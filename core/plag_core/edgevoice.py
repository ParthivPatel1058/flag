"""PLAG's free voice: Microsoft Edge's online neural voices (the ones Edge's "Read aloud" uses), through edge-tts,
the library openai-edge-tts is built on (used directly: no extra server running, no OpenAI key, no account).

Indian voices: Madhur (Hindi, male), Prabhat (Indian English, male), Swara and Neerja (female). Measured
2026-09-25: first sound after ~0.9-1.4 s, a sentence done in ~1.3-1.9 s. A Hindi voice gets Hinglish with its Hindi
words in Devanagari; an Indian-English voice reads it as written. Every phrase is cached on disk (MP3).
"""

import hashlib
import time

from . import settings
from .config import TTS_CACHE_DIR
from .hinglish import for_voice, is_hindi

CACHE = TTS_CACHE_DIR / "edge"
RATE = {"calm": 0, "cheerful": 5, "excited": 10, "serious": -3, "sorry": -5, "curious": 2}  # % faster or slower


class EdgeError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class EdgeVoice:
    def __init__(self) -> None:
        self._rest_until = 0.0
        self.last_error: str | None = None
        self.health: dict | None = None

    @staticmethod
    def available() -> bool:
        """Installed? (Looked up, not imported: nothing loads until the Edge voice first speaks.)"""
        import importlib.util
        return importlib.util.find_spec("edge_tts") is not None

    def usable(self) -> bool:
        return time.time() >= self._rest_until and self.available()

    async def speak(self, text: str, mood: str = "calm") -> bytes:
        """MP3 bytes of `text` in the chosen Edge voice."""
        if not self.usable():
            raise EdgeError("The Edge voice is resting.", "unavailable")
        import edge_tts

        s = settings.get()
        voice = s["edge_voice"]
        spoken = for_voice(text) if voice.startswith("hi-IN") and is_hindi(text) else text
        pct = round((float(s["speed"]) - 1) * 100) + RATE.get(mood, 0)
        rate = f"{pct:+d}%"
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / (hashlib.sha1(f"{voice}|{rate}|{spoken}".encode()).hexdigest()[:20] + ".mp3")
        if path.exists():
            return path.read_bytes()
        t0, audio = time.perf_counter(), bytearray()
        try:
            async for chunk in edge_tts.Communicate(spoken, voice, rate=rate).stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
        except Exception as e:  # the service is unofficial: it can change or refuse without notice
            self.last_error = type(e).__name__
            self._rest_until = time.time() + 60
            raise EdgeError(f"The Edge voice failed ({type(e).__name__}).", "failed") from e
        if not audio:
            self.last_error = "empty"
            self._rest_until = time.time() + 30
            raise EdgeError("The Edge voice sent no audio.", "empty")
        path.write_bytes(bytes(audio))
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time()}
        self.last_error = None
        return bytes(audio)


edgevoice = EdgeVoice()
