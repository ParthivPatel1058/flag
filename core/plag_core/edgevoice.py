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
# how each mood sounds: % faster or slower, and a brighter or lower pitch (joy: faster and higher)
RATE = {"calm": 0, "cheerful": 2, "excited": 4, "serious": -3, "sorry": -4, "curious": 1}
PITCH = {"calm": 0, "cheerful": 1, "excited": 2, "serious": -2, "sorry": -3, "curious": 1}  # steady, professional
# the same person in the other language: English is spoken by the English voice, Hindi by the Hindi one
PAIR = {"hi-IN-MadhurNeural": "en-IN-PrabhatNeural", "en-IN-PrabhatNeural": "hi-IN-MadhurNeural",
        "hi-IN-SwaraNeural": "en-IN-NeerjaNeural", "en-IN-NeerjaNeural": "hi-IN-SwaraNeural",
        "en-IN-NeerjaExpressiveNeural": "hi-IN-SwaraNeural"}


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
        voice, hindi = s["edge_voice"], is_hindi(text)
        if hindi != voice.startswith("hi-IN"):
            voice = PAIR.get(voice, voice)  # you spoke English: the English voice answers (and the other way round)
        spoken = for_voice(text) if voice.startswith("hi-IN") and hindi else text
        pct = round((float(s["speed"]) - 1) * 100) + RATE.get(mood, 0)
        rate, pitch = f"{pct:+d}%", f"{PITCH.get(mood, 0):+d}Hz"
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / (hashlib.sha1(f"{voice}|{rate}|{pitch}|{spoken}".encode()).hexdigest()[:20] + ".mp3")
        if path.exists():
            return path.read_bytes()
        t0, audio = time.perf_counter(), bytearray()
        try:
            async for chunk in edge_tts.Communicate(spoken, voice, rate=rate, pitch=pitch).stream():
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
