"""Gemini provider: one JSON 'turn' call (audio or text in) and speech synthesis, with failover.

Thin REST adapter over the Gemini Developer API. Every model in a route is tried in order;
a model that returns 429/5xx or times out is skipped for a while (circuit breaker).
"""

import base64
import hashlib
import io
import json
import time
import wave

import httpx

from .config import BREAKER_SECONDS, MODELS, ROUTE_BUDGET, ROUTE_TIMEOUT, THINKING, TTS_CACHE_DIR, VOICE
from .secrets import get_secret

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class ProviderError(Exception):
    def __init__(self, message: str, code: str | int | None = None, tried: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.tried = tried or []


class Gemini:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=8.0))
        self._skip_until: dict[str, float] = {}
        self.health: dict[str, dict] = {}  # model -> {ok, ms, at, error}

    async def close(self) -> None:
        await self._http.aclose()

    def _key(self) -> str:
        key = get_secret("gemini_api_key")
        if not key:
            raise ProviderError("No Gemini key in Windows Credential Manager (PLAG / gemini_api_key).", "no_key")
        return key

    async def _post(self, model: str, body: dict, timeout: float) -> tuple[dict, int]:
        t0 = time.perf_counter()
        try:
            r = await self._http.post(f"{BASE}/{model}:generateContent", headers={"x-goog-api-key": self._key()},
                                      json=body, timeout=httpx.Timeout(timeout, connect=8.0))
        except httpx.TimeoutException as e:
            self._mark(model, False, t0, "timeout")
            raise ProviderError("timeout", "timeout") from e
        except httpx.TransportError as e:
            self._mark(model, False, t0, "offline")
            raise ProviderError("network unreachable", "offline") from e
        data = r.json() if r.content else {}
        if r.status_code != 200 or "error" in data:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            code = err.get("code", r.status_code)
            quotas = [v.get("quotaId", "") for d in err.get("details", []) for v in d.get("violations", [])]
            if code == 429 and any("PerDay" in q for q in quotas):
                code = "quota_day"  # the free tier's daily cap: don't retry this model today
            self._mark(model, False, t0, str(code))
            raise ProviderError(err.get("message", f"HTTP {r.status_code}")[:240], code)
        ms = self._mark(model, True, t0)
        return data, ms

    def _mark(self, model: str, ok: bool, t0: float, error: str | None = None) -> int:
        ms = int((time.perf_counter() - t0) * 1000)
        self.health[model] = {"ok": ok, "ms": ms, "at": time.time(), "error": error}
        return ms

    @staticmethod
    def _has_audio(body: dict) -> bool:
        return any("inlineData" in p and p["inlineData"].get("mimeType", "").startswith("audio")
                   for c in body.get("contents", []) for p in c.get("parts", []))

    @staticmethod
    def _for_model(model: str, body: dict) -> dict:
        """Adapt one request to each model: its fastest thinking level, and Gemma's plainer JSON mode."""
        gc = dict(body.get("generationConfig") or {})
        gc.pop("thinkingConfig", None)
        if model in THINKING and "thinkingConfig" in (body.get("generationConfig") or {}):
            gc["thinkingConfig"] = {"thinkingLevel": THINKING[model]}
        out = {**body, "generationConfig": gc}
        if model.startswith("gemma") and "responseSchema" in gc:
            # Gemma ignores response schemas (returns empty actions); asking in the prompt works. The keys come from
            # the schema asked for (until 2026-09-24 they were always the command keys, so Gemma could never help
            # with answers, briefs or writing).
            keys = ", ".join((gc.pop("responseSchema").get("properties") or {}).keys())
            sys_text = body.get("systemInstruction", {}).get("parts", [{}])[0].get("text", "")
            out["systemInstruction"] = {"parts": [{"text": sys_text + f"\n\nOutput ONLY one JSON object with the keys "
                                                   f"{keys}. No markdown."}]}
        return out

    async def _first_ok(self, route: str, body: dict, models: list[str] | None = None) -> tuple[str, dict, int]:
        tried: list[str] = []
        deadline = time.monotonic() + ROUTE_BUDGET.get(route, 40.0)
        candidates = list(models or MODELS[route])
        now = time.time()
        # models that failed recently go last instead of being skipped, so there is always a real attempt
        order = [m for m in candidates if self._skip_until.get(m, 0) <= now] + \
                [m for m in candidates if self._skip_until.get(m, 0) > now]
        for model in order:
            left = deadline - time.monotonic()
            if left < 2.0:
                tried.append(f"{model}: out of time")
                break
            if model.startswith("gemma") and self._has_audio(body):
                continue  # Gemma can't listen to audio
            try:
                data, ms = await self._post(model, self._for_model(model, body), min(ROUTE_TIMEOUT.get(route, 40.0), left))
                return model, data, ms
            except ProviderError as e:
                tried.append(f"{model}: {e.code}")
                if e.code in ("no_key", "offline"):
                    raise ProviderError(str(e), e.code, tried) from e
                if e.code == "quota_day":
                    self._skip_until[model] = time.time() + 6 * 3600
                elif e.code == 429:
                    self._skip_until[model] = time.time() + BREAKER_SECONDS["rate"]
                elif e.code == "timeout":
                    self._skip_until[model] = time.time() + BREAKER_SECONDS["timeout"]
                elif isinstance(e.code, int) and e.code >= 500:
                    self._skip_until[model] = time.time() + BREAKER_SECONDS["server"]
        raise ProviderError("All models in route failed", "unavailable", tried)

    async def turn(self, *, system: str, schema: dict, history: list[tuple[str, str]],
                   audio_wav: bytes | None = None, text: str | None = None,
                   models: list[str] | None = None, route: str = "turn") -> tuple[str, dict, int]:
        contents = [{"role": role, "parts": [{"text": t}]} for role, t in history]
        if audio_wav is not None:
            part = {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(audio_wav).decode()}}
        else:
            part = {"text": text or ""}
        contents.append({"role": "user", "parts": [part]})
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            # minimal thinking: commands need a fast answer, not deliberation
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0.3,
                                 "thinkingConfig": {"thinkingLevel": "minimal"}},
        }
        model, data, ms = await self._first_ok(route, body, models or MODELS["turn"])
        try:
            parts = data["candidates"][0]["content"]["parts"]
            raw = "".join(p.get("text", "") for p in parts)
            if model.startswith("gemma"):  # plain JSON mode may wrap the object in a code fence
                raw = raw[raw.find("{"):raw.rfind("}") + 1] or raw
            return model, json.loads(raw), ms
        except (KeyError, IndexError, ValueError) as e:
            raise ProviderError("Model returned an unreadable answer", "bad_output") from e

    async def look(self, *, jpeg: bytes, system: str, schema: dict, question: str) -> tuple[str, dict, int]:
        """Camera: one image plus the user's question in, JSON out."""
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [
                {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                {"text": question},
            ]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": 0.2,
                                 "thinkingConfig": {"thinkingLevel": "minimal"}},
        }
        model, data, ms = await self._first_ok("vision", body)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return model, json.loads("".join(p.get("text", "") for p in parts)), ms
        except (KeyError, IndexError, ValueError) as e:
            raise ProviderError("Model returned an unreadable answer", "bad_output") from e

    async def speak(self, text: str, voice: str = VOICE, mood: str = "calm") -> tuple[bytes, bool]:
        """Return (wav_bytes, from_cache). `mood` shapes the delivery, like a human would."""
        key = hashlib.sha1(f"{voice}|{mood}|{text}".encode()).hexdigest()
        cached = TTS_CACHE_DIR / f"{key}.wav"
        if cached.exists():
            return cached.read_bytes(), True
        style = {
            "cheerful": "warmly, with a smile in your voice",
            "excited": "with bright, genuine excitement",
            "calm": "calmly and confidently, like a trusted assistant",
            "serious": "in a focused, serious tone",
            "sorry": "gently and apologetically",
            "curious": "with light curiosity",
        }.get(mood, "calmly and confidently, like a trusted assistant")
        body = {
            "contents": [{"parts": [{"text": f"Say naturally, {style}: {text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        _, data, _ = await self._first_ok("tts", body)
        try:
            inline = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        except (KeyError, IndexError) as e:
            raise ProviderError("No audio in response", "bad_output") from e
        pcm = base64.b64decode(inline["data"])
        rate = 24000
        for piece in inline.get("mimeType", "").split(";"):
            if piece.strip().startswith("rate="):
                rate = int(piece.split("=")[1])
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        wav = buf.getvalue()
        cached.write_bytes(wav)
        return wav, False


gemini = Gemini()
