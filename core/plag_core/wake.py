"""Wake word "PLAG" (like "Hey Alexa") and on-device speech-to-text, fully offline.

How it works: the microphone stream stays on this laptop. A loudness detector cuts it into phrases; each phrase is
transcribed locally by Whisper. A phrase that *starts* with "PLAG" / "Hey PLAG" wakes PLAG. Nothing is recorded to
disk and no audio leaves the laptop unless a command then needs ElevenLabs or Gemini.

Two Whisper sizes, measured 2026-09-24 on this laptop (median of 3, Kokoro-voiced clips):
- "tiny" listens all the time: ~0.4 s per phrase, 189 MB. It hears "PLAG" reliably but mangles some names, and once
  heard "Plug in the charger" as "PLAG in the charger", so a wake with an unclear command is re-checked.
- "base" hears commands on this laptop when ElevenLabs doesn't: ~0.7 s, +~50 MB. It loads only when needed and is
  freed after 10 idle minutes; with ElevenLabs hearing your commands it never loads at all.
A short phrase ("PLAG") ends after 0.45 s of quiet instead of the full pause, so the wake answers sooner.
"""

import asyncio
import base64
import gc
import io
import logging
import queue
import re
import threading
import time
import wave
from collections import deque

import numpy as np

from .audit import audit
from .bus import bus

log = logging.getLogger("plag.wake")

RATE = 16000
BLOCK = 480  # 30 ms
# Nudges Whisper to spell the name and the words commands use (tested against look-alike phrases).
PROMPT = ("Assistant name: PLAG. PLAG, open YouTube, Google, WhatsApp, Gmail. Search, play, weather, lecture, class 10th, "
          "kholo, chalao.")
MAX_PHRASE_S = 12.0  # a long command ("…class 10th lecture on YouTube") isn't cut off
SHORT_PHRASE_S, SHORT_PAUSE_S = 1.3, 0.45  # "PLAG" on its own ends quickly
LISTEN_MODEL, COMMAND_MODEL = "tiny", "base"
# "flag" too: it's how the user says it, and how speech-to-text often spells "PLAG" (2026-09-24)
EXACT_WAKE = {"plag", "plagg", "plaag", "flag", "flagg"}  # look-alikes ("plug", "plague") need a real command after them
_WAKE_WORDS = r"plag+|plaag|flag+|plague|plaque|plug|plack|plak|pleg|plog"
_WAKE = re.compile(rf"^\W*(?:(?:hey|hi|hay|he|ok|okay|hello|yo)\W+){{0,2}}(?:{_WAKE_WORDS})\b\W*", re.I)
# Interrupting PLAG while it talks: "stop", "PLAG stop", "ruko", "bas". While PLAG speaks, the microphone also hears
# PLAG, so the user's word usually arrives at the end of a phrase ("...the main reason is stop").
_STOP_WORDS = r"stop(?:\s+it|\s+talking)?|wait|enough|cancel|quiet|shut\s+up|ruko|ruk\s+jao|bas(?:\s+karo)?|chup(?:\s+raho)?"
_STOP_ALONE = re.compile(rf"^\W*(?:(?:hey\W+)?(?:{_WAKE_WORDS})\W+)?(?:{_STOP_WORDS})\W*$", re.I)
_STOP_TAIL = re.compile(rf"(?:^|\W)(?:(?:{_WAKE_WORDS})\W+)?(?:stop|ruko|bas)\W*$", re.I)


# "Always listening" skips these: what speech-to-text makes of coughs, music and room noise
HANDS_FREE_NOISE = {"you", "thank you", "thanks for watching", "thank you for watching", "bye", "okay", "ok", "hmm", "uh",
                    "um", "so", "the", "yeah", "oh", "ah", "huh", "music", "applause", "silence", "i", "a"}


def is_stop(text: str) -> bool:
    return bool(_STOP_ALONE.match(text or "") or _STOP_TAIL.search(text or ""))


def exact_wake(text: str) -> bool:
    """Starts with PLAG itself, not a look-alike ("plug", "plague")."""
    m = _WAKE.match(text or "")
    return bool(m) and re.sub(r"[^a-z]", "", m.group(0).split()[-1].casefold()) in EXACT_WAKE


_models: dict = {}
_used: dict[str, float] = {}
_model_lock = threading.Lock()


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def model(name: str = LISTEN_MODEL):
    """Load a Whisper size once (1-2 s from the local cache) and share it."""
    with _model_lock:
        m = _models.get(name)
        if m is None:
            from faster_whisper import WhisperModel
            try:  # the copy on this laptop, without asking HuggingFace for updates first (faster, works offline)
                m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4, local_files_only=True)
            except Exception:  # not downloaded yet: fetch it once
                m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4)
            _models[name] = m
        _used[name] = time.monotonic()
        return m


def unload_idle(idle_s: float) -> list[str]:
    """Free the command model when nothing needed it for `idle_s` (the listening model always stays)."""
    with _model_lock:
        gone = [n for n in list(_models) if n != LISTEN_MODEL and time.monotonic() - _used.get(n, 0) > idle_s]
        for n in gone:
            _models.pop(n, None)
    if gone:
        gc.collect()
    return gone


def transcribe(samples: np.ndarray, language: str | None = "en", name: str = LISTEN_MODEL) -> str:
    segs, _ = model(name).transcribe(samples, language=language, initial_prompt=PROMPT, beam_size=1,
                                     condition_on_previous_text=False, without_timestamps=True)
    return " ".join(s.text.strip() for s in segs).strip()


def wav_samples(wav: bytes) -> np.ndarray | None:
    """16 kHz mono float32 from a PCM WAV (resampled if needed)."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return None
    if width != 2:
        return None
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    if channels > 1:
        a = a.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(a):
        a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * RATE / rate)), np.arange(len(a)), a).astype(np.float32)
    return a


def transcribe_wav(wav: bytes, language: str | None = "en", name: str = COMMAND_MODEL) -> str:
    """A spoken command, heard on this laptop with the more accurate model."""
    a = wav_samples(wav)
    return transcribe(a, language, name) if a is not None and len(a) > RATE // 5 else ""


def split_wake(text: str) -> tuple[bool, str]:
    """("PLAG, open YouTube") -> (True, "open YouTube"). Only a phrase that *starts* with the name counts."""
    m = _WAKE.match(text or "")
    if not m:
        return False, ""
    return True, text[m.end():].strip(" ,.!?")


def _to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class WakeListener:
    def __init__(self) -> None:
        self.enabled = False
        self.speaking = False  # set by the dashboard while PLAG talks: phrases are cut short so "stop" lands fast
        self.always = False  # the dashboard's "Always listening" button: every phrase is for PLAG, no "PLAG" needed
        self.paused = False  # a live ElevenLabs agent conversation has the microphone: phrases are skipped
        self.quiet_since = 0.0  # when PLAG last stopped talking (its own voice mustn't become your next command)
        self.pause_s = 0.8  # silence that ends what you're saying (Settings: fast 0.55 / normal 0.8 / patient 1.2)
        self.gain = 2.4  # how far above the room's noise speech must be (Settings: high 1.9 / normal 2.4 / low 3.0)
        self.sensitivity = "normal"
        self._last_wake = 0.0
        self.state = "off"  # off | starting | listening | error
        self.apply_settings()
        self.error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._jobs: queue.Queue = queue.Queue(maxsize=4)

    def status(self) -> dict:
        return {"enabled": self.enabled, "state": self.state, "phrase": "PLAG", "error": self.error, "always": self.always}

    def apply_settings(self) -> None:
        from . import settings
        s = settings.get()
        self.always = s["always_listen"]
        self.pause_s = settings.PAUSE_S.get(s["turn"], 0.8)
        self.sensitivity = s["wake_sensitivity"]
        self.gain = {"high": 1.9, "normal": 2.4, "low": 3.0}.get(self.sensitivity, 2.4)

    def _emit(self, fn, *args) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(fn, *args)

    def _set(self, state: str, error: str | None = None) -> None:
        self.state, self.error = state, error
        bus.publish("wake.state", self.status())

    def start(self) -> None:
        self.enabled = True
        if self._thread and self._thread.is_alive():
            return
        self._loop = asyncio.get_running_loop()
        self._stop = threading.Event()
        self._set("starting")
        self._thread = threading.Thread(target=self._capture, args=(self._stop,), daemon=True, name="plag-wake")
        self._thread.start()
        threading.Thread(target=self._work, args=(self._stop,), daemon=True, name="plag-wake-stt").start()

    def stop(self, *, disable: bool = True) -> None:
        if disable:
            self.enabled = False
        self._stop.set()
        self._thread = None
        self._set("off")

    # -------------------------------------------------------- microphone -> phrases

    def _capture(self, stop: threading.Event) -> None:
        if not available():
            self._emit(self._set, "error", "Speech model not installed. Run: cd core && uv sync")
            return
        try:
            model()
            import sounddevice as sd
        except Exception as e:  # model or audio stack failed to load
            self._emit(self._set, "error", f"Couldn't load the speech model: {e}")
            return
        blocks: queue.Queue = queue.Queue(maxsize=400)

        def on_audio(indata, frames, t, status):
            try:
                blocks.put_nowait(bytes(indata))
            except queue.Full:
                pass

        try:
            with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=on_audio):
                self._emit(self._set, "listening")
                self._segment(blocks, stop)
        except Exception as e:
            log.warning("wake microphone failed: %s", e)
            self._emit(self._set, "error", "Couldn't open the microphone. Check Windows microphone privacy settings.")
            return
        if self.enabled and not stop.is_set():
            self._emit(self._set, "error", "The microphone stopped.")

    def _segment(self, blocks: queue.Queue, stop: threading.Event) -> None:
        noise = 250.0
        speaking = False
        pre: deque[bytes] = deque(maxlen=10)  # 300 ms before speech starts
        phrase: list[bytes] = []
        quiet = 0
        last_level = 0.0
        while not stop.is_set():
            try:
                block = blocks.get(timeout=0.5)
            except queue.Empty:
                continue
            a = np.frombuffer(block, np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(a * a))) if len(a) else 0.0
            now = time.monotonic()
            if now - last_level >= 0.1 and bus.has_subscribers:
                last_level = now
                self._emit(bus.publish, "wake.level", {"v": min(100, int(rms / 40))})
            threshold = max(noise * self.gain, 160.0)  # sensitive enough for a soft "PLAG" on a quiet laptop mic
            if not speaking:
                pre.append(block)
                if rms > threshold:
                    speaking, phrase, quiet = True, list(pre), 0
                else:
                    noise = 0.97 * noise + 0.03 * rms  # follow the room's background level
                continue
            phrase.append(block)
            quiet = quiet + 1 if rms < threshold * 0.7 else 0
            seconds = len(phrase) * BLOCK / RATE
            # while PLAG talks its own voice never goes quiet: cut every 2 s so an interruption is heard quickly
            longest, pause = (2.0, 0.3) if self.speaking else (MAX_PHRASE_S, self.pause_s)
            if seconds <= SHORT_PHRASE_S:
                pause = min(pause, SHORT_PAUSE_S)  # "PLAG" alone: answer sooner (the dashboard mic takes it from there)
            if quiet * BLOCK / RATE >= pause or seconds >= longest:
                speaking = False
                pre.clear()
                if 0.35 <= seconds <= MAX_PHRASE_S:
                    try:
                        self._jobs.put_nowait(b"".join(phrase))
                    except queue.Full:
                        pass  # still transcribing earlier speech: skip, never fall behind
                phrase = []

    # -------------------------------------------------------- phrase -> wake word

    def _work(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                pcm = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.paused:
                continue  # the ElevenLabs agent is listening right now
            try:
                samples = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
                text = transcribe(samples, "en")
            except Exception as e:
                log.warning("wake transcription failed: %s", e)
                continue
            if stop.is_set():
                continue
            woke, rest = split_wake(text)
            if is_stop(text):
                # the dashboard acts on it only while PLAG is talking or thinking
                self._emit(bus.publish, "voice.stop", {"text": text})
                continue
            if not woke and self.always:
                self._hands_free(text, pcm)
                continue
            if not woke:
                continue
            # "plug in the charger" isn't "PLAG": a look-alike only counts before a real command, or alone
            said_word = re.sub(r"[^a-z]", "", _WAKE.match(text).group(0).split()[-1].casefold()) if text else ""
            if said_word not in EXACT_WAKE and rest and self.sensitivity != "high":
                from .fastpath import parse_many
                if not parse_many(rest):
                    continue
            now = time.monotonic()
            if now - self._last_wake < 1.5:
                continue  # one "PLAG" heard twice (echo, a repeated word): wake once
            self._last_wake = now
            data = {"text": text, "rest": rest, "confidence": 1.0 if said_word in EXACT_WAKE else 0.6,
                    "audio": base64.b64encode(_to_wav(pcm)).decode() if len(rest) >= 2 else None, "hands_free": False}
            self._emit(bus.publish, "wake.detected", data)
            self._emit(lambda: audit("wake.detected", text=text, with_command=bool(rest)))

    def _hands_free(self, text: str, pcm: bytes) -> None:
        """"Always listening": a phrase without "PLAG" is a command too. Not while PLAG talks (that's its own voice,
        heard back) or just after, and not what's only noise ("Thank you." is what speech-to-text makes of a cough)."""
        if self.speaking or time.monotonic() - self.quiet_since < 0.8:
            return
        words = re.sub(r"[^\w\s']", "", text.casefold()).split()
        if len(" ".join(words)) < 3 or " ".join(words) in HANDS_FREE_NOISE or len(pcm) < RATE * 2 * 0.45:
            return
        now = time.monotonic()
        if now - self._last_wake < 1.0:
            return
        self._last_wake = now
        self._emit(bus.publish, "wake.detected", {"text": text, "rest": text, "confidence": 0.8, "hands_free": True,
                                                  "audio": base64.b64encode(_to_wav(pcm)).decode()})


wake = WakeListener()
