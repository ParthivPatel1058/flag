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


groq = Groq()
