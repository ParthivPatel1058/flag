"""PLAG's natural voice: Kokoro (the engine OpenJarvis uses), fully offline, ~3x faster than real time on this CPU.

English uses a British male voice, Hindi (Devanagari) a Hindi male voice. Mood sets the pace.
"""

import gc
import io
import re
import threading
import time
import wave
from collections import OrderedDict

import numpy as np

from .config import MODELS_DIR

MODEL_DIR = MODELS_DIR / "kokoro"
MODEL = MODEL_DIR / "kokoro-v1.0.onnx"
VOICES = MODEL_DIR / "voices-v1.0.bin"
VOICE = {"en": ("bm_george", "en-gb"), "hi": ("hm_omega", "hi")}
PACE = {"calm": 1.02, "cheerful": 1.1, "excited": 1.15, "serious": 0.97, "sorry": 0.95, "curious": 1.05}
WARM_PHRASES = [("Yes sir?", "calm"), ("जी सर?", "calm")]

_engine = None
_used = 0.0  # when the voice last spoke: with ElevenLabs as PLAG's voice, an idle Kokoro is freed (~350 MB)
_lock = threading.Lock()
_cache: "OrderedDict[tuple[str, str], bytes]" = OrderedDict()


def available() -> bool:
    if not (MODEL.exists() and VOICES.exists()):
        return False
    try:
        import kokoro_onnx  # noqa: F401
        return True
    except ImportError:
        return False


def _load():
    global _engine
    if _engine is None:
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        so = ort.SessionOptions()
        so.intra_op_num_threads = 8  # measured fastest on this laptop
        _engine = Kokoro.from_session(ort.InferenceSession(str(MODEL), so, providers=["CPUExecutionProvider"]), str(VOICES))
    return _engine


def unload_idle(idle_s: float) -> bool:
    """Free the voice model when it hasn't spoken for `idle_s` (it reloads in ~1.5 s if it's needed again)."""
    global _engine
    with _lock:
        if _engine is None or time.monotonic() - _used < idle_s:
            return False
        _engine = None
    gc.collect()
    return True


def speak(text: str, mood: str = "calm") -> bytes:
    """WAV bytes for `text`. Recent phrases are cached, so repeats are instant."""
    global _used
    key = (text.strip(), mood)
    with _lock:
        _used = time.monotonic()
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
        voice, lang = VOICE["hi"] if re.search(r"[ऀ-ॿ]", text) else VOICE["en"]
        samples, rate = _load().create(text.strip(), voice=voice, speed=PACE.get(mood, 1.02), lang=lang)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
        wav = buf.getvalue()
        _cache[key] = wav
        if len(_cache) > 32:  # recent phrases only: each is ~100-300 KB of audio kept in memory
            _cache.popitem(last=False)
        return wav


def warm() -> None:
    """Load the voice and pre-make "Yes sir?" so the wake word answers instantly."""
    if not available():
        return
    for text, mood in WARM_PHRASES:
        try:
            speak(text, mood)
        except Exception:
            return
