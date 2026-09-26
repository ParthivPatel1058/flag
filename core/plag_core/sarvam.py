"""PLAG's voice on Sarvam AI (Bulbul v3): Indian voices made for Hindi, English and the mix of the two.

POST https://api.sarvam.ai/text-to-speech, header api-subscription-key (docs.sarvam.ai, checked 2026-09-25):
{text, language_code, model, speaker, pace, speech_sample_rate, output_audio_codec} -> {"audios": [base64 WAV]}.
There's no streaming, so the dashboard voices a reply phrase by phrase and makes the next phrase while one plays.
Every phrase is cached on disk, so a repeated reply ("Yes sir?") costs nothing.

Key: Windows Credential Manager, PLAG / sarvam_api_key, saved from Settings once Sarvam accepts it. It's never
shown again, never written to a file, and never logged.
"""

import base64
import hashlib
import io
import time
import wave

import httpx

from . import settings
from .config import TTS_CACHE_DIR
from .hinglish import for_voice, is_hindi
from .secrets import delete_secret, get_secret, set_secret

API = "https://api.sarvam.ai/text-to-speech"
MODEL = "bulbul:v3"
KEY = "sarvam_api_key"
RATE = 24000
MAX_CHARS = 2500  # Bulbul v3's limit per request
CACHE = TTS_CACHE_DIR / "sarvam"
# How each mood sounds (2026-09-25: "react with more joy… with more energy"): Bulbul v3's pace, and its temperature,
# which is how expressive the voice is (0.01-2.0, default 0.6)
# 2026-09-26: the user wants a professional male voice, not an excitable one: small, steady variations only (a fast
# pace also raised the pitch and made the male voice sound lighter)
PACE = {"calm": 1.0, "cheerful": 1.02, "excited": 1.04, "serious": 0.97, "sorry": 0.96, "curious": 1.0}
TEMPERATURE = {"calm": 0.5, "cheerful": 0.6, "excited": 0.7, "serious": 0.45, "sorry": 0.5, "curious": 0.55}


class SarvamError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _join_wavs(parts: list[bytes]) -> bytes:
    """Sarvam may answer a long text in several WAVs: one WAV, same format."""
    if len(parts) == 1:
        return parts[0]
    frames, params = [], None
    for p in parts:
        with wave.open(io.BytesIO(p)) as w:
            params = params or w.getparams()
            frames.append(w.readframes(w.getnframes()))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))
    return out.getvalue()


class Sarvam:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._rest_until = 0.0
        self.last_error: str | None = None
        self.health: dict | None = None

    @staticmethod
    def configured() -> bool:
        return bool(get_secret(KEY))

    def usable(self) -> bool:
        return self.configured() and time.time() >= self._rest_until

    def _failed(self, code: str, rest: float) -> None:
        self.last_error = code
        self._rest_until = time.time() + rest

    @staticmethod
    def _body(text: str, mood: str) -> dict:
        s = settings.get()
        hindi = is_hindi(text)
        pace = min(2.0, max(0.5, float(s["speed"]) * PACE.get(mood, 1.0)))
        return {"text": (for_voice(text) if hindi else text)[:MAX_CHARS], "language_code": "hi-IN" if hindi else "en-IN",
                "model": MODEL, "speaker": s["sarvam_speaker"], "pace": round(pace, 2),
                "temperature": TEMPERATURE.get(mood, 0.6), "speech_sample_rate": RATE, "output_audio_codec": "wav"}

    async def _request(self, body: dict, key: str) -> httpx.Response:
        return await self._http.post(API, headers={"api-subscription-key": key}, json=body)

    async def speak(self, text: str, mood: str = "calm") -> bytes:
        """WAV bytes of `text` in the chosen Sarvam voice."""
        key = get_secret(KEY)
        if not key or not self.usable():
            raise SarvamError("Sarvam is resting or has no key.", "unavailable")
        body = self._body(text, mood)
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / (hashlib.sha1(f"{body['speaker']}|{body['language_code']}|{body['pace']}|{body['temperature']}|"
                                     f"{body['text']}".encode()).hexdigest()[:20] + ".wav")
        if path.exists():
            return path.read_bytes()
        t0 = time.perf_counter()
        try:
            r = await self._request(body, key)
        except httpx.HTTPError as e:
            self._failed("offline", 30)
            raise SarvamError("Sarvam can't be reached.", "offline") from e
        if r.status_code != 200:
            code = {401: "bad_key", 403: "bad_key", 402: "no_credits", 429: "busy"}.get(r.status_code, str(r.status_code))
            self._failed(code, 3600 if code in ("bad_key", "no_credits") else 60 if code == "busy" else 30)
            raise SarvamError(f"Sarvam voice error ({code}).", code)
        try:
            wav = _join_wavs([base64.b64decode(a) for a in r.json().get("audios") or []])
        except (ValueError, wave.Error) as e:
            self._failed("bad_output", 30)
            raise SarvamError("Sarvam sent audio PLAG can't play.", "bad_output") from e
        path.write_bytes(wav)
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time()}
        self.last_error = None
        return wav

    async def save_key(self, key: str) -> dict:
        """Check the key with one short phrase before keeping it (Sarvam has no free "who am I" call)."""
        key = key.strip()
        try:
            r = await self._request({**self._body("Namaste.", "calm"), "text": "Namaste."}, key)
        except httpx.HTTPError as e:
            raise SarvamError("Sarvam can't be reached right now. Check the internet and try again.", "offline") from e
        if r.status_code in (401, 403):
            raise SarvamError("Sarvam didn't accept that key. Copy it again from dashboard.sarvam.ai → API keys.", "bad_key")
        if r.status_code != 200:
            raise SarvamError(f"Sarvam answered with an error ({r.status_code}). Try again in a minute.", str(r.status_code))
        set_secret(KEY, key)
        self._rest_until, self.last_error = 0.0, None
        return self.status()

    def remove_key(self) -> None:
        delete_secret(KEY)
        self.last_error = None

    def status(self) -> dict:
        return {"configured": self.configured(), "usable": self.usable(), "error": self.last_error,
                "speaker": settings.get()["sarvam_speaker"], "speakers": list(settings.SARVAM_SPEAKERS)}


sarvam = Sarvam()
