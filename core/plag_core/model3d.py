"""3D models: Microsoft TRELLIS on NVIDIA's API turns a short description into a textured 3D model (GLB).

Measured 2026-09-24 with the user's key: through the ai.api.nvidia.com address, "a wooden chair" took ~10 s once,
then later requests hung 90 s or ended in 504s. Calling the same NVIDIA function directly (NVCF "ai-trellis") with
NVIDIA's ask-again protocol (202 + status polling) worked in ~19 s while the other address was failing, so PLAG
uses that, with 12 sampling steps (16 took 36 s; 25 once ran 92 s and failed).
NVIDIA's hosted TRELLIS takes text only (its image mode accepts NVIDIA's example images, not uploads) and the
description must fit in 77 characters. A model is shown in the dashboard's viewer and stays a draft in memory until
you say "save" (drafts.py): then it goes to Documents\\PLAG\\3D. Windows opens .glb files in 3D Viewer.

Key: Windows Credential Manager, PLAG / nvidia_trellis_api_key.
"""

import asyncio
import base64
import io
import json
import random
import zipfile
import re
import time
from datetime import datetime

import httpx

from . import drafts
from .research import _documents
from .secrets import get_secret

NVCF = "https://api.nvcf.nvidia.com/v2/nvcf"
KNOWN_FUNCTION = "7c3ba6c7-1664-4486-a611-bd4475c98d92"  # "ai-trellis", as listed for this key on 2026-09-24
BUDGET_S = 180  # NVIDIA's queue can be slow: wait up to 3 minutes, asking every ~10 s
OUT_DIR = _documents() / "PLAG" / "3D"
_function: str | None = None
MAX_PROMPT = 77  # NVIDIA's limit for text mode
health: dict | None = None  # last result, for the Connections panel


class ModelError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _key() -> str | None:
    return get_secret("nvidia_trellis_api_key")


def ready() -> bool:
    return bool(_key())


def short_prompt(prompt: str) -> str:
    """ "a 3D model of a red toy robot with big eyes" -> "a red toy robot with big eyes", cut at a word to 77 chars."""
    p = " ".join(prompt.split()).strip(" .!?")
    p = re.sub(r"^(?:a\s+|an\s+)?3d\s+(?:model|object|asset|version)\s+(?:of|for)\s+", "", p, flags=re.I)
    if len(p) <= MAX_PROMPT:
        return p
    cut = p[:MAX_PROMPT]
    return cut[:cut.rfind(" ")] if " " in cut else cut


def _slug(prompt: str) -> str:
    words = re.sub(r"[^\w\s-]", "", prompt.casefold()).split()[:6]
    return "-".join(words)[:48] or "model"


async def _function_id(http: httpx.AsyncClient, key: str) -> str:
    """The NVIDIA function behind TRELLIS, found once from the functions this key can call."""
    global _function
    if _function:
        return _function
    try:
        r = await http.get(f"{NVCF}/functions", headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200:
            for f in r.json().get("functions", []):
                if f.get("name") == "ai-trellis" and f.get("status") == "ACTIVE":
                    _function = f["id"]
                    return _function
    except (httpx.HTTPError, ValueError):
        pass
    return KNOWN_FUNCTION


def _json_of(r: httpx.Response) -> dict:
    """The answer as JSON: straight, or inside the zip NVIDIA sends for large results."""
    if r.status_code != 200 or not r.content:
        return {}
    if r.content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = next((n for n in z.namelist() if n.endswith((".response", ".json"))), None)
            return json.loads(z.read(name)) if name else {}
    try:
        return r.json()
    except ValueError:
        return {}


async def generate(prompt: str) -> dict:
    """Build a 3D model of `prompt`; returns {id, path, prompt, ms, bytes}. Raises ModelError the user can act on."""
    global health
    key = _key()
    if not key:
        raise ModelError("The 3D key is missing. Save it in Windows Credential Manager as PLAG / nvidia_trellis_api_key.", "no_key")
    prompt = short_prompt(prompt)
    if not prompt:
        raise ModelError("Tell me what to build, like “a 3D model of a wooden chair”.", "empty")
    t0 = time.perf_counter()
    auth = {"Authorization": f"Bearer {key}", "Accept": "application/json", "NVCF-POLL-SECONDS": "10"}

    async def attempt(http: httpx.AsyncClient, fid: str) -> httpx.Response:
        body = {"mode": "text", "prompt": prompt, "seed": random.randint(1, 2**31 - 1), "output_format": "glb",
                "slat_sampling_steps": 12, "ss_sampling_steps": 12}
        r = await http.post(f"{NVCF}/pexec/functions/{fid}", json=body, headers=auth)
        while r.status_code == 202 and time.perf_counter() - t0 < BUDGET_S:  # still building: ask again
            r = await http.get(f"{NVCF}/pexec/status/{r.headers.get('nvcf-reqid', '')}", headers=auth)
        if r.status_code == 302 and r.headers.get("location"):  # a big result comes as a download
            r = await http.get(r.headers["location"])
        return r

    # Measured 2026-09-24: a good request finishes in ~10-20 s, but that afternoon over half hung ~90 s and then
    # failed with a 500. So two start together, a third joins if neither is done after 30 s, and the first finished
    # model wins (the rest are cancelled). Costs a little more of NVIDIA's credit; turns a coin toss into a model.
    r: httpx.Response | None = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=8.0)) as http:
            fid = await _function_id(http, key)
            tasks = [asyncio.create_task(attempt(http, fid)) for _ in range(2)]
            done, _ = await asyncio.wait(tasks, timeout=30)
            if not any(t.done() and not t.exception() and t.result().status_code == 200 for t in done):
                tasks.append(asyncio.create_task(attempt(http, fid)))
            try:
                for finished in asyncio.as_completed(tasks):
                    try:
                        r = await finished
                    except httpx.HTTPError:
                        continue
                    if r.status_code == 200:
                        break
            finally:
                for t in tasks:
                    t.cancel()
    except httpx.TransportError as e:
        health = {"ok": False, "ms": 0, "error": "offline"}
        raise ModelError("Can't reach NVIDIA's 3D service. Check the internet connection.", "offline") from e
    ms = int((time.perf_counter() - t0) * 1000)
    if r is None:
        health = {"ok": False, "ms": ms, "error": "timeout"}
        raise ModelError("NVIDIA's 3D service took too long. Try again in a minute.", "timeout")
    if r.status_code == 202:
        health = {"ok": False, "ms": ms, "error": "timeout"}
        raise ModelError("NVIDIA's 3D queue is very slow right now (over 3 minutes). Try again later.", "timeout")
    data = _json_of(r)
    if r.status_code != 200:
        health = {"ok": False, "ms": ms, "error": str(r.status_code)}
        message = {401: "NVIDIA rejected the 3D key. Check PLAG / nvidia_trellis_api_key.",
                   403: "This NVIDIA key isn't allowed to use TRELLIS.",
                   402: "The NVIDIA account is out of credits.",
                   429: "Too many 3D requests right now. Try again in a minute.",
                   422: "NVIDIA couldn't use that description. Try describing it differently."}.get(
            r.status_code, f"NVIDIA's 3D service returned an error ({r.status_code}). Try again in a minute.")
        raise ModelError(message, str(r.status_code))
    art = (data.get("artifacts") or [{}])[0]
    if art.get("finishReason") == "CONTENT_FILTERED" or not art.get("base64"):
        health = {"ok": False, "ms": ms, "error": "filtered"}
        raise ModelError("NVIDIA's safety filter blocked that model. Try a different description.", "filtered")
    glb = base64.b64decode(art["base64"])
    if glb[:4] != b"glTF":
        health = {"ok": False, "ms": ms, "error": "bad_output"}
        raise ModelError("NVIDIA sent back something that isn't a 3D model. Try again.", "bad_output")
    # a draft until you say "save" (then it goes to Documents\PLAG\3D): nothing is written to your laptop before that
    d = drafts.add("3d", prompt, glb, OUT_DIR, f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{_slug(prompt)}.glb")
    health = {"ok": True, "ms": ms, "error": None}
    return {"id": d.id, "path": "", "prompt": prompt, "ms": ms, "bytes": len(glb), "draft": True}
