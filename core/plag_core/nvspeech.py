"""PLAG's voice on NVIDIA: Magpie TTS Multilingual, voice "Leo" in Hindi and English, over NVIDIA's cloud (gRPC).

Streamed (stream()): the first sound is ready ~0.27 s after the request (measured 2026-09-24, both languages), so
PLAG starts talking almost at once and the rest arrives while it speaks. English uses the same Leo, so a reply that
switches language keeps one voice; Leo's English has moods (calm, sad, neutral), Hindi has one.

Measured 2026-09-24 with the user's key: ~0.5-0.7 s a phrase (2 s for the first), and a Whisper round-trip hears
the words back. Chatterbox Multilingual (also hi-IN) was 2-3 s a phrase; Magpie's other Hindi voices are Siwei,
Sofia and Pascal ("Aria" doesn't exist on the server). Hinglish is turned into Devanagari first (hinglish.py):
in Latin letters the voice can't read it.

Key: Windows Credential Manager, PLAG / nvidia_speech_api_key. Every phrase is cached on disk, so a repeated reply
costs nothing. After an error the voice rests for a minute (an hour for a refused key) and the next voice speaks.
"""

import hashlib
import io
import threading
import time
import wave

from .config import TTS_CACHE_DIR
from .hinglish import for_voice, is_hindi
from .secrets import get_secret

SERVER = "grpc.nvcf.nvidia.com:443"
FUNCTION_ID = "877104f7-e885-42b9-8de8-f6e4c6303969"  # ai-magpie-tts-multilingual on NVCF
VOICE = "Magpie-Multilingual.HI-IN.Leo"
EN_VOICE = "Magpie-Multilingual.EN-US.Leo"
EN_MOODS = {"calm": "Calm", "sorry": "Sad", "serious": "Neutral"}  # Leo has no happy voice: cheerful stays plain Leo
RATE = 22050
MAX_CHARS = 600  # per request; longer text is spoken sentence by sentence
CACHE = TTS_CACHE_DIR / "nvidia"


class NvSpeechError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class NvSpeech:
    def __init__(self) -> None:
        self._tts = None  # the gRPC client is only built (and grpc only imported) when Hindi is first spoken
        self._lock = threading.Lock()
        self._rest_until = 0.0
        self.last_error: str | None = None
        self.health: dict | None = None

    @staticmethod
    def configured() -> bool:
        return bool(get_secret("nvidia_speech_api_key"))

    def usable(self) -> bool:
        return self.configured() and time.time() >= self._rest_until

    def _client(self):
        if self._tts is None:
            import riva.client  # ~40 MB with grpc: loaded on first use, not at start
            auth = riva.client.Auth(uri=SERVER, use_ssl=True, metadata_args=[
                ["function-id", FUNCTION_ID], ["authorization", f"Bearer {get_secret('nvidia_speech_api_key')}"]])
            self._tts = riva.client.SpeechSynthesisService(auth)
        return self._tts

    def _synth(self, text: str, voice: str = VOICE, lang: str = "hi-IN") -> bytes:
        import riva.client
        return self._client().synthesize(text, voice_name=voice, language_code=lang, sample_rate_hz=RATE,
                                         encoding=riva.client.AudioEncoding.LINEAR_PCM).audio

    def _synth_stream(self, text: str, voice: str, lang: str):
        import riva.client
        for resp in self._client().synthesize_online(text, voice_name=voice, language_code=lang, sample_rate_hz=RATE,
                                                     encoding=riva.client.AudioEncoding.LINEAR_PCM):
            yield resp.audio

    @staticmethod
    def plan(text: str, mood: str = "calm") -> tuple[str, str, str]:
        """(voice, language, the words as spoken): Hindi and Hinglish in Devanagari, English as written."""
        text = text.strip()
        if is_hindi(text):
            return VOICE, "hi-IN", for_voice(text)
        mood_voice = EN_MOODS.get(mood)
        return (f"{EN_VOICE}.{mood_voice}" if mood_voice else EN_VOICE), "en-US", text

    @staticmethod
    def cache_path(voice: str, spoken: str):
        CACHE.mkdir(parents=True, exist_ok=True)
        return CACHE / (hashlib.sha1(f"{voice}|{spoken}".encode()).hexdigest()[:20] + ".wav")

    @staticmethod
    def _parts(spoken: str) -> list[str]:
        """Sentences, joined up to MAX_CHARS: the first one is voiced (and heard) first."""
        parts, cur = [], ""
        for sentence in spoken.replace("।", "।\n").replace(". ", ".\n").replace("? ", "?\n").replace("! ", "!\n").splitlines():
            if cur and len(cur) + len(sentence) > MAX_CHARS:
                parts.append(cur)
                cur = ""
            cur = f"{cur} {sentence}".strip()
        parts.append(cur)
        return [p for p in parts if p]

    def _failed(self, e: Exception) -> NvSpeechError:
        code = getattr(e, "code", lambda: None)()
        name = getattr(code, "name", "") or type(e).__name__
        self.last_error = name
        self._rest_until = time.time() + (3600 if name in ("UNAUTHENTICATED", "PERMISSION_DENIED") else 60)
        self._tts = None  # a broken channel is rebuilt next time
        return NvSpeechError(f"NVIDIA voice failed ({name}).", name)

    @staticmethod
    def wav(pcm: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        return buf.getvalue()

    def speak(self, text: str, mood: str = "calm") -> bytes:
        """WAV bytes of `text` (Hindi, Hinglish or English) in Leo's voice. Blocking: call it from a thread."""
        if not self.usable():
            raise NvSpeechError("The NVIDIA voice is resting or has no key.", "unavailable")
        voice, lang, spoken = self.plan(text, mood)
        path = self.cache_path(voice, spoken)
        if path.exists():
            return path.read_bytes()
        t0 = time.perf_counter()
        try:
            with self._lock:  # one gRPC call at a time on the shared channel
                pcm = b"".join(self._synth(p, voice, lang) for p in self._parts(spoken))
        except Exception as e:  # grpc.RpcError and connection errors
            raise self._failed(e) from e
        wav = self.wav(pcm)
        path.write_bytes(wav)
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time()}
        self.last_error = None
        return wav

    def stream(self, text: str, mood: str = "calm", stop=None):
        """Raw 16-bit PCM at RATE, piece by piece as NVIDIA makes it (blocking generator: run it in a thread).
        `stop` (a threading.Event) ends it early, e.g. when you interrupt PLAG. A finished reply is cached."""
        if not self.usable():
            raise NvSpeechError("The NVIDIA voice is resting or has no key.", "unavailable")
        voice, lang, spoken = self.plan(text, mood)
        t0, got, first = time.perf_counter(), [], None
        try:
            for part in self._parts(spoken):
                for pcm in self._synth_stream(part, voice, lang):
                    if stop is not None and stop.is_set():
                        return
                    if first is None:
                        first = int((time.perf_counter() - t0) * 1000)
                    got.append(pcm)
                    yield pcm
        except Exception as e:  # grpc.RpcError and connection errors
            raise self._failed(e) from e
        pcm = b"".join(got)
        if len(pcm) >= RATE // 5:  # at least 0.1 s: never cache a cut-off reply
            self.cache_path(voice, spoken).write_bytes(self.wav(pcm))
        self.health = {"ok": True, "ms": first or 0, "at": time.time(), "streamed": True}
        self.last_error = None


nvspeech = NvSpeech()
