"""ElevenLabs realtime hearing and streamed voice, checked end to end against a local stand-in for ElevenLabs.

The stand-in speaks the documented protocol (checked 2026-09-24): WSS /v1/speech-to-text/realtime answers
session_started, partial_transcript while audio arrives and committed_transcript on commit; POST
/v1/text-to-speech/{voice}/stream sends chunked 24 kHz PCM. PLAG's core runs for real in dry-run mode with a
made-up key held in memory, so this never touches your ElevenLabs account, credits or Credential Manager.

    cd core && .venv\\Scripts\\python tests/check_realtime.py
"""

import asyncio
import base64
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
os.environ["PLAG_ELEVEN_API"] = f"http://127.0.0.1:{PORT}"  # before plag_core is imported
os.environ["PLAG_DRY_RUN"] = "1"
os.environ["PLAG_TOKEN"] = "realtime-token"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

seen: dict = {"auth": [], "audio": 0, "commits": 0, "mode": "ok", "tts": []}
mock = FastAPI()


@mock.get("/v1/voices")
async def voices():
    return {"voices": [{"voice_id": "v1", "name": "Test Voice", "category": "premade", "labels": {}}]}


@mock.get("/v1/user/subscription")
async def subscription():
    return {"character_count": 100, "character_limit": 100000, "next_character_count_reset_unix": 0}


@mock.post("/v1/text-to-speech/{voice}/stream")
async def tts_stream(voice: str, request: Request):
    if voice == "library1":  # what ElevenLabs answers on a free plan for a voice-library voice (2026-09-24)
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": {"type": "payment_required", "code": "paid_plan_required", "status": "payment_required",
                                        "message": "Free users cannot use library voices via the API."}}, status_code=402)
    if voice == "broke1":  # the month's credits are gone (the user's account, 2026-09-24)
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": {"type": "invalid_request", "code": "quota_exceeded", "status": "quota_exceeded",
                                        "message": "This request exceeds your quota of 10000. You have 0 credits remaining, "
                                                   "while 2 credits are required for this request."}}, status_code=401)
    seen["tts"].append({"voice": voice, "key": request.headers.get("xi-api-key"), "format": request.query_params.get("output_format"),
                        **(await request.json())})

    async def pcm():
        for _ in range(6):  # 6 x 0.1 s of 24 kHz 16-bit audio, arriving over 0.3 s like a real stream
            await asyncio.sleep(0.05)
            yield b"\x10\x00" * 2400
    return StreamingResponse(pcm(), media_type="application/octet-stream")


@mock.websocket("/v1/speech-to-text/realtime")
async def realtime(ws: WebSocket):
    seen["query"] = dict(ws.query_params)
    seen["auth"].append(ws.headers.get("xi-api-key"))
    await ws.accept()
    await ws.send_json({"message_type": "session_started", "session_id": "s1", "config": {}})
    partials = ["open", "open YouTube"]
    while True:
        try:
            m = json.loads(await ws.receive_text())
        except Exception:  # noqa: BLE001 - the core closed the session
            return
        seen["audio"] += len(base64.b64decode(m["audio_base_64"]))
        if seen["mode"] == "silent" and m.get("commit"):
            await ws.send_json({"message_type": "insufficient_audio_activity", "error": "No speech detected"})
        elif m.get("commit"):
            seen["commits"] += 1
            await ws.send_json({"message_type": "committed_transcript", "text": "PLAG, open YouTube."})
        elif partials:
            await ws.send_json({"message_type": "partial_transcript", "text": partials.pop(0)})


server = uvicorn.Server(uvicorn.Config(mock, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)

from fastapi.testclient import TestClient  # noqa: E402

from plag_core import elevenlabs, memory, settings  # noqa: E402

memory.DB_PATH = Path(tempfile.mkdtemp()) / "plag.db"
settings.PATH = Path(tempfile.mkdtemp()) / "settings.json"
settings._cache = None
elevenlabs.TTS_CACHE_DIR = Path(tempfile.mkdtemp())  # streamed replies are cached here, not in your real cache
elevenlabs.ElevenLabs.key = staticmethod(lambda: "test-key")  # in memory only
elevenlabs.STATE = Path(tempfile.mkdtemp()) / "elevenlabs_state.json"  # "out of credits" is remembered here, not in yours
elevenlabs.eleven._quota_out_at = 0.0

from plag_core.api import app  # noqa: E402
from plag_core.edgevoice import edgevoice  # noqa: E402
from plag_core.nvasr import nvhearing  # noqa: E402
from plag_core.nvspeech import nvspeech  # noqa: E402
from plag_core.sarvam import sarvam  # noqa: E402

# this suite is about the ElevenLabs paths: NVIDIA's hearing and voice, Sarvam and Edge stay out of it (no network)
for _other in (nvhearing, nvspeech, edgevoice, sarvam):
    _other.usable = lambda: False

H = {"X-PLAG-Token": "realtime-token"}
passed, failed = 0, []


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed
    if ok:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}  {detail}")


with TestClient(app) as c:
    print("realtime hearing (Scribe v2 Realtime stand-in)")
    got: list[dict] = []
    t0 = time.perf_counter()
    with c.websocket_connect("/ws/listen", subprotocols=["plag.v1", "token.realtime-token"]) as ws:
        for _ in range(5):
            ws.send_bytes(b"\x01\x00" * 2048)  # 128 ms of 16 kHz audio, like the dashboard's microphone chunks
        while not any(m.get("type") == "partial" and m.get("text") == "open YouTube" for m in got):
            got.append(ws.receive_json())
        t_end = time.perf_counter()
        ws.send_json({"type": "end"})
        while not got or got[-1].get("type") != "final":
            got.append(ws.receive_json())
        final_ms = (time.perf_counter() - t_end) * 1000
    check("the core says realtime hearing is on", {"type": "ready", "realtime": True} in got, str(got[:2]))
    check("words come back while you speak", [m["text"] for m in got if m["type"] == "partial"] == ["open", "open YouTube"], str(got))
    final = got[-1]
    check("the final words arrive right after you stop", final.get("ok") is True and final.get("text") == "PLAG, open YouTube."
          and final_ms < 1500, f"{final} in {final_ms:.0f} ms")
    q = seen.get("query", {})
    check("asks for Scribe v2 Realtime, 16 kHz PCM, PLAG decides when a phrase ends",
          q.get("model_id") == "scribe_v2_realtime" and q.get("audio_format") == "pcm_16000" and q.get("commit_strategy") == "manual", str(q))
    check("your key goes in the header, never the URL", seen["auth"] == ["test-key"] and "test-key" not in json.dumps(q))
    check("every chunk of audio was relayed, then one commit", seen["audio"] >= 5 * 4096 and seen["commits"] == 1,
          f"{seen['audio']} bytes, {seen['commits']} commits")
    res = c.post("/v1/turn/text", headers=H, json={"text": final["text"], "heard_by": "ElevenLabs realtime"}).json()
    check("the realtime words run as a spoken command", res.get("action", {}).get("type") == "open_url"
          and res.get("transcript") == "open YouTube" and res.get("model") == "elevenlabs realtime", str(res)[:200])
    print(f"        (final words {final_ms:.0f} ms after 'end'; whole session {(time.perf_counter() - t0) * 1000:.0f} ms)")

    seen["mode"] = "silent"
    with c.websocket_connect("/ws/listen", subprotocols=["plag.v1", "token.realtime-token"]) as ws:
        ws.receive_json()  # ready
        ws.send_bytes(b"\x00\x00" * 2048)
        ws.send_json({"type": "end"})
        msg = ws.receive_json()
        while msg.get("type") != "final":
            msg = ws.receive_json()
    check("nothing heard: the dashboard is told to use its recording instead", msg.get("ok") is False and msg.get("text") == "", str(msg))

    print("streamed voice (text-to-speech stream stand-in)")
    text = "Done. YouTube results for honey singh are open."
    t0 = time.perf_counter()
    with c.stream("POST", "/v1/tts/stream", headers=H, json={"text": text, "mood": "cheerful"}) as r:
        first = None
        body = b""
        for chunk in r.iter_bytes():
            first = first or (time.perf_counter() - t0) * 1000
            body += chunk
        total = (time.perf_counter() - t0) * 1000
        kind = r.headers.get("content-type", "")
    check("streams raw 24 kHz PCM", r.status_code == 200 and "audio/l16" in kind.lower() and "24000" in kind and len(body) == 6 * 4800,
          f"{kind} / {len(body)} bytes")
    sent = seen["tts"][-1]
    check("ElevenLabs is asked for the stream with your voice and model", sent["voice"] == "v1" and sent["format"] == "pcm_24000"
          and sent["model_id"] == "eleven_flash_v2_5" and sent["text"] == text and sent["key"] == "test-key", str(sent)[:200])
    again = c.post("/v1/tts/stream", headers=H, json={"text": text, "mood": "cheerful"})
    check("said again: the cached recording, no new ElevenLabs call", again.status_code == 200
          and again.headers.get("content-type") == "audio/wav" and again.content[:4] == b"RIFF" and len(seen["tts"]) == 1,
          f"{again.headers.get('content-type')} / {len(seen['tts'])} calls")
    settings.update({"eleven_model": "eleven_v3_conversational"})
    c.post("/v1/tts/stream", headers=H, json={"text": "That worked!", "mood": "excited"}).close()
    check("the Expressive voice gets the mood as an audio tag", seen["tts"][-1]["text"] == "[excited] That worked!", seen["tts"][-1]["text"])
    settings.update({"eleven_model": "eleven_flash_v2_5"})
    long_reply = "word " * 190
    r = c.post("/v1/tts/stream", headers=H, json={"text": long_reply.strip(), "mood": "calm"})
    check("long replies are spoken by ElevenLabs too (every reply, by default)", r.status_code == 200, str(r.status_code))
    settings.update({"eleven_voice_id": "library1"})
    r = c.post("/v1/tts/stream", headers=H, json={"text": "A voice from the library.", "mood": "calm"})
    check("a voice that needs a paid plan: a built-in voice speaks instead, no pause", r.status_code == 200
          and seen["tts"][-1]["voice"] == "v1" and elevenlabs.eleven.usable(), f"{r.status_code} / {seen['tts'][-1]['voice']}")
    settings.update({"eleven_voice_id": "broke1"})
    kept = elevenlabs.eleven._usage
    c.post("/v1/tts/stream", headers=H, json={"text": "No credits left.", "mood": "calm"})
    row = next(x for x in c.get("/v1/connectors", headers=H).json() if x["id"] == "elevenlabs")
    check("credits used up: the dashboard says so, and the local voice takes over", not elevenlabs.eleven.usable()
          and "used up (0 of 10,000 left)" in row["detail"], row["detail"])
    elevenlabs.eleven._usage, elevenlabs.eleven._rest_until, elevenlabs.eleven.last_error = kept, 0, None
    check("out of credits is remembered across restarts", elevenlabs.STATE.exists() and elevenlabs.eleven._quota_out_at > 0)
    elevenlabs.eleven._out_of_credits(False)
    settings.update({"eleven_voice_id": ""})
    settings.update({"eleven_hear": False})
    with c.websocket_connect("/ws/listen", subprotocols=["plag.v1", "token.realtime-token"]) as ws:
        check("hearing turned off in Settings: no realtime session", ws.receive_json() == {"type": "ready", "realtime": False})

print("streamed voice through a real server (the test client above collects whole responses)")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    CORE = s.getsockname()[1]
import httpx  # noqa: E402

elevenlabs.eleven._http = httpx.AsyncClient(base_url=elevenlabs.API)  # the test client's shutdown closed the first one
core = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=CORE, log_level="warning"))
threading.Thread(target=core.run, daemon=True).start()
while not core.started:
    time.sleep(0.05)
import httpx  # noqa: E402

t0 = time.perf_counter()
first = None
body = b""
with httpx.stream("POST", f"http://127.0.0.1:{CORE}/v1/tts/stream", headers=H, json={"text": "A brand new reply.", "mood": "calm"}) as r:
    for chunk in r.iter_bytes():
        first = first or (time.perf_counter() - t0) * 1000
        body += chunk
total = (time.perf_counter() - t0) * 1000
check("the first sound arrives while ElevenLabs is still speaking", first is not None and first < total - 150 and len(body) == 6 * 4800,
      f"first bytes {first:.0f} ms, all {total:.0f} ms, {len(body)} bytes")
print(f"        (first audio {first:.0f} ms, whole reply {total:.0f} ms, through PLAG's core)")
core.should_exit = True
server.should_exit = True
time.sleep(0.3)
print(f"\n{passed} passed, {len(failed)} failed" + (f": {failed}" if failed else ""))
sys.exit(1 if failed else 0)
