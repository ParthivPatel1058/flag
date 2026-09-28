"""PLAG's voice on Fish Audio: a Jarvis voice from Fish Audio's voice library (or any voice model you pick).

POST https://api.fish.audio/v1/tts, Authorization: Bearer <key>, header model: s1,
{text, reference_id, format, mp3_bitrate, latency, prosody: {speed}} -> the audio (MP3).
The voice: Settings -> Voice -> Fish Audio voice ID (the id in a fish.audio voice page's address). Left empty, PLAG
searches Fish Audio's library for "Jarvis" the first time it speaks, takes the most used English voice, and remembers
its id in Settings (so it's looked up once). Every phrase is cached on disk: a repeated reply costs no credits.

Key: Windows Credential Manager, PLAG / fishaudio_api_key (saved from Settings once Fish Audio accepts it, or with
keyring by hand). Never written to a file, never logged.
"""

import hashlib
import logging
import time

import httpx

from . import settings
from .config import TTS_CACHE_DIR
from .hinglish import for_voice, is_hindi
from .secrets import delete_secret, get_secret, set_secret

API = "https://api.fish.audio"
KEY = "fishaudio_api_key"
MODEL = "s1"  # Fish Audio's newest speech model
MAX_CHARS = 1500
CACHE = TTS_CACHE_DIR / "fish"
SEARCH = "jarvis"
log = logging.getLogger("plag.fish")
# a calmer, more deliberate delivery for serious news; a touch quicker when cheerful (Jarvis stays composed)
SPEED = {"calm": 1.0, "cheerful": 1.03, "excited": 1.05, "serious": 0.97, "sorry": 0.96, "curious": 1.0}


class FishError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class FishAudio:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        self._rest_until = 0.0
        self.last_error: str | None = None
        self.health: dict | None = None
        self.voice_name: str = ""

    @staticmethod
    def configured() -> bool:
        return bool(get_secret(KEY))

    def usable(self) -> bool:
        return self.configured() and time.time() >= self._rest_until

    def _failed(self, code: str, rest: float) -> None:
        self.last_error = code
        self._rest_until = time.time() + rest

    @staticmethod
    def _headers(key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}

    async def find_voice(self, key: str, query: str = SEARCH) -> tuple[str, str]:
        """The most used public voice model matching `query` (English first): (id, title)."""
        try:
            r = await self._http.get(f"{API}/model", headers=self._headers(key),
                                     params={"title": query, "page_size": 20, "sort_by": "task_count"})
        except httpx.HTTPError as e:
            raise FishError("Fish Audio can't be reached.", "offline") from e
        if r.status_code in (401, 403):
            raise FishError("Fish Audio didn't accept the key.", "bad_key")
        if r.status_code != 200:
            raise FishError(f"Fish Audio's voice search failed ({r.status_code}).", str(r.status_code))
        items = [m for m in (r.json().get("items") or []) if m.get("_id") and m.get("state", "trained") == "trained"]
        if not items:
            raise FishError(f"No “{query}” voice found on Fish Audio. Paste a voice ID in Settings.", "no_voice")
        english = [m for m in items if "en" in (m.get("languages") or [])] or items
        best = max(english, key=lambda m: (m.get("task_count") or 0, m.get("like_count") or 0))
        return best["_id"], str(best.get("title") or query)

    async def _voice(self, key: str) -> str:
        s = settings.get()
        if s.get("fish_voice_id"):
            return s["fish_voice_id"]
        vid, title = await self.find_voice(key)
        settings.update({"fish_voice_id": vid})
        self.voice_name = title
        log.info("Fish Audio voice: %s (%s)", title, vid)
        return vid

    async def speak(self, text: str, mood: str = "calm") -> bytes:
        """MP3 bytes of `text` in the Jarvis voice (or the voice chosen in Settings)."""
        key = get_secret(KEY)
        if not key or not self.usable():
            raise FishError("Fish Audio is resting or has no key.", "unavailable")
        try:
            voice = await self._voice(key)
        except FishError as e:
            self._failed(e.code, 3600 if e.code in ("bad_key", "no_voice") else 60)
            raise
        said = (for_voice(text) if is_hindi(text) else text)[:MAX_CHARS]
        speed = round(min(1.5, max(0.7, float(settings.get()["speed"]) * SPEED.get(mood, 1.0))), 2)
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / (hashlib.sha1(f"{voice}|{speed}|{said}".encode()).hexdigest()[:20] + ".mp3")
        if path.exists():
            return path.read_bytes()
        t0 = time.perf_counter()
        try:
            r = await self._http.post(f"{API}/v1/tts", headers={**self._headers(key), "model": MODEL}, json={
                "text": said, "reference_id": voice, "format": "mp3", "mp3_bitrate": 128, "latency": "balanced",
                "normalize": True, "prosody": {"speed": speed, "volume": 0}})
        except httpx.HTTPError as e:
            self._failed("offline", 30)
            raise FishError("Fish Audio can't be reached.", "offline") from e
        if r.status_code != 200 or not r.content or r.headers.get("content-type", "").startswith("application/json"):
            code = {401: "bad_key", 403: "bad_key", 402: "no_credits", 429: "busy"}.get(r.status_code, str(r.status_code))
            if r.status_code in (400, 404) and "reference" in r.text.lower():
                settings.update({"fish_voice_id": ""})  # that voice model is gone: look it up again next time
                code = "no_voice"
            self._failed(code, 3600 if code in ("bad_key", "no_credits") else 60 if code == "busy" else 30)
            raise FishError(f"Fish Audio voice error ({code}).", code)
        path.write_bytes(r.content)
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time()}
        self.last_error = None
        return r.content

    async def save_key(self, key: str) -> dict:
        """Check the key (it must be able to find a voice) before keeping it."""
        key = key.strip()
        vid, title = await self.find_voice(key)  # also proves the key works
        set_secret(KEY, key)
        if not settings.get().get("fish_voice_id"):
            settings.update({"fish_voice_id": vid})
        self.voice_name = title or self.voice_name
        self._rest_until, self.last_error = 0.0, None
        return self.status()

    def remove_key(self) -> None:
        delete_secret(KEY)
        self.last_error = None

    def status(self) -> dict:
        return {"configured": self.configured(), "usable": self.usable(), "error": self.last_error,
                "voice_id": settings.get().get("fish_voice_id", ""), "voice_name": self.voice_name}


fishaudio = FishAudio()
