"""Local API for the dashboard: loopback only, token-authenticated, origin-checked."""

import asyncio
import base64
import binascii
import contextlib
import hmac
import json
import logging
import os
import re
import subprocess
import time

import psutil
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__, approvals, whatsapp
from . import documents, drafts, imagegen, location, model3d, weather
from . import memory as mem
from . import settings as app_settings
from .elevenlabs import REALTIME_ERRORS, ElevenError, eleven
from .google import GoogleError, google, open_in_browser
from .agent import agent
from .audit import audit
from .bus import bus
from .config import MODELS, TTS_CACHE_DIR, VOICE
from .gemini import ProviderError, gemini
from .nvidia import nvidia
from .edgevoice import EdgeError, edgevoice
from .nvasr import NAME as NV_HEARING, nvhearing
from .sarvam import SarvamError, sarvam
from .nvspeech import RATE as NV_RATE, NvSpeechError, nvspeech
from .policy import Halted, policy
from .system import sampler
from . import voice as local_voice
from . import wake as wake_mod
from .wake import wake
import threading

log = logging.getLogger("plag")
TOKEN = os.environ.get("PLAG_TOKEN", "")
ORIGIN_RE = re.compile(r"^(file://|null|http://(127\.0\.0\.1|localhost):\d{2,5})$")
_current: asyncio.Task | None = None


# ---------------------------------------------------------------- background loops

_ui = {"visible": True}  # the dashboard is on screen (not hidden in the tray or minimized)


async def _metrics_loop() -> None:
    n = 0
    while True:
        await asyncio.sleep(1.0)
        if not bus.has_subscribers or not _ui["visible"]:
            continue  # nobody is watching: sample nothing
        try:
            bus.publish("system.metrics", await asyncio.to_thread(sampler.sample))
            if n % 3 == 0:
                bus.publish("system.processes", await asyncio.to_thread(sampler.processes))
            if n % 5 == 0:
                bus.publish("connectors", await asyncio.to_thread(connectors))
        except Exception:
            log.exception("metrics loop")
        n += 1


async def _reminder_loop() -> None:
    """Every 5 s: reminders that are due go to the dashboard, which speaks them and shows a Windows notification.
    Only while a dashboard is connected, so a reminder is never marked done without being delivered."""
    while True:
        await asyncio.sleep(5)
        if not bus.has_subscribers or policy.halted:
            continue
        try:
            for r in await asyncio.to_thread(mem.take_due):
                bus.publish("reminder.due", {"id": r["id"], "text": r["text"], "due": r["due"], "late_minutes": r["late_minutes"]})
                audit("reminder.due", id=r["id"], late_minutes=r["late_minutes"])
                bus.publish("reminders.changed", {})
        except Exception:
            log.exception("reminder loop")


async def _parent_watchdog() -> None:
    """Exit when the desktop shell that started us is gone, so no orphan core keeps running."""
    ppid = int(os.environ.get("PLAG_PARENT_PID") or 0)
    if not ppid:
        return
    while True:
        await asyncio.sleep(3)
        if not psutil.pid_exists(ppid):
            wake.stop()
            os._exit(0)


def _eleven_can_speak(text: str) -> bool:
    s = app_settings.get()
    return s["eleven_speak"] and eleven.configured() and eleven.usable() and len(text) <= s["eleven_max_chars"]


def _voice_ready(engine: str, text: str) -> bool:
    """Can this voice speak now? Looked up without loading anything (the offline voice is ~350 MB once loaded)."""
    if engine == "sarvam":
        return sarvam.usable()
    if engine == "nvidia":
        return nvspeech.usable()
    if engine == "edge":
        return edgevoice.usable()
    if engine == "elevenlabs":
        return _eleven_can_speak(text)
    return local_voice.MODEL.exists() and local_voice.VOICES.exists()


def _voice_order(text: str = "Okay.") -> list[str]:
    """Who speaks, in order: the voice chosen in Settings first, the others as backups. Auto: Sarvam when its key is
    saved, Leo on NVIDIA, Edge (free, no key), your ElevenLabs voice, then the offline voice."""
    order = [e for e in ("sarvam", "nvidia", "edge", "elevenlabs", "local") if _voice_ready(e, text)]
    choice = app_settings.get()["voice_engine"]
    if choice in order:
        order.remove(choice)
        order.insert(0, choice)
    return order


def _eleven_speaks() -> bool:
    """A cloud voice is PLAG's voice: the offline Kokoro voice (~350 MB when loaded) is only the fallback, loaded if
    ever needed and freed again when idle."""
    order = _voice_order()
    return bool(order) and order[0] != "local"


async def _nvidia_stream(text: str, mood: str) -> Response | None:
    """Leo's voice as NVIDIA makes it (first sound ~0.27 s), or the saved WAV when this reply was said before.
    None when the NVIDIA voice can't start: the next voice takes over."""
    voice_name, _lang, spoken = nvspeech.plan(text, mood)
    path = nvspeech.cache_path(voice_name, spoken)
    if path.exists():
        return Response(path.read_bytes(), media_type="audio/wav", headers={"X-PLAG-Voice": "nvidia-magpie"})
    loop = asyncio.get_running_loop()
    pieces: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def work() -> None:
        try:
            for pcm in nvspeech.stream(text, mood, stop):
                loop.call_soon_threadsafe(pieces.put_nowait, pcm)
            loop.call_soon_threadsafe(pieces.put_nowait, None)
        except Exception as e:  # NvSpeechError, the connection dropping
            loop.call_soon_threadsafe(pieces.put_nowait, e)

    threading.Thread(target=work, daemon=True, name="plag-nvtts").start()
    first = await pieces.get()
    if first is None or isinstance(first, Exception):
        stop.set()
        log.info("NVIDIA streamed voice unavailable (%s)", first)
        return None

    async def body():
        try:
            yield first
            while (piece := await pieces.get()) is not None and not isinstance(piece, Exception):
                yield piece
        finally:
            stop.set()  # you interrupted and the dashboard hung up: NVIDIA stops making the rest

    return StreamingResponse(body(), media_type=f"audio/L16;rate={NV_RATE};channels=1",
                             headers={"X-PLAG-Voice": "nvidia-magpie-stream", "Cache-Control": "no-store"})


IDLE_S = 600  # a model nobody used for 10 minutes gives its memory back


async def _idle_loop() -> None:
    """Every minute: free the models that aren't pulling their weight (the listening model always stays)."""
    while True:
        await asyncio.sleep(60)
        try:
            freed = await asyncio.to_thread(wake_mod.unload_idle, IDLE_S)
            if _eleven_speaks() and await asyncio.to_thread(local_voice.unload_idle, IDLE_S):
                freed.append("kokoro")
            if freed:
                log.info("freed idle models: %s", ", ".join(freed))
        except Exception:
            log.exception("idle loop")


def _prune_voice_cache(everything: bool = False) -> tuple[int, int]:
    """Spoken-reply cache: local-voice and NVIDIA files (in the "nvidia" folder) older than a week go; ElevenLabs files
    (you paid for them, and they save credits when a reply repeats) are kept up to 80 MB, newest first. `everything`
    empties it. -> (files, bytes)."""
    files = sorted((p for p in TTS_CACHE_DIR.rglob("*") if p.suffix in (".wav", ".mp3") and p.is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    week_ago = time.time() - 7 * 86400
    kept_el, gone, freed = 0, 0, 0
    for p in files:
        size = p.stat().st_size
        drop = everything
        if not drop and p.name.startswith("el_"):
            kept_el += size
            drop = kept_el > 80 * 2**20
        elif not drop:
            drop = p.stat().st_mtime < week_ago
        if drop:
            with contextlib.suppress(OSError):
                p.unlink()
                gone, freed = gone + 1, freed + size
    return gone, freed


async def _warm_voice() -> None:
    """ "Yes sir?" in PLAG's voice now (saved on disk: made only once per voice), and NVIDIA's hearing connected, so
    the first "PLAG" and the first reply aren't slower (the first NVIDIA reply was ~4 s cold). Never with ElevenLabs:
    that would spend your credits at every start."""
    if (_voice_order() or ["none"])[0] in ("nvidia", "sarvam", "edge"):
        for line in ("Yes sir?", "जी सर?"):
            with contextlib.suppress(Exception):
                await tts(SpeakRequest(text=line, mood="calm"))
    if nvhearing.configured():
        with contextlib.suppress(Exception):
            await asyncio.to_thread(nvhearing._get_service)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = [asyncio.create_task(_metrics_loop()), asyncio.create_task(_parent_watchdog()),
             asyncio.create_task(_reminder_loop()), asyncio.create_task(_idle_loop())]
    audit("core.start", version=__version__)
    if wake_mod.available():  # warm the listening model so the first "PLAG" is quick
        threading.Thread(target=wake_mod.model, daemon=True).start()
    if not _eleven_speaks():  # the offline voice is PLAG's voice: warm it, with "Yes sir?" ready
        threading.Thread(target=local_voice.warm, daemon=True).start()
    else:
        tasks.append(asyncio.create_task(_warm_voice()))
    if eleven.configured():
        tasks.append(asyncio.create_task(eleven.usage(fresh=True)))  # warm the connection and read the quota once
    threading.Thread(target=_prune_voice_cache, daemon=True).start()
    threading.Thread(target=drafts.clear_temp, daemon=True).start()  # temporary copies of unsaved drafts from last time
    yield
    drafts.clear_temp()
    wake.stop()
    for t in tasks:
        t.cancel()
    await gemini.close()
    await google._http.aclose()
    await eleven._http.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


async def _guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and not ORIGIN_RE.match(origin):
        return JSONResponse({"error": "origin"}, status_code=403)
    if request.method != "OPTIONS" and request.url.path.startswith("/v1/"):
        sent = request.headers.get("x-plag-token", "")
        if not TOKEN or not hmac.compare_digest(sent, TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


# order matters: CORS is added last so it wraps the guard and decorates its errors too
app.add_middleware(BaseHTTPMiddleware, dispatch=_guard)
app.add_middleware(CORSMiddleware, allow_origin_regex=ORIGIN_RE.pattern, allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["X-PLAG-Token", "Content-Type"], max_age=600)


# ---------------------------------------------------------------- helpers

VOICE_NAMES = {"sarvam": "Sarvam AI", "nvidia": "Leo on NVIDIA (streamed)", "edge": "Microsoft Edge (free)",
               "elevenlabs": "ElevenLabs", "local": "Offline voice (Kokoro)"}


def voice_row() -> dict:
    """Who speaks PLAG's replies right now: the voice chosen in Settings, or the backup when it can't."""
    s = app_settings.get()
    order = _voice_order()
    if not order:
        return {"id": "voice_engine", "name": "Voice", "role": "Speaks PLAG's replies", "state": "off",
                "detail": "No voice available · replies are text only"}
    first = order[0]
    detail = VOICE_NAMES[first] + {"sarvam": f" · {s['sarvam_speaker']}", "edge": f" · {s['edge_voice'].split('-')[2].removesuffix('Neural')}"}.get(first, "")
    chosen = s["voice_engine"]
    if chosen not in ("auto", first):
        detail += f" · {VOICE_NAMES.get(chosen, chosen)} isn't available right now"
    return {"id": "voice_engine", "name": "Voice", "role": "Speaks PLAG's replies", "state": "online" if chosen in ("auto", first) else "degraded",
            "detail": detail}


def sarvam_row() -> dict:
    if not sarvam.configured():
        state, detail = "off", "No key · add it in Settings → Voice to use Sarvam's Indian voices"
    elif not sarvam.usable():
        state, detail = "degraded", f"Paused after an error ({sarvam.last_error}) · the next voice speaks"
    else:
        state, detail = ("online" if sarvam.health else "ready"), f"Bulbul v3 · {app_settings.get()['sarvam_speaker']}"
    return {"id": "sarvam", "name": "Sarvam AI", "role": "Indian voices", "state": state, "detail": detail}


def glm_row() -> dict:
    h = nvidia.health
    if not nvidia.ready():
        state, detail = ("off", "No NVIDIA key saved") if not nvidia._keys() else ("degraded", "Both models resting after errors · back in 5 min")
    elif h and h["ok"]:
        state, detail = "online", f"Last answer: {h['model'].split('/')[-1]} · {h['ms'] / 1000:.1f} s"
    elif h:
        state, detail = "degraded", f"{h['model'].split('/')[-1]}: {h['error']} · still racing"
    else:
        state, detail = "ready", "gpt-oss-20b + mistral-nemotron race Gemini on every question"
    return {"id": "nvidia", "name": "NVIDIA open models", "role": "Race Gemini", "state": state, "detail": detail}


def eleven_row() -> dict:
    u = eleven._usage
    if not eleven.configured():
        state, detail = "off", "Not configured · add your key in Settings · using the local voice"
    elif not eleven.usable():
        if u and u["limit"] and u["used"] >= u["limit"]:
            detail = f"Monthly credits used up (0 of {u['limit']:,} left) · local voice until they reset"
        elif u and not eleven._budget_ok():
            detail = "Monthly credits nearly used · local voice until reset"
        else:
            detail = f"Paused after an error ({eleven.last_error}) · local voice for now"
        state = "degraded"
    else:
        left = f" · {max(0, u['limit'] - u['used']):,} of {u['limit']:,} credits left" if u and u["limit"] else ""
        paid = " · your chosen voice needs a paid plan, so a built-in voice speaks" if eleven.paid_only else ""
        state, detail = "online", f"{app_settings.get()['eleven_model']}{left}{paid}"
    return {"id": "elevenlabs", "name": "ElevenLabs", "role": "Voice and hearing", "state": state, "detail": detail}


def image_row() -> dict:
    h = imagegen.health
    if not imagegen.ready():
        state, detail = "off", "No image key · PLAG / nvidia_image_api_key"
    elif h and h["ok"]:
        state, detail = "online", f"FLUX.1-dev · {h['ms'] / 1000:.1f} s · saves to Pictures\\PLAG"
    elif h:
        state, detail = "degraded", f"FLUX.1-dev · last try: {h['error']}"
    else:
        state, detail = "ready", "FLUX.1-dev on NVIDIA · say “generate an image of…”"
    return {"id": "images", "name": "Image generation", "role": "Draw images", "state": state, "detail": detail}


def model3d_row() -> dict:
    h = model3d.health
    if not model3d.ready():
        state, detail = "off", "No 3D key · PLAG / nvidia_trellis_api_key"
    elif h and h["ok"]:
        state, detail = "online", f"TRELLIS · {h['ms'] / 1000:.1f} s · saves to Documents\\PLAG\\3D"
    elif h:
        state, detail = "degraded", f"TRELLIS · last try: {h['error']}"
    else:
        state, detail = "ready", "TRELLIS on NVIDIA · say “make a 3D model of…”"
    return {"id": "model3d", "name": "3D models", "role": "Build 3D objects", "state": state, "detail": detail}


def weather_row() -> dict:
    h = weather.health
    if not weather.ready():
        state, detail = "ready", "Forecasts on (Open-Meteo) · no simulation key"
    elif h and h["ok"]:
        state, detail = "online", f"Forecasts + FourCastNet simulation · {h['ms'] / 1000:.1f} s"
    elif h:
        state, detail = "degraded", f"FourCastNet · last try: {h['error']} · forecasts still work"
    else:
        state, detail = "ready", "Forecasts + FourCastNet · say “weather simulation”"
    return {"id": "weather", "name": "Weather", "role": "Forecasts and a global simulation", "state": state, "detail": detail}


def location_row() -> dict:
    h = location.health
    if not location.enabled():
        state, detail = "off", "Turned off in Settings"
    elif h and h["ok"]:
        state, detail = "online", f"Windows Location · within {h['accuracy_m']} m · address from OpenStreetMap"
    elif h:
        state, detail = "degraded", {"denied": "Location is off for apps in Windows privacy settings"}.get(h["error"], "No fix right now")
    else:
        state, detail = "ready", "Windows Location · say “where am I”"
    return {"id": "location", "name": "Location", "role": "Where you are, your address", "state": state, "detail": detail}


def google_row() -> dict:
    g = google.status()
    if g["connected"]:
        state, detail = "online", f"{g['email'] or 'Signed in'} · read-only email and calendar"
    elif g["error"]:
        state, detail = "degraded", "Needs signing in again · click Connect"
    elif g["client"]:
        state, detail = "ready", "Client saved · click Connect to sign in"
    else:
        state, detail = "off", "Not connected · needs your Google OAuth client"
    return {"id": "google", "name": "Gmail + Calendar", "role": "Read email and your schedule", "state": state,
            "detail": detail, "action": "disconnect" if g["connected"] else "connect"}


def connectors() -> list[dict]:
    def latest(models: list[str]) -> tuple[str, dict] | None:
        seen = [(m, gemini.health[m]) for m in models if m in gemini.health]
        return max(seen, key=lambda x: x[1]["at"]) if seen else None

    def state_of(entry):
        if not entry:
            return "ready", "Waiting for the first request"
        m, h = entry
        if h["ok"]:
            return "online", f"{m} · {h['ms'] / 1000:.1f} s"
        if h["error"] == "quota_day":
            return "degraded", "Free daily limit reached · using local voice"
        return "degraded", f"{m} · error {h['error']}"

    brain_state, brain_detail = state_of(latest(MODELS["turn"]))
    voice_state, voice_detail = state_of(latest(MODELS["tts"]))
    wa = whatsapp.status()
    if not wa["installed"]:
        wa_state, wa_detail = "degraded", "WhatsApp Desktop not found · numbers open in the browser"
    elif not wa["running"]:
        wa_state, wa_detail = "ready", "Anyone in your WhatsApp · opens it when needed"
    else:
        wa_state, wa_detail = "online", "Anyone in your WhatsApp · sends without asking"
    ws = wake.status()
    wake_state = {"listening": "online", "starting": "ready", "error": "degraded"}.get(ws["state"], "off")
    wake_detail = {"listening": "Say “PLAG” · offline recognizer", "starting": "Starting…"}.get(
        ws["state"], ws["error"] or "Off")
    return [
        {"id": "wake", "name": "Wake word", "role": "Say PLAG", "state": wake_state, "detail": wake_detail},
        {"id": "gemini", "name": "Gemini", "role": "Understands voice, text and camera", "state": brain_state, "detail": brain_detail},
        {"id": "whatsapp", "name": "WhatsApp", "role": "Send messages by voice", "state": wa_state, "detail": wa_detail},
        {"id": "voice", "name": "Natural voice", "role": f"Gemini speech · {VOICE}", "state": voice_state, "detail": voice_detail},
        voice_row(),
        sarvam_row(),
        eleven_row(),
        glm_row(),
        image_row(),
        model3d_row(),
        weather_row(),
        location_row(),
        google_row(),
        {"id": "linkedin", "name": "LinkedIn", "role": "Sign in and post", "state": "planned", "detail": "Connects in phase 12"},
    ]


def _error(exc: BaseException) -> JSONResponse:
    bus.publish("status.changed", {"state": "halted" if policy.halted else "idle"})
    if isinstance(exc, ProviderError):
        status = {"no_key": 500, "offline": 503}.get(str(exc.code), 503)
        return JSONResponse({"error": str(exc.code or "provider"), "message": str(exc), "tried": exc.tried}, status_code=status)
    if isinstance(exc, Halted):
        return JSONResponse({"error": "halted"}, status_code=423)
    log.error("task failed", exc_info=exc)
    return JSONResponse({"error": "internal", "message": str(exc)[:200]}, status_code=500)


async def _run(factory):
    """Run one agent task at a time; a new request replaces the one in flight."""
    global _current
    if policy.halted:
        return JSONResponse({"error": "halted"}, status_code=423)
    if _current and not _current.done():
        _current.cancel()
    task = asyncio.create_task(factory())
    _current = task
    await asyncio.wait({task})
    if task.cancelled():
        return JSONResponse({"error": "cancelled"}, status_code=409)
    if (exc := task.exception()) is not None:
        return _error(exc)
    return task.result()


def _lang(value: str) -> str:
    return value if value in ("auto", "en", "hi") else "auto"


# ---------------------------------------------------------------- routes

class TextTurn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    lang: str = "auto"
    approval_id: str | None = None
    heard_by: str | None = Field(default=None, max_length=40)  # spoken, heard word by word ("ElevenLabs realtime")
    followup: str = Field(default="", pattern=r"^(|answer|window)$")  # said without "PLAG" after a reply


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    mood: str = "calm"


class WakeRequest(BaseModel):
    enabled: bool


class SpeakingRequest(BaseModel):
    speaking: bool


class GoogleClientRequest(BaseModel):
    file: str = Field(min_length=20, max_length=20_000)  # the client JSON Google Cloud Console downloads


class Decision(BaseModel):
    approve: bool
    lang: str = "auto"


class VisionRequest(BaseModel):
    image: str = Field(min_length=100, max_length=8_000_000)  # base64 JPEG
    lang: str = "auto"
    question: str = Field(default="", max_length=600)
    source: str = Field(default="camera", pattern=r"^(camera|upload)$")  # an uploaded picture or the camera


class ImagineRequest(BaseModel):
    image: str = Field(min_length=100, max_length=4_000_000)  # base64 JPEG camera frame
    lang: str = "auto"
    style: str = Field(default="", max_length=200)


@app.get("/v1/health")
async def health():
    return {"ok": True, "version": __version__, "halted": policy.halted, "voice": VOICE, "models": MODELS,
            "wake": wake.status()}


def _followup(value: str | None) -> str:
    return value if value in ("answer", "window") else ""


@app.post("/v1/turn/audio")
async def turn_audio(request: Request, lang: str = "auto", approval_id: str | None = None, hint: str | None = None,
                     followup: str | None = None):
    body = await request.body()
    if not body or len(body) > 8 * 2**20:
        return JSONResponse({"error": "size", "message": "Audio must be under 8 MB"}, status_code=413)
    if not body.startswith(b"RIFF"):
        return JSONResponse({"error": "format", "message": "Send 16-bit PCM WAV"}, status_code=415)
    return await _run(lambda: agent.turn(audio=body, lang_pref=_lang(lang), approval_id=approval_id,
                                         hint=(hint or "")[:300] or None, followup=_followup(followup)))


@app.post("/v1/turn/text")
async def turn_text(req: TextTurn):
    return await _run(lambda: agent.turn(text=req.text, lang_pref=_lang(req.lang), approval_id=req.approval_id,
                                         heard_by=req.heard_by, followup=_followup(req.followup)))


@app.post("/v1/approvals/{approval_id}")
async def decide(approval_id: str, req: Decision):
    return await _run(lambda: agent.decide(approval_id, req.approve, _lang(req.lang)))


@app.post("/v1/vision")
async def vision(req: VisionRequest):
    try:
        jpeg = base64.b64decode(req.image.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError):
        return JSONResponse({"error": "format", "message": "Send a base64 JPEG"}, status_code=415)
    if not jpeg.startswith(b"\xff\xd8"):
        return JSONResponse({"error": "format", "message": "Send a base64 JPEG"}, status_code=415)
    return await _run(lambda: agent.look(jpeg=jpeg, lang_pref=_lang(req.lang), question=req.question, source=req.source))


@app.post("/v1/imagine")
async def imagine(req: ImagineRequest):
    """ "Gen an image of this": one camera frame in, a new image out."""
    try:
        jpeg = base64.b64decode(req.image.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError):
        return JSONResponse({"error": "format", "message": "Send a base64 JPEG"}, status_code=415)
    if not jpeg.startswith(b"\xff\xd8"):
        return JSONResponse({"error": "format", "message": "Send a base64 JPEG"}, status_code=415)
    return await _run(lambda: agent.imagine(jpeg=jpeg, lang_pref=_lang(req.lang), style=req.style))


def _image_file(image_id: str):
    return imagegen.path_of(image_id) if re.fullmatch(r"[0-9a-f]{10}", image_id or "") else None


@app.get("/v1/images/{image_id}")
async def image_get(image_id: str):
    path = _image_file(image_id)
    if path is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return Response(path.read_bytes(), media_type="image/png" if path.suffix == ".png" else "image/jpeg")


@app.post("/v1/images/{image_id}/open")
async def image_open(image_id: str):
    path = _image_file(image_id)
    if path is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await asyncio.to_thread(os.startfile, str(path))  # the Windows Photos app
    return {"ok": True}


@app.post("/v1/images/{image_id}/reveal")
async def image_reveal(image_id: str):
    path = _image_file(image_id)
    if path is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await asyncio.to_thread(subprocess.Popen, ["explorer.exe", f"/select,{path}"])
    return {"ok": True}


def _draft(draft_id: str, kind: str = "") -> drafts.Draft | None:
    d = drafts.get(draft_id) if re.fullmatch(r"[0-9a-f]{10}", draft_id or "") else None
    return d if d and (not kind or d.kind == kind) else None


def _not_saved() -> JSONResponse:
    return JSONResponse({"error": "not_saved", "message": "It isn't saved yet: say “save” or press Save first."}, status_code=409)


@app.get("/v1/models3d/{model_id}")
async def model_get(model_id: str):
    """A 3D model PLAG made (a draft in memory until you save it)."""
    d = _draft(model_id, "3d")
    if d is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return Response(d.data, media_type="model/gltf-binary")


@app.post("/v1/models3d/{model_id}/open")
async def model_open(model_id: str):
    d = _draft(model_id, "3d")
    if d is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    try:  # Windows 3D Viewer, if installed: the saved file, or a temporary copy of the draft
        await asyncio.to_thread(os.startfile, str(await asyncio.to_thread(drafts.temp_copy, d)))
    except OSError:
        return JSONResponse({"error": "no_viewer", "message": "No app on this laptop opens .glb files. Install 3D Viewer "
                                                             "from the Microsoft Store."}, status_code=409)
    return {"ok": True}


@app.post("/v1/models3d/{model_id}/reveal")
async def model_reveal(model_id: str):
    d = _draft(model_id, "3d")
    if d is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if not d.saved:
        return _not_saved()
    await asyncio.to_thread(subprocess.Popen, ["explorer.exe", f"/select,{d.saved}"])
    return {"ok": True}


@app.post("/v1/docs/{doc_id}/open")
async def doc_open(doc_id: str):
    """Open a PDF PLAG wrote, in your PDF reader (only when you ask: PLAG never opens it by itself). An unsaved draft
    opens from a temporary copy."""
    d = _draft(doc_id, "pdf")
    if d is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    await asyncio.to_thread(os.startfile, str(await asyncio.to_thread(drafts.temp_copy, d)))
    return {"ok": True}


@app.post("/v1/docs/{doc_id}/reveal")
async def doc_reveal(doc_id: str):
    d = _draft(doc_id, "pdf")
    if d is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if not d.saved:
        return _not_saved()
    await asyncio.to_thread(subprocess.Popen, ["explorer.exe", f"/select,{d.saved}"])
    return {"ok": True}


@app.post("/v1/drafts/{draft_id}/save")
async def draft_save(draft_id: str):
    """The Save button: write the PDF or 3D model to Documents\\PLAG. Nothing PLAG makes is saved before this."""
    d = _draft(draft_id)
    if d is None:
        return JSONResponse({"error": "not_found", "message": "That's no longer in memory (PLAG restarted?). Make it again."},
                            status_code=404)
    path = await asyncio.to_thread(drafts.save, d)
    audit("draft.saved", kind=d.kind, bytes=len(d.data))
    return {"ok": True, "path": str(path), "folder": str(path.parent)}


@app.get("/v1/weather/{run_id}/{index}")
async def weather_frame(run_id: str, index: int):
    path = weather.frame_of(run_id, index) if re.fullmatch(r"[0-9a-f]{10}", run_id or "") else None
    if path is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return Response(path.read_bytes(), media_type="image/png")


@app.post("/v1/weather/{run_id}/reveal")
async def weather_reveal(run_id: str):
    folder = weather.folder_of(run_id) if re.fullmatch(r"[0-9a-f]{10}", run_id or "") else None
    if folder is None or not folder.exists():
        return JSONResponse({"error": "not_found"}, status_code=404)
    await asyncio.to_thread(subprocess.Popen, ["explorer.exe", str(folder)])
    return {"ok": True}


@app.post("/v1/tts/stream")
async def tts_stream(req: SpeakRequest):
    """PLAG's voice as it's being made: the first sound plays while the rest is still coming (raw 16-bit PCM, the
    rate in the content type), or the whole WAV when this reply was said before. Leo on NVIDIA and ElevenLabs can
    stream; when the voice chosen first is another (Sarvam, Edge, offline) or neither works, 409: the dashboard then
    voices the reply phrase by phrase with /v1/tts."""
    if policy.halted:
        return JSONResponse({"error": "halted"}, status_code=423)
    order = _voice_order(req.text)
    upstream = None
    while order and order[0] in ("nvidia", "elevenlabs") and upstream is None:
        engine = order.pop(0)
        if engine == "nvidia":
            if (res := await _nvidia_stream(req.text, req.mood)) is not None:
                return res
            continue
        try:
            cached, upstream, path = await eleven.open_stream(req.text, req.mood)
        except ElevenError as e:
            log.info("ElevenLabs streamed voice unavailable (%s)", e.code)
            continue
        if cached is not None:
            return Response(cached, media_type="audio/wav", headers={"X-PLAG-Voice": "elevenlabs"})
    if upstream is None:
        return JSONResponse({"error": "no_stream"}, status_code=409)
    t0 = asyncio.get_running_loop().time()

    async def relay():
        pcm = bytearray()
        try:
            async for chunk in upstream.aiter_bytes():
                pcm += chunk
                yield chunk
            eleven.keep_stream(req.text, bytes(pcm), path, int((asyncio.get_running_loop().time() - t0) * 1000))
        finally:
            await upstream.aclose()

    return StreamingResponse(relay(), media_type="audio/L16;rate=24000;channels=1",
                             headers={"X-PLAG-Voice": "elevenlabs-stream", "Cache-Control": "no-store"})


@app.post("/v1/tts")
async def tts(req: SpeakRequest):
    """One phrase in PLAG's voice: the voice chosen in Settings, else the next one that works (see _voice_order)."""
    if policy.halted:
        return JSONResponse({"error": "halted"}, status_code=423)
    for engine in _voice_order(req.text):
        try:
            if engine == "sarvam":  # Indian voices; cached phrases are free
                audio, kind = await sarvam.speak(req.text, req.mood), "audio/wav"
            elif engine == "nvidia":  # Leo, ~0.6 s a phrase
                audio, kind = await asyncio.to_thread(nvspeech.speak, req.text, req.mood), "audio/wav"
            elif engine == "edge":  # Microsoft Edge's voices, free
                audio, kind = await edgevoice.speak(req.text, req.mood), "audio/mpeg"
            elif engine == "elevenlabs":  # your ElevenLabs voice: the quota guard keeps a reserve
                audio, kind = await eleven.speak(req.text, req.mood), "audio/wav"
            else:  # offline, on this laptop
                audio, kind = await asyncio.to_thread(local_voice.speak, req.text, req.mood), "audio/wav"
            return Response(audio, media_type=kind, headers={"X-PLAG-Voice": {"nvidia": "nvidia-magpie", "local": "kokoro"}.get(engine, engine)})
        except (SarvamError, NvSpeechError, EdgeError, ElevenError) as e:
            log.info("%s voice unavailable (%s); trying the next voice", engine, e.code)
        except Exception:
            log.exception("%s voice failed; trying the next voice", engine)
    try:
        wav, cached = await gemini.speak(req.text, mood=req.mood)
    except ProviderError as e:
        return JSONResponse({"error": str(e.code), "message": str(e), "tried": e.tried}, status_code=503)
    return Response(wav, media_type="audio/wav", headers={"X-PLAG-Cache": "hit" if cached else "miss"})


@app.get("/v1/wake")
async def wake_status():
    return wake.status()


@app.post("/v1/cache/clear")
async def cache_clear():
    """Settings -> Clear cache: every saved spoken reply (they're made again when needed; ElevenLabs ones cost
    credits again), and the idle models' memory. Your pictures, 3D models, reports and memories are never touched."""
    files, freed = await asyncio.to_thread(_prune_voice_cache, True)
    await asyncio.to_thread(wake_mod.unload_idle, 0)
    if _eleven_speaks():
        await asyncio.to_thread(local_voice.unload_idle, 0)
    audit("cache.cleared", files=files, bytes=freed)
    return {"ok": True, "files": files, "mb": round(freed / 2**20, 1)}


class SettingsRequest(BaseModel):
    changes: dict


class ElevenKeyRequest(BaseModel):
    key: str = Field(min_length=20, max_length=200)


def _live() -> dict:
    """What the dashboard can use right now: realtime hearing (NVIDIA Parakeet, or ElevenLabs Scribe), a streamed
    voice (only when the first voice is Leo on NVIDIA or ElevenLabs), and which voice speaks first."""
    s = app_settings.get()
    order = _voice_order()
    return {"hear": nvhearing.usable() or (s["eleven_hear"] and s["eleven_realtime"] and eleven.usable()),
            "stream": bool(order) and order[0] in ("nvidia", "elevenlabs"),
            "agent": s["voice_agent"] and eleven.configured() and eleven.usable(),
            "voice": order[0] if order else "none",
            "sarvam": sarvam.status(), "nvidia": {"configured": nvspeech.configured(), "usable": nvspeech.usable()},
            "edge": {"available": edgevoice.available(), "usable": edgevoice.usable(), "voices": list(app_settings.EDGE_VOICES)}}


@app.get("/v1/settings")
async def settings_get():
    return {"settings": app_settings.get(), "eleven": await eleven.status(), "live": _live()}


class SarvamKeyRequest(BaseModel):
    key: str = Field(min_length=10, max_length=200)


@app.post("/v1/sarvam/key")
async def sarvam_key(req: SarvamKeyRequest):
    """Your Sarvam key goes to Windows Credential Manager once Sarvam accepts it; it's never returned or logged."""
    try:
        status = await sarvam.save_key(req.key)
    except SarvamError as e:
        return JSONResponse({"error": e.code, "message": str(e)}, status_code=422)
    audit("sarvam.connected")
    bus.publish("connectors", await asyncio.to_thread(connectors))
    return {"sarvam": status, "live": _live()}


@app.delete("/v1/sarvam/key")
async def sarvam_key_remove():
    sarvam.remove_key()
    audit("sarvam.disconnected")
    bus.publish("connectors", await asyncio.to_thread(connectors))
    return {"sarvam": sarvam.status(), "live": _live()}


@app.post("/v1/settings")
async def settings_set(req: SettingsRequest):
    s = app_settings.update(req.changes)
    wake.apply_settings()
    audit("settings.changed", keys=sorted(req.changes))
    bus.publish("connectors", await asyncio.to_thread(connectors))
    return {"settings": s, "eleven": await eleven.status(), "live": _live()}


@app.post("/v1/elevenlabs/key")
async def eleven_key(req: ElevenKeyRequest):
    """Your ElevenLabs key goes to Windows Credential Manager after ElevenLabs confirms it; it's never returned."""
    try:
        status = await eleven.save_key(req.key)
    except ElevenError as e:
        return JSONResponse({"error": e.code, "message": str(e)}, status_code=422)
    audit("elevenlabs.connected")
    bus.publish("connectors", await asyncio.to_thread(connectors))
    return status


@app.delete("/v1/elevenlabs/key")
async def eleven_key_delete():
    eleven.forget_key()
    audit("elevenlabs.disconnected")
    bus.publish("connectors", await asyncio.to_thread(connectors))
    return {"ok": True}


@app.get("/v1/elevenlabs/voices")
async def eleven_voices():
    try:
        return {"voices": await eleven.voices()}
    except (ElevenError, Exception) as e:  # noqa: BLE001 - shown to the user as "couldn't list voices"
        return JSONResponse({"error": getattr(e, "code", "error"), "message": str(e)[:200]}, status_code=409)


@app.post("/v1/google/client")
async def google_client(req: GoogleClientRequest):
    try:
        cid = await asyncio.to_thread(google.save_client, req.file)
    except GoogleError as e:
        return JSONResponse({"error": e.code, "message": str(e)}, status_code=422)
    audit("google.client_saved", client=cid[:12] + "…")
    bus.publish("connectors", await asyncio.to_thread(connectors))
    return {"ok": True}


@app.post("/v1/google/connect")
async def google_connect(request: Request):
    """Opens Google's consent page in the browser; Google sends the browser back to /oauth/google."""
    try:
        url = google.begin(str(request.base_url))
    except GoogleError as e:
        return JSONResponse({"error": e.code, "message": str(e)}, status_code=409)
    await asyncio.to_thread(open_in_browser, url)
    audit("google.connect_started")
    return {"ok": True}


@app.post("/v1/google/disconnect")
async def google_disconnect():
    await google.disconnect()
    audit("google.disconnected")
    bus.publish("connectors", await asyncio.to_thread(connectors))
    return {"ok": True}


_PAGE = """<!doctype html><meta charset="utf-8"><title>PLAG</title>
<body style="margin:0;display:grid;place-items:center;height:100vh;background:#050506;color:#eeefe8;font:16px system-ui">
<div style="max-width:420px;text-align:center"><div style="color:{color};font:600 13px monospace;letter-spacing:.2em">PLAG</div>
<h1 style="font-weight:500;font-size:22px">{title}</h1><p style="color:#9a9c94">{body}</p></div></body>"""


@app.get("/oauth/google")
async def google_callback(state: str = "", code: str = "", error: str = ""):
    """Where Google sends the browser after sign-in. No token here (the browser has none): the one-time `state`
    PLAG created for this sign-in is the check instead."""
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    if error or not code:
        body = _PAGE.format(color="#ff5a4e", title="Google sign-in was cancelled", body=esc(error or "No code came back."))
        return Response(body, media_type="text/html", status_code=400)
    try:
        email = await google.finish(state, code)
    except GoogleError as e:
        return Response(_PAGE.format(color="#ff5a4e", title="Couldn't connect Google", body=esc(e)), media_type="text/html",
                        status_code=400)
    audit("google.connected")
    bus.publish("connectors", await asyncio.to_thread(connectors))
    return Response(_PAGE.format(color="#D6F24B", title="PLAG is connected to Google",
                                 body=f"{esc(email)} · read-only email and calendar. You can close this tab."),
                    media_type="text/html")


@app.get("/v1/memory")
async def memory_list():
    return {"memories": await asyncio.to_thread(mem.memories)}


@app.delete("/v1/memory/{memory_id}")
async def memory_delete(memory_id: str):
    removed = await asyncio.to_thread(mem.forget, memory_id)
    audit("memory.deleted", id=memory_id, removed=removed)
    bus.publish("memory.changed", {})
    return {"ok": removed}


@app.get("/v1/reminders")
async def reminders_list():
    return {"reminders": await asyncio.to_thread(mem.reminders)}


@app.delete("/v1/reminders/{reminder_id}")
async def reminder_delete(reminder_id: str):
    removed = await asyncio.to_thread(mem.cancel_reminder, reminder_id)
    audit("reminder.cancelled", id=reminder_id, removed=removed)
    bus.publish("reminders.changed", {})
    return {"ok": removed}


@app.get("/v1/agent/session")
async def agent_session():
    """A one-time link to talk with your ElevenLabs agent (the dashboard's SDK connects with it; the key stays here).
    409 when the agent isn't in use: the dashboard then uses PLAG's own voice loop."""
    s = app_settings.get()
    if policy.halted:
        return JSONResponse({"error": "halted"}, status_code=423)
    if not (s["voice_agent"] and eleven.configured()):
        return JSONResponse({"error": "no_agent", "message": "The ElevenLabs agent is off or not set up."}, status_code=409)
    if not eleven.usable():  # out of credits or resting: answer at once, don't let the agent connect and fail
        return JSONResponse({"error": "unavailable", "message": "ElevenLabs is out of credits or resting."}, status_code=409)
    try:
        info = await eleven.agent_session()
    except ElevenError as e:
        log.info("ElevenLabs agent unavailable (%s); PLAG's own voice takes over", e.code)
        return JSONResponse({"error": e.code, "message": str(e)}, status_code=409)
    audit("agent.session", agent=info["agent_id"])
    return info


class PauseRequest(BaseModel):
    paused: bool


@app.post("/v1/wake/pause")
async def wake_pause(req: PauseRequest):
    """While you talk with the ElevenLabs agent it has the microphone: the "PLAG" listener rests so both don't answer."""
    wake.paused = req.paused
    return {"ok": True}


class VisibleRequest(BaseModel):
    visible: bool


@app.post("/v1/ui/visible")
async def ui_visible(req: VisibleRequest):
    """The dashboard went to the tray (or came back): live system stats are only sampled while you can see them."""
    _ui["visible"] = req.visible
    return {"ok": True}


@app.post("/v1/voice/speaking")
async def voice_speaking(req: SpeakingRequest):
    """The dashboard says when PLAG starts and stops talking, so the listener can catch "stop" quickly (and, when
    always listening, not take PLAG's own voice for a command)."""
    if wake.speaking and not req.speaking:
        wake.quiet_since = time.monotonic()
    wake.speaking = req.speaking
    return {"ok": True}


@app.post("/v1/wake")
async def wake_toggle(req: WakeRequest):
    if req.enabled and not policy.halted:
        wake.start()
    elif req.enabled:
        wake.enabled = True  # starts on resume
    else:
        wake.stop()
    audit("wake.toggle", enabled=req.enabled)
    return wake.status()


@app.post("/v1/cancel")
async def cancel():
    if _current and not _current.done():
        _current.cancel()
    bus.publish("status.changed", {"state": "halted" if policy.halted else "idle"})
    return {"ok": True}


@app.post("/v1/killswitch")
async def killswitch():
    policy.halted = True
    approvals.clear()
    wake.stop(disable=False)  # remember the setting; resume turns it back on
    if _current and not _current.done():
        _current.cancel()
    audit("killswitch.engaged")
    bus.publish("killswitch.engaged", {})
    bus.publish("status.changed", {"state": "halted"})
    return {"ok": True, "halted": True}


@app.post("/v1/resume")
async def resume():
    policy.halted = False
    if wake.enabled:
        wake.start()
    audit("killswitch.released")
    bus.publish("status.changed", {"state": "idle"})
    return {"ok": True, "halted": False}


@app.get("/v1/connectors")
async def list_connectors():
    return await asyncio.to_thread(connectors)


def _ws_allowed(ws: WebSocket) -> bool:
    """The dashboard offers ["plag.v1", "token.<hex>"] as subprotocols, so the token never appears in a URL."""
    origin = ws.headers.get("origin")
    offered = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    sent = next((p.removeprefix("token.") for p in offered if p.startswith("token.")), "")
    return not ((origin and not ORIGIN_RE.match(origin)) or "plag.v1" not in offered or not TOKEN
                or not hmac.compare_digest(sent, TOKEN))


async def _listen_nvidia(ws: WebSocket) -> None:
    """/ws/listen through NVIDIA Parakeet (streaming): same messages as the ElevenLabs relay below."""
    try:
        session = nvhearing.session()
    except Exception as e:  # riva missing, or the channel can't be built: the dashboard uploads the recording
        log.info("NVIDIA hearing unavailable (%s)", e)
        await ws.send_json({"type": "ready", "realtime": False})
        await ws.close()
        return
    await ws.send_json({"type": "ready", "realtime": True, "by": NV_HEARING})
    started = asyncio.get_running_loop().time()

    async def relay() -> None:
        while True:
            kind, heard = await session.events.get()
            if kind == "partial":
                await ws.send_json({"type": "partial", "text": heard})
            else:
                return

    down = asyncio.create_task(relay())
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            if (pcm := m.get("bytes")) is not None:
                session.push(pcm)
                continue
            try:
                cmd = json.loads(m.get("text") or "{}").get("type")
            except ValueError:
                continue
            if cmd == "cancel":
                break
            if cmd == "end":
                t = time.perf_counter()
                heard = await session.end(3.0)
                nvhearing.health = {"ok": heard is not None, "ms": int((time.perf_counter() - t) * 1000), "at": time.time()}
                await ws.send_json({"type": "final", "ok": heard is not None, "text": heard or "", "lang": "multi",
                                    "by": NV_HEARING, "ms": nvhearing.health["ms"]})
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        session.cancel()
        down.cancel()
        audit("hearing.realtime", by="nvidia", seconds=round(session.seconds, 1))
        log.info("NVIDIA hearing session %.1f s", asyncio.get_running_loop().time() - started)
        with contextlib.suppress(Exception):
            await ws.close()


@app.websocket("/ws/listen")
async def listen(ws: WebSocket):
    """Realtime hearing. While you speak, the dashboard streams the microphone here (16 kHz 16-bit PCM, binary
    frames); PLAG relays it to ElevenLabs Scribe v2 Realtime and sends the words back as they're recognised
    ({"type": "partial"}). {"type": "end"} closes the phrase: the final text comes back ({"type": "final"}) within
    a moment. Only this command's audio is sent, never the always-on wake listening. When ElevenLabs isn't in use
    the first message is {"type": "ready", "realtime": false} and the dashboard uploads the recording instead."""
    if not _ws_allowed(ws):
        await ws.close(code=4401)
        return
    await ws.accept(subprotocol="plag.v1")
    s = app_settings.get()
    if not policy.halted and nvhearing.usable():  # NVIDIA Parakeet: free with the key, ~0.3 s, hears Hinglish best
        await _listen_nvidia(ws)
        return
    if policy.halted or not (s["eleven_hear"] and s["eleven_realtime"] and eleven.usable()):
        await ws.send_json({"type": "ready", "realtime": False})
        await ws.close()
        return
    loop = asyncio.get_running_loop()
    committed: asyncio.Future = loop.create_future()
    lock = asyncio.Lock()
    early: list[bytes] = []  # audio that arrived before ElevenLabs answered (the connection takes ~0.3-1 s)
    live: dict = {"rt": None}

    def settle(value=None, error: ElevenError | None = None) -> None:
        if not committed.done():
            committed.set_exception(error) if error else committed.set_result(value)

    async def relay_down(rt) -> None:
        """ElevenLabs -> dashboard: partial words while you speak, then the committed text."""
        try:
            while True:
                m = await rt.recv()
                kind = m.get("message_type")
                if kind == "partial_transcript":
                    await ws.send_json({"type": "partial", "text": m.get("text", "")})
                elif kind in ("committed_transcript", "committed_transcript_with_timestamps"):
                    settle({"text": (m.get("text") or "").strip(), "lang": m.get("language_code") or ""})
                elif kind in REALTIME_ERRORS:
                    settle(error=ElevenError(m.get("error") or kind, kind))
                    return
        except Exception as e:  # noqa: BLE001 - the upstream closed or broke: the dashboard falls back
            settle(error=ElevenError(str(e)[:120], "closed"))

    async def open_upstream() -> None:
        try:
            rt = await eleven.realtime()
        except ElevenError as e:
            settle(error=e)
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "ready", "realtime": False, "error": e.code})
            return
        async with lock:
            for chunk in early:
                await rt.send(chunk)
            early.clear()
            live["rt"] = rt
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "ready", "realtime": True})
        await relay_down(rt)

    up = asyncio.create_task(open_upstream())
    started = loop.time()
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            if (pcm := m.get("bytes")) is not None:
                async with lock:
                    if live["rt"] is not None:
                        await live["rt"].send(pcm)
                    elif len(early) < 250:  # ~30 s of 128 ms chunks at most
                        early.append(pcm)
                continue
            try:
                cmd = json.loads(m.get("text") or "{}").get("type")
            except ValueError:
                continue
            if cmd == "cancel":
                break
            if cmd == "end":
                for _ in range(100):  # the connection may still be opening: wait up to 3 s
                    if live["rt"] is not None or committed.done():
                        break
                    await asyncio.sleep(0.03)
                result = None
                if live["rt"] is not None and not committed.done():
                    async with lock:
                        await live["rt"].send(b"\x00" * 3200, commit=True)  # 100 ms of silence, then "that's all"
                    with contextlib.suppress(Exception):
                        result = await asyncio.wait_for(asyncio.shield(committed), 4)
                elif committed.done() and not committed.exception():
                    result = committed.result()
                await ws.send_json({"type": "final", "ok": result is not None, "text": (result or {}).get("text", ""),
                                    "lang": (result or {}).get("lang", "")})
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        up.cancel()
        if live["rt"] is not None:
            audit("hearing.realtime", seconds=round(live["rt"].seconds, 1))
            await live["rt"].close()
        if committed.done() and not committed.cancelled():
            committed.exception()  # retrieved: no "exception never retrieved" warning
        log.info("realtime hearing session %.1f s", loop.time() - started)
        with contextlib.suppress(Exception):
            await ws.close()


@app.websocket("/ws")
async def events(ws: WebSocket):
    if not _ws_allowed(ws):
        await ws.close(code=4401)
        return
    await ws.accept(subprotocol="plag.v1")
    q = bus.subscribe()
    try:
        await ws.send_json({"topic": "hello", "data": {
            "version": __version__, "state": "halted" if policy.halted else "idle",
            "connectors": await asyncio.to_thread(connectors), "wake": wake.status()}})
        if sampler.latest:
            await ws.send_json({"topic": "system.metrics", "data": sampler.latest})
        while True:
            await ws.send_json(await q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)
