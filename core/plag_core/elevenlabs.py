"""ElevenLabs, on your account: PLAG's voice (text-to-speech) and hearing (Scribe speech-to-text).

Spending your credits carefully is part of the design:
- the wake word is never sent here: it's recognised on this laptop, free, and never-ending room audio stays local;
- only the command you say after "PLAG" is transcribed, and simple ones ("open YouTube", "stop") stay on-device;
- speech uses Flash v2.5 by default (fastest, and half the credits of the premium models), every phrase is cached on
  disk so a repeated reply ("Yes sir?", "On it.", "YouTube is open.") is paid for once, and long replies use the
  free local voice;
- PLAG reads your remaining quota from ElevenLabs and switches to the local voice before it runs out.

Faster, when your key is set:
- realtime hearing (Scribe v2 Realtime): while you speak, the dashboard streams the microphone through the core to
  ElevenLabs and the words come back as you say them; the final text is ready the moment you stop, instead of
  uploading and transcribing afterwards. Only the command window is streamed (at most 20 s), never the wake listening.
- streamed speech: PLAG starts talking as the first audio arrives instead of waiting for the whole reply.

Key: Windows Credential Manager, PLAG / elevenlabs_api_key. Docs checked 2026-09-24: POST /v1/text-to-speech/{voice}
(xi-api-key, output_format=wav_24000) and /v1/text-to-speech/{voice}/stream (pcm_24000, chunked), POST
/v1/speech-to-text (model scribe_v2), WSS /v1/speech-to-text/realtime (scribe_v2_realtime, pcm_16000, manual commit:
input_audio_chunk up; session_started, partial_transcript, committed_transcript down), GET /v1/user/subscription.
PLAG_ELEVEN_API overrides the address (the tests point it at a local stand-in).
"""

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import time
import wave
from pathlib import Path
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import InvalidStatus, WebSocketException

from . import settings
from .config import DATA_DIR, TTS_CACHE_DIR

# The month's credits ran out: remembered across restarts, so PLAG doesn't try ElevenLabs (and wait for it to fail)
# on every wake until it's worth checking again. A key without "user_read" can't ask how many credits are left.
STATE = DATA_DIR / "elevenlabs_state.json"
RECHECK_S = 6 * 3600
from .secrets import delete_secret, get_secret, set_secret

API = os.environ.get("PLAG_ELEVEN_API", "https://api.elevenlabs.io")
WS_API = "ws" + API.removeprefix("http")  # https -> wss, http -> ws
STT_MODEL = "scribe_v2"
REALTIME_MODEL = "scribe_v2_realtime"
RATE_OUT = 24000  # streamed speech: 16-bit mono PCM at 24 kHz
# what the realtime service sends when something went wrong (docs, 2026-09-24)
REALTIME_ERRORS = {"error", "auth_error", "quota_exceeded", "commit_throttled", "unaccepted_terms", "rate_limited",
                   "queue_overflow", "resource_exhausted", "session_time_limit_exceeded", "input_error",
                   "invalid_request", "chunk_size_exceeded", "insufficient_audio_activity", "transcriber_error"}
# how each mood sounds: lower stability = more expressive; style adds the voice's own character
DELIVERY = {"calm": (0.5, 0.15), "cheerful": (0.4, 0.35), "excited": (0.3, 0.5), "serious": (0.65, 0.05),
            "sorry": (0.55, 0.2), "curious": (0.45, 0.3)}
# The reply's mood (the AI picks one per answer) shapes delivery on every model through the voice settings above; the
# Expressive model (v3) also reads an audio tag per mood. Calm replies stay untagged: the voice's own character.
TAGS = {"excited": "[excited] ", "curious": "[curious] ", "cheerful": "[happily] ", "sorry": "[softly] ",
        "serious": "[seriously] "}


class ElevenError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def wav_of(pcm: bytes, rate: int = RATE_OUT) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm[: len(pcm) // 2 * 2])
    return buf.getvalue()


class Realtime:
    """One live transcription: your audio goes up in small chunks while you speak, partial words come back, and the
    final text arrives right after the commit that ends the phrase."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.seconds = 0.0  # audio sent, for the audit log

    async def send(self, pcm: bytes, commit: bool = False) -> None:
        self.seconds += len(pcm) / 32000
        await self.ws.send(json.dumps({"message_type": "input_audio_chunk", "audio_base_64": base64.b64encode(pcm).decode(),
                                       "commit": commit, "sample_rate": 16000}))

    async def recv(self) -> dict:
        return json.loads(await self.ws.recv())

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.ws.close()


class ElevenLabs:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(base_url=API, timeout=httpx.Timeout(20.0, connect=6.0))  # one warm connection
        self._usage: dict | None = None
        self._usage_at = 0.0
        self._rest_until = 0.0
        self._voices: list[dict] | None = None
        self.last_error: str | None = None
        self.health: dict | None = None  # last call: {ok, ms, what}
        self.no_usage = False  # the key may not read the account (no "user_read"): credits can't be shown
        # Voices ElevenLabs won't voice for this plan over the API (2026-09-24: "Free users cannot use library voices
        # via the API", e.g. the agent's Amit Gupta). PLAG speaks with a built-in voice instead, without pausing.
        self.paid_only: set[str] = set()
        self._agent_id = ""  # your ElevenLabs agent, found once by name
        self._agent_name = ""
        try:
            self._quota_out_at = float(json.loads(STATE.read_text(encoding="utf-8")).get("quota_out_at") or 0)
        except (OSError, ValueError, AttributeError):
            self._quota_out_at = 0.0

    def _out_of_credits(self, out: bool) -> None:
        """Remember (or forget) that the month's credits are used up."""
        if out == bool(self._quota_out_at):
            return
        self._quota_out_at = time.time() if out else 0.0
        with contextlib.suppress(OSError):
            STATE.write_text(json.dumps({"quota_out_at": self._quota_out_at}), encoding="utf-8")

    # ------------------------------------------------------------ setup

    @staticmethod
    def key() -> str | None:
        return get_secret("elevenlabs_api_key")

    def configured(self) -> bool:
        return bool(self.key())

    async def save_key(self, key: str) -> dict:
        """Check the key against your account before keeping it."""
        key = key.strip()
        try:
            r = await self._http.get("/v1/user/subscription", headers={"xi-api-key": key})
            if r.status_code == 401 and "missing_permissions" in r.text:
                # A key limited to some features (2026-09-24: the user's key has voices, speech and agents, but not
                # "user_read"): check it can list your voices instead. The credit count just can't be shown.
                r = await self._http.get("/v1/voices", headers={"xi-api-key": key})
                if r.status_code == 200:
                    set_secret("elevenlabs_api_key", key)
                    self._voices, self._rest_until, self.last_error, self._usage = None, 0.0, None, None
                    self.no_usage = True
                    return await self.status()
        except httpx.HTTPError as e:
            raise ElevenError("ElevenLabs can't be reached right now.", "offline") from e
        if r.status_code == 401:
            raise ElevenError("ElevenLabs didn't accept that key.", "bad_key")
        if "api_key_id_used_as_api_key" in r.text:  # 2026-09-24: the key's ID was pasted instead of the key
            raise ElevenError("That's the key's ID, not the key itself. In ElevenLabs, create a new API key and copy the "
                              "value that starts with sk_ (it's shown only once).", "key_id")
        if r.status_code != 200:
            raise ElevenError(f"ElevenLabs answered with an error ({r.status_code}).", str(r.status_code))
        set_secret("elevenlabs_api_key", key)
        self._voices, self._rest_until, self.last_error = None, 0.0, None
        self._keep_usage(r.json())
        return await self.status()

    def forget_key(self) -> None:
        delete_secret("elevenlabs_api_key")
        self._voices = self._usage = None

    def _headers(self) -> dict:
        key = self.key()
        if not key:
            raise ElevenError("No ElevenLabs key saved.", "no_key")
        return {"xi-api-key": key}

    def _keep_usage(self, data: dict) -> None:
        self._usage = {"used": int(data.get("character_count") or 0), "limit": int(data.get("character_limit") or 0),
                       "reset": int(data.get("next_character_count_reset_unix") or 0)}
        self._usage_at = time.time()

    async def usage(self, fresh: bool = False) -> dict | None:
        if self.configured() and not self.no_usage and (fresh or not self._usage or time.time() - self._usage_at > 300):
            try:
                r = await self._http.get("/v1/user/subscription", headers=self._headers())
                if r.status_code == 200:
                    self._keep_usage(r.json())
                elif r.status_code == 401 and "missing_permissions" in r.text:
                    self.no_usage = True  # a key without "user_read": fine, just no credit count
            except (httpx.HTTPError, ElevenError):
                pass
        return self._usage

    def _budget_ok(self, cost: int = 0) -> bool:
        if self._quota_out_at and time.time() - self._quota_out_at < RECHECK_S:
            return False  # out of credits a moment ago (or before a restart): look again in a few hours
        u = self._usage
        if not u or not u["limit"]:
            return True  # unknown yet: the first call fetches it
        reserve = u["limit"] * settings.get()["eleven_reserve_pct"] / 100
        return u["limit"] - u["used"] - cost > reserve

    def usable(self, cost: int = 0) -> bool:
        return self.configured() and time.time() >= self._rest_until and self._budget_ok(cost)

    def _failed(self, code: str, rest: float) -> None:
        self.last_error = code
        self._rest_until = time.time() + rest

    def _refused(self, status: int, body: str) -> str:
        """Rest after a refused request and return its code. Out of credits also marks the month as used up, so the
        dashboard says so even for a key that can't read the credit count (2026-09-24: 0 of 10,000 left)."""
        if "quota_exceeded" in body:
            limit = int(m[1]) if (m := re.search(r"quota of (\d+)", body)) else 0
            self._usage, self._usage_at = {"used": limit, "limit": limit, "reset": 0}, time.time()
            self._failed("quota_exceeded", 3600)
            self._out_of_credits(True)
            return "quota_exceeded"
        self._failed(str(status), 3600 if status in (401, 402) else 60)  # a bad key or no credits: stop trying
        return str(status)

    async def voices(self) -> list[dict]:
        if self._voices is None:
            r = await self._http.get("/v1/voices", headers=self._headers())
            if r.status_code != 200:
                raise ElevenError(f"Couldn't list your voices ({r.status_code}).", str(r.status_code))
            self._voices = [{"id": v["voice_id"], "name": v.get("name", ""), "labels": v.get("labels") or {},
                             "category": v.get("category", "")} for v in r.json().get("voices", [])]
        return self._voices

    async def _voice_id(self) -> str:
        chosen = settings.get()["eleven_voice_id"]
        if chosen and chosen not in self.paid_only:
            return chosen
        voices = await self.voices()
        if not voices:
            raise ElevenError("Your ElevenLabs account has no voices.", "no_voice")
        # a built-in (premade) voice works on every plan; George first, a warm male voice close to PLAG's own
        premade = [v for v in voices if v["category"] == "premade" and v["id"] not in self.paid_only]
        pick = next((v for v in premade if v["name"].startswith("George")), premade[0] if premade else voices[0])
        return pick["id"]

    def _paid_plan_voice(self, voice: str, body: str) -> bool:
        """A 402 because this voice needs a paid plan: remember it and use a built-in voice (no pause)."""
        if "paid_plan_required" in body:
            self.paid_only.add(voice)
            self.last_error = "voice_needs_paid_plan"
            return True
        return False

    async def status(self) -> dict:
        s = settings.get()
        u = await self.usage() if self.configured() else None
        voice_name = ""
        if self.configured():
            try:
                vid = await self._voice_id()
                voice_name = next((v["name"] for v in await self.voices() if v["id"] == vid), "")
            except (ElevenError, httpx.HTTPError):
                pass
        return {"configured": self.configured(), "usable": self.usable(), "error": self.last_error, "usage": u,
                "voice": voice_name, "model": s["eleven_model"], "speak": s["eleven_speak"], "hear": s["eleven_hear"]}

    # ------------------------------------------------------------ speaking

    def cache_path(self, text: str, mood: str, voice: str, model: str):
        key = hashlib.sha1(f"eleven|{voice}|{model}|{mood}|{settings.get()['speed']}|{text}".encode()).hexdigest()
        return TTS_CACHE_DIR / f"el_{key}.wav"

    @staticmethod
    def _body(text: str, mood: str, model: str) -> dict:
        stability, style = DELIVERY.get(mood, DELIVERY["calm"])
        body = {"text": (TAGS.get(mood, "") if model == "eleven_v3_conversational" else "") + text, "model_id": model,
                "voice_settings": {"stability": stability, "similarity_boost": 0.75, "style": style,
                                   "use_speaker_boost": True, "speed": settings.get()["speed"]}}
        if re.search(r"[ऀ-ॿ]", text):
            body["language_code"] = "hi"
        return body

    async def open_stream(self, text: str, mood: str = "calm") -> tuple[bytes | None, httpx.Response | None, Path]:
        """Your voice, streamed: (wav, None, path) when this exact reply was said before (free), otherwise
        (None, response, path) with the response still open; its body is 24 kHz PCM arriving while ElevenLabs speaks."""
        s = settings.get()
        voice, model = await self._voice_id(), s["eleven_model"]
        cached = self.cache_path(text, mood, voice, model)
        if cached.exists():
            return cached.read_bytes(), None, cached
        if not self.usable(len(text)):
            raise ElevenError("ElevenLabs is resting or the monthly credits are nearly used.", "budget")
        for _ in range(2):  # a second try only after a voice turned out to need a paid plan
            req = self._http.build_request("POST", f"/v1/text-to-speech/{voice}/stream", params={"output_format": "pcm_24000"},
                                           json=self._body(text, mood, model), headers=self._headers())
            try:
                r = await self._http.send(req, stream=True)
            except httpx.HTTPError as e:
                self._failed("offline", 30)
                raise ElevenError("ElevenLabs can't be reached.", "offline") from e
            if r.status_code == 402:
                body = (await r.aread()).decode(errors="replace")
                await r.aclose()
                if self._paid_plan_voice(voice, body):
                    voice = await self._voice_id()
                    cached = self.cache_path(text, mood, voice, model)
                    if cached.exists():
                        return cached.read_bytes(), None, cached
                    continue
            break
        if r.status_code != 200:
            body = (await r.aread()).decode("utf-8", "replace")
            await r.aclose()
            code = self._refused(r.status_code, body)
            raise ElevenError(f"ElevenLabs voice error ({code}).", code)
        return None, r, cached

    def keep_stream(self, text: str, pcm: bytes, cached: Path, ms: int) -> None:
        """A streamed reply finished: keep it as WAV so saying it again costs nothing."""
        if len(pcm) >= 4800:  # at least 0.1 s: never cache a cut-off stream
            cached.write_bytes(wav_of(pcm))
        if self._usage:
            self._usage["used"] += len(text)
        self.health = {"ok": True, "ms": ms, "what": "voice (streamed)"}
        self.last_error = None
        self._out_of_credits(False)

    async def speak(self, text: str, mood: str = "calm") -> bytes:
        """WAV bytes in your ElevenLabs voice. Cached phrases cost nothing."""
        s = settings.get()
        voice, model = await self._voice_id(), s["eleven_model"]
        cached = self.cache_path(text, mood, voice, model)
        if cached.exists():
            return cached.read_bytes()
        if not self.usable(len(text)):
            raise ElevenError("ElevenLabs is resting or the monthly credits are nearly used.", "budget")
        body = self._body(text, mood, model)
        t0 = time.perf_counter()
        for _ in range(2):  # a second try only after a voice turned out to need a paid plan
            try:
                r = await self._http.post(f"/v1/text-to-speech/{voice}", params={"output_format": "wav_24000"},
                                          json=body, headers=self._headers())
            except httpx.HTTPError as e:
                self._failed("offline", 30)
                raise ElevenError("ElevenLabs can't be reached.", "offline") from e
            if r.status_code == 402 and self._paid_plan_voice(voice, r.text):
                voice = await self._voice_id()
                cached = self.cache_path(text, mood, voice, model)
                if cached.exists():
                    return cached.read_bytes()
                continue
            break
        if r.status_code != 200:
            code = self._refused(r.status_code, r.text)
            raise ElevenError(f"ElevenLabs voice error ({code}).", code)
        audio = r.content
        cached.write_bytes(audio)
        if self._usage:
            self._usage["used"] += len(text)  # keep the budget current between quota checks
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "what": "voice"}
        self.last_error = None
        self._out_of_credits(False)  # credits are back
        return audio

    # ------------------------------------------------------------ hearing

    async def transcribe(self, wav: bytes) -> tuple[str, str]:
        """(text, language) for a spoken command, via Scribe. Hindi, English and mixed speech are detected."""
        if not self.usable():
            raise ElevenError("ElevenLabs is resting or the monthly credits are nearly used.", "budget")
        t0 = time.perf_counter()
        try:
            r = await self._http.post("/v1/speech-to-text", headers=self._headers(),
                                      data={"model_id": STT_MODEL, "tag_audio_events": "false"},
                                      files={"file": ("command.wav", wav, "audio/wav")})
        except httpx.HTTPError as e:
            self._failed("offline", 30)
            raise ElevenError("ElevenLabs can't be reached.", "offline") from e
        if r.status_code != 200:
            code = self._refused(r.status_code, r.text)
            raise ElevenError(f"ElevenLabs hearing error ({code}).", code)
        data = r.json()
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "what": "hearing"}
        self.last_error = None
        return (data.get("text") or "").strip(), (data.get("language_code") or "")

    async def agent_session(self) -> dict:
        """A one-time link for a live conversation with your ElevenLabs agent (the dashboard connects with it, so the
        key never leaves this laptop's core): {signed_url, agent_id, name}. The agent is the one in Settings, else
        the one named "PLAG" on your account."""
        agent_id = settings.get()["eleven_agent_id"] or self._agent_id
        try:
            if not agent_id:
                r = await self._http.get("/v1/convai/agents", headers=self._headers(), params={"page_size": 30})
                if r.status_code != 200:
                    raise ElevenError(f"Couldn't list your ElevenLabs agents ({r.status_code}).", str(r.status_code))
                agents = r.json().get("agents", [])
                pick = next((a for a in agents if (a.get("name") or "").strip().casefold() == "plag"), None)
                if not pick:
                    raise ElevenError("No ElevenLabs agent named PLAG on your account.", "no_agent")
                agent_id, self._agent_name = pick["agent_id"], pick.get("name") or "PLAG"
                self._agent_id = agent_id
            r = await self._http.get("/v1/convai/conversation/get-signed-url", headers=self._headers(),
                                     params={"agent_id": agent_id})
        except httpx.HTTPError as e:
            raise ElevenError("ElevenLabs can't be reached.", "offline") from e
        if r.status_code != 200:
            raise ElevenError(f"ElevenLabs didn't start the agent ({r.status_code}).", str(r.status_code))
        return {"signed_url": r.json()["signed_url"], "agent_id": agent_id, "name": self._agent_name or "PLAG"}

    async def realtime(self) -> Realtime:
        """Open a live transcription session (Scribe v2 Realtime). The phrase ends when PLAG commits it."""
        if not self.usable():
            raise ElevenError("ElevenLabs is resting or the monthly credits are nearly used.", "budget")
        query = urlencode({"model_id": REALTIME_MODEL, "audio_format": "pcm_16000", "commit_strategy": "manual",
                           "include_language_detection": "true"})
        t0 = time.perf_counter()
        try:
            ws = await ws_connect(f"{WS_API}/v1/speech-to-text/realtime?{query}", additional_headers=self._headers(),
                                  open_timeout=6, max_size=2**20, ping_interval=None)
        except InvalidStatus as e:
            code = str(e.response.status_code)
            self._failed(code, 3600 if code in ("401", "402") else 60)
            raise ElevenError(f"ElevenLabs realtime hearing error ({code}).", code) from e
        except (OSError, TimeoutError, WebSocketException) as e:
            self._failed("offline", 30)
            raise ElevenError("ElevenLabs realtime hearing can't connect.", "offline") from e
        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), 5))
        except (asyncio.TimeoutError, WebSocketException, ValueError) as e:
            await ws.close()
            self._failed("realtime_start", 30)
            raise ElevenError("ElevenLabs realtime hearing didn't start.", "realtime_start") from e
        if first.get("message_type") != "session_started":
            await ws.close()
            code = first.get("message_type") or "realtime_start"
            self._failed(code, 3600 if code in ("auth_error", "quota_exceeded") else 60)
            raise ElevenError(first.get("error") or f"ElevenLabs realtime hearing refused ({code}).", code)
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "what": "realtime hearing"}
        self.last_error = None
        return Realtime(ws)


eleven = ElevenLabs()
