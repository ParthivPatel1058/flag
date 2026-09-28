"""PLAG's fast hearing on NVIDIA: Parakeet 1.1B multilingual, streaming, over NVIDIA's cloud (gRPC).

While you speak, the dashboard streams the microphone to the core (/ws/listen) and on to NVIDIA; the words come
back as you say them, and the final text arrives a moment after you stop. Measured 2026-09-24 with the user's key,
speech fed in at real-time pace: the final words 0.23-0.67 s after the audio ended, against 1.1-1.7 s for local
Whisper base, which also garbled Hinglish ("OME کو message page 2K-10 minute") where Parakeet heard it word for word
("ओमी को मैसेज भेज दो कि मैं दस मिनट में आ रहा हूं"). Language "multi" switches between Hindi and English by
itself (and was the fastest of hi-IN / en-US / multi). Hindi comes back in Devanagari and is turned into Hinglish in
Latin letters (hinglish.to_latin), the way you write it.

Key: Windows Credential Manager, PLAG / nvidia_hearing_api_key (hearing's own key, 2026-09-28), else
nvidia_speech_api_key (the same key as the Hindi voice). Only the audio of what you say to PLAG is sent, never the
always-on wake listening.
"""

import asyncio
import queue
import threading
import time

from .hinglish import to_latin
from .secrets import get_secret

SERVER = "grpc.nvcf.nvidia.com:443"
FUNCTION_ID = "71203149-d3b7-4460-8231-1be2543a1fca"  # ai-parakeet-1_1b-rnnt-multilingual-asr on NVCF
LANGUAGE = "multi"
NAME = "NVIDIA Parakeet"


def _key() -> str | None:
    return get_secret("nvidia_hearing_api_key") or get_secret("nvidia_speech_api_key")


class Session:
    """One utterance. push() audio while you speak; `events` gets ("partial", text) as words are recognised, then
    ("final", text) or ("error", why). end() waits for the final text."""

    def __init__(self, service, config, loop: asyncio.AbstractEventLoop, owner: "NvHearing") -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self._audio: queue.Queue = queue.Queue()
        self._loop, self._owner = loop, owner
        self._done = asyncio.Event()
        self._final: str | None = None
        self.seconds = 0.0  # audio sent, for the audit log
        self._cancelled = False
        threading.Thread(target=self._run, args=(service, config), daemon=True, name="plag-nvasr").start()

    def push(self, pcm: bytes) -> None:
        if not self._cancelled:
            self.seconds += len(pcm) / 32000
            self._audio.put(pcm)

    def _emit(self, kind: str, text: str) -> None:
        self._loop.call_soon_threadsafe(self.events.put_nowait, (kind, text))

    def _run(self, service, config) -> None:
        def chunks():
            while True:
                pcm = self._audio.get()
                if pcm is None:
                    return
                yield pcm

        finals: list[str] = []
        try:
            for resp in service.streaming_response_generator(audio_chunks=chunks(), streaming_config=config):
                for r in resp.results:
                    if not r.alternatives:
                        continue
                    words = r.alternatives[0].transcript.strip()
                    if r.is_final:
                        finals.append(words)
                        words = ""
                    heard = to_latin(" ".join([*finals, words]).strip())
                    if heard and not self._cancelled:
                        self._emit("partial", heard)
            self._final = to_latin(" ".join(finals).strip())
            self._emit("final", self._final)
        except Exception as e:  # grpc.RpcError, the connection dropping
            if not self._cancelled:
                self._owner.failed(getattr(getattr(e, "code", lambda: None)(), "name", "") or type(e).__name__)
                self._emit("error", str(e)[:160])
        finally:
            self._loop.call_soon_threadsafe(self._done.set)

    async def end(self, timeout: float = 3.0) -> str | None:
        """You stopped talking: the final words (None if NVIDIA failed or took too long)."""
        self._audio.put(None)
        try:
            await asyncio.wait_for(self._done.wait(), timeout)
        except TimeoutError:
            return None
        return self._final or None

    def cancel(self) -> None:
        self._cancelled = True
        self._audio.put(None)


class NvHearing:
    def __init__(self) -> None:
        self._service = None
        self._lock = threading.Lock()
        self._rest_until = 0.0
        self.last_error: str | None = None
        self.health: dict | None = None

    @staticmethod
    def configured() -> bool:
        return bool(_key())

    def usable(self) -> bool:
        return self.configured() and time.time() >= self._rest_until

    def failed(self, why: str) -> None:
        self.last_error = why
        self._rest_until = time.time() + (3600 if why in ("UNAUTHENTICATED", "PERMISSION_DENIED") else 60)
        self._service = None  # a broken channel is rebuilt next time

    def _get_service(self):
        import riva.client  # loaded on first use (~40 MB with grpc), shared with the Hindi voice

        with self._lock:
            if self._service is None:
                auth = riva.client.Auth(uri=SERVER, use_ssl=True, metadata_args=[
                    ["function-id", FUNCTION_ID], ["authorization", f"Bearer {_key()}"]])
                self._service = riva.client.ASRService(auth)
            return self._service

    @staticmethod
    def _config(rate: int = 16000):
        import riva.client
        return riva.client.RecognitionConfig(encoding=riva.client.AudioEncoding.LINEAR_PCM, sample_rate_hertz=rate,
                                             language_code=LANGUAGE, max_alternatives=1,
                                             enable_automatic_punctuation=True, audio_channel_count=1)

    def session(self) -> Session:
        import riva.client
        service = self._get_service()
        config = riva.client.StreamingRecognitionConfig(config=self._config(), interim_results=True)
        return Session(service, config, asyncio.get_running_loop(), self)

    def recognize(self, wav: bytes) -> str:
        """A whole recording at once, as Hinglish text: the dashboard's upload when streaming wasn't possible, and
        "PLAG, open YouTube" said in one breath (heard again properly after the wake listener caught it). Blocking:
        call it from a thread. Raises on failure (the caller falls back to on-device Whisper)."""
        import io
        import wave

        with wave.open(io.BytesIO(wav)) as w:
            rate, pcm = w.getframerate(), w.readframes(w.getnframes())
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("expects 16-bit mono audio")
        t0 = time.perf_counter()
        try:
            resp = self._get_service().offline_recognize(pcm, self._config(rate))
        except Exception as e:
            self.failed(getattr(getattr(e, "code", lambda: None)(), "name", "") or type(e).__name__)
            raise
        heard = " ".join(r.alternatives[0].transcript.strip() for r in resp.results if r.alternatives)
        self.health = {"ok": True, "ms": int((time.perf_counter() - t0) * 1000), "at": time.time()}
        return to_latin(heard.strip())


nvhearing = NvHearing()
