"""Groq: open-source models (Llama 3.3 70B) on Groq's LPU chips, typically answering in well under a second. Raced
against Gemini and Gemma: whichever answers first wins, so with a Groq key PLAG's own answers get much faster.

Key: Windows Credential Manager, PLAG / groq_api_key (free at console.groq.com). Without it, nothing changes.
OpenAI-compatible API with JSON mode. After two failures in a row it rests for five minutes.
"""

import json
import re
import time

import httpx

from .gemini import ProviderError
from .secrets import get_secret

URL = "https://api.groq.com/openai/v1/chat/completions"
STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
STT_MODEL = "whisper-large-v3"  # Groq's most accurate Whisper (multilingual: English, Hindi, Hinglish)
# spellings Whisper should know (it takes a short style prompt)
STT_PROMPT = "PLAG, WhatsApp, YouTube, Gmail, LinkedIn, Instagram, Cal.com. Hinglish: bhej do, kholo, chalao, yaad dilana."
MODEL = "llama-3.3-70b-versatile"


def _json_from(content: str) -> dict:
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError("Groq returned no JSON", "bad_output")
    return json.loads(content[start:end + 1])


class Groq:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0))
        self.health: dict | None = None
        self._fails = 0
        self._rest_until = 0.0

    @staticmethod
    def key() -> str | None:
        return get_secret("groq_api_key")

    def ready(self) -> bool:
        return bool(self.key()) and time.time() >= self._rest_until

    def _failed(self, why: str, t0: float) -> None:
        self.health = {"ok": False, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time(), "error": why}
        self._fails += 1
        if self._fails >= 2:
            self._rest_until = time.time() + 300

    async def turn(self, *, system: str, history: list[tuple[str, str]], text: str, schema: dict | None = None,
                   max_tokens: int = 600) -> tuple[str, dict, int]:
        """One JSON answer with the keys of `schema` (the same shape Gemini returns)."""
        key = self.key()
        if not key or not self.ready():
            raise ProviderError("Groq is resting or has no key", "unavailable")
        keys = ", ".join(((schema or {}).get("properties") or {}).keys()) or "transcript, language, actions, reply, mood"
        messages = [{"role": "system", "content": f"{system}\n\nOutput ONLY one JSON object with the keys {keys}."}]
        messages += [{"role": "assistant" if role == "model" else "user", "content": t} for role, t in history]
        messages.append({"role": "user", "content": text})
        t0 = time.perf_counter()
        try:
            r = await self._http.post(URL, headers={"Authorization": f"Bearer {key}"}, json={
                "model": MODEL, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"}})
        except httpx.TimeoutException as e:
            self._failed("timeout", t0)
            raise ProviderError("Groq timed out", "timeout") from e
        except httpx.TransportError as e:
            self._failed("offline", t0)
            raise ProviderError("Groq unreachable", "offline") from e
        if r.status_code != 200:
            self._failed(str(r.status_code), t0)
            raise ProviderError(f"Groq HTTP {r.status_code}", r.status_code)
        try:
            obj = _json_from(r.json()["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, ValueError) as e:
            self._failed("bad_output", t0)
            raise ProviderError("Groq answer unreadable", "bad_output") from e
        ms = int((time.perf_counter() - t0) * 1000)
        self.health = {"ok": True, "ms": ms, "at": time.time(), "error": None}
        self._fails = 0
        return f"groq {MODEL}", obj, ms


    async def check_key(self, key: str) -> None:
        """Prove a key works before it's saved (one free call to the model list). Raises ProviderError."""
        key = key.strip()
        if not key.startswith("gsk_"):
            raise ProviderError("A Groq key starts with gsk_ (console.groq.com → API keys).", "bad_key")
        try:
            r = await self._http.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            raise ProviderError("Groq can't be reached right now. Check the internet and try again.", "offline") from e
        if r.status_code in (401, 403):
            raise ProviderError("Groq didn't accept that key. Copy it again from console.groq.com → API keys.", "bad_key")
        if r.status_code != 200:
            raise ProviderError(f"Groq answered with an error ({r.status_code}). Try again in a minute.", str(r.status_code))
        self._fails, self._rest_until = 0, 0.0  # a fresh key starts with a clean slate

    async def transcribe(self, wav: bytes, language: str | None = None) -> str:
        """What was said in `wav`, by Whisper Large V3 on Groq (Hindi comes back as Hinglish in Latin letters).
        Raises ProviderError; the caller then tries the next hearing."""
        key = self.key()
        if not key or not self.ready():
            raise ProviderError("Groq is resting or has no key", "unavailable")
        data = {"model": STT_MODEL, "response_format": "json", "temperature": "0", "prompt": STT_PROMPT}
        if language in ("en", "hi"):
            data["language"] = language
        t0 = time.perf_counter()
        try:
            r = await self._http.post(STT_URL, headers={"Authorization": f"Bearer {key}"}, data=data,
                                      files={"file": ("speech.wav", wav, "audio/wav")})
        except httpx.HTTPError as e:
            self._failed("offline", t0)
            raise ProviderError("Groq hearing unreachable", "offline") from e
        if r.status_code != 200:
            self._failed(str(r.status_code), t0)
            raise ProviderError(f"Groq hearing HTTP {r.status_code}", r.status_code)
        from .hinglish import to_latin
        try:
            said = (r.json().get("text") or "").strip()
        except ValueError as e:  # the caller can then try the next hearing
            self._failed("bad_output", t0)
            raise ProviderError("Groq hearing answer unreadable", "bad_output") from e
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time(), "error": None}
        self._fails = 0  # hearing and answering share the counter: a success clears it, as turn() does
        return to_latin(said)


groq = Groq()
