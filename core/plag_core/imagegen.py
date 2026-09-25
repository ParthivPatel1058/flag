"""Image generation: FLUX.1-dev on NVIDIA's API. Every image is saved to Pictures\\PLAG and shown on the dashboard.

Measured 2026-09-24 with the user's key: flux.1-dev answers in ~6 s; flux.1-schnell sat in NVIDIA's queue past 90 s;
the Stable Diffusion endpoints aren't enabled for this account (404). NVIDIA's hosted image-editing models (FLUX.1
Kontext, flux.1-dev depth/canny) only accept NVIDIA's own example images, so "an image of this" (the camera) works by
having Gemini describe the frame and FLUX drawing from that description: a re-creation, not an edit.

Key: Windows Credential Manager, PLAG / nvidia_image_api_key (falls back to nvidia_api_key).
"""

import base64
import ctypes
import os
import random
import re
import time
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import httpx

from .secrets import get_secret

MODEL = "black-forest-labs/flux.1-dev"
URL = "https://ai.api.nvidia.com/v1/genai/" + MODEL


def _pictures() -> Path:
    """The real Pictures folder (it's often moved into OneDrive), from Windows itself."""
    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
    folder_id = GUID(0x33E28130, 0x4E1E, 0x4676, (ctypes.c_ubyte * 8)(0x83, 0x5A, 0x98, 0x39, 0x5C, 0x3B, 0xC3, 0xBB))
    out = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
            path = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except (AttributeError, OSError):
        pass
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Pictures"


OUT_DIR = _pictures() / "PLAG"
SIZES = {"1:1": (1024, 1024), "16:9": (1344, 768), "9:16": (768, 1344)}
health: dict | None = None  # last result, for the Connections panel
_images: dict[str, Path] = {}  # id -> file, for the dashboard to fetch


class ImageError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _key() -> str | None:
    return get_secret("nvidia_image_api_key") or get_secret("nvidia_api_key")


def ready() -> bool:
    return bool(_key())


def path_of(image_id: str) -> Path | None:
    p = _images.get(image_id)
    return p if p and p.exists() else None


def _slug(prompt: str) -> str:
    words = re.sub(r"[^\w\s-]", "", prompt.casefold()).split()[:6]
    return "-".join(words)[:48] or "image"


async def generate(prompt: str, aspect: str = "1:1") -> dict:
    """Draw `prompt`; returns {id, path, prompt, ms}. Raises ImageError with something the user can act on."""
    global health
    key = _key()
    if not key:
        raise ImageError("The image key is missing. Save it in Windows Credential Manager as PLAG / nvidia_image_api_key.", "no_key")
    prompt = " ".join(prompt.split())[:900]
    width, height = SIZES.get(aspect, SIZES["1:1"])
    body = {"prompt": prompt, "mode": "base", "cfg_scale": 3.5, "width": width, "height": height,
            "seed": random.randint(1, 2**31 - 1), "steps": 30}
    t0 = time.perf_counter()
    r = None
    # FLUX usually answers in ~6 s. On 2026-09-24 one request hung 61 s and ended in a server error while the next
    # took 6 s: so a stuck or failed first try gets one quick retry instead of a long wait.
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(35.0 if attempt == 1 else 60.0, connect=8.0)) as http:
                r = await http.post(URL, json=body, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        except httpx.TimeoutException as e:
            if attempt == 1:
                continue
            health = {"ok": False, "ms": int((time.perf_counter() - t0) * 1000), "error": "timeout"}
            raise ImageError("NVIDIA's image service took too long. Try again in a minute.", "timeout") from e
        except httpx.TransportError as e:
            health = {"ok": False, "ms": 0, "error": "offline"}
            raise ImageError("Can't reach NVIDIA's image service. Check the internet connection.", "offline") from e
        if r.status_code < 500:
            break
    assert r is not None
    ms = int((time.perf_counter() - t0) * 1000)
    data = r.json() if r.content and "json" in r.headers.get("content-type", "") else {}
    if r.status_code != 200:
        health = {"ok": False, "ms": ms, "error": str(r.status_code)}
        message = {401: "NVIDIA rejected the image key. Check PLAG / nvidia_image_api_key.",
                   403: "This NVIDIA key isn't allowed to use FLUX.1-dev.",
                   402: "The NVIDIA account is out of credits.",
                   429: "Too many image requests right now. Try again in a minute.",
                   422: "NVIDIA couldn't use that description. Try describing it differently."}.get(r.status_code,
                                                                                                    f"NVIDIA's image service returned an error ({r.status_code}).")
        raise ImageError(message, str(r.status_code))
    art = (data.get("artifacts") or [{}])[0]
    if art.get("finishReason") == "CONTENT_FILTERED" or not art.get("base64"):
        health = {"ok": False, "ms": ms, "error": "filtered"}
        raise ImageError("NVIDIA's safety filter blocked that image. Try a different description.", "filtered")
    raw = base64.b64decode(art["base64"])
    ext = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{_slug(prompt)}{ext}"
    path.write_bytes(raw)
    image_id = uuid.uuid4().hex[:10]
    _images[image_id] = path
    health = {"ok": True, "ms": ms, "error": None}
    return {"id": image_id, "path": str(path), "prompt": prompt, "ms": ms}
