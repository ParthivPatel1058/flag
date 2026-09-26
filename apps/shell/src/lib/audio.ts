// Microphone capture to 16 kHz WAV with simple voice-activity endpointing, playback with level metering,
// and short earcons. Audio lives only in memory; the mic track is released the moment recording stops.
import { live } from './live';

export type AutoStopReason = 'silence' | 'nospeech' | 'max';

/** How long a pause ends what you're saying (Settings → Listening): fast 450, normal 650, patient 1000 ms. */
export const endpoint = { silenceMs: 650 };

const FRAME = 1024; // 64 ms at 16 kHz: an interruption is noticed within a few frames
const PREROLL_FRAMES = 12; // ~0.77 s kept while PLAG talks: the words you said as you cut in aren't lost
// ~0.4 s of your voice over PLAG's stops it (0.2 s let PLAG's own voice and room noise cut it off, 2026-09-26)
const BARGE_FRAMES = 6;

export class MicRecorder {
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private source?: MediaStreamAudioSourceNode;
  private proc?: ScriptProcessorNode;
  private chunks: Float32Array[] = [];
  private startedAt = 0;
  private speechAt = 0;
  private lastVoice = 0;
  private noise = 0.008;
  private fired = false;
  private noSpeechMs = 7000;
  // while PLAG talks: how much of its own voice still reaches the mic after echo cancellation (learned as it
  // speaks), the last few output levels (the echo arrives a little late), and how long you've been talking over it
  private leak = 0.15;
  private outRecent: number[] = [];
  private voicedFrames = 0;
  private monitorAt = 0;
  onAutoStop?: (reason: AutoStopReason) => void;
  /** Each 64 ms of audio as 16 kHz 16-bit PCM, while recording (realtime hearing streams it to the core). */
  onPcm?: (pcm: ArrayBuffer) => void;
  /** PLAG is saying "Yes sir?" while the mic is already open: only a clearly louder voice (yours) counts as speech. */
  holding = false;
  /** Listening while PLAG talks, for you cutting in: nothing is recorded or sent, only the last ~0.8 s is kept. */
  monitor = false;
  /** You started talking over PLAG. The recorder has already switched to recording, your first words included. */
  onBargeIn?: () => void;
  /** How loud PLAG's own voice is right now (the speaker's level), so its echo is never taken for you. */
  echoLevel: () => number = () => 0;

  get active() {
    return !!this.ctx;
  }

  get heardSpeech() {
    return this.speechAt > 0;
  }

  get durationMs() {
    return this.startedAt ? performance.now() - this.startedAt : 0;
  }

  /**
   * Open the mic. `noSpeechMs`: give up this long after starting if nobody spoke (a follow-up window is shorter than
   * a tap). `monitor`: PLAG is about to talk; listen only for you cutting in (see record()).
   */
  async start(opts: { noSpeechMs?: number; monitor?: boolean } = {}): Promise<void> {
    this.noSpeechMs = opts.noSpeechMs ?? 7000;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext({ sampleRate: 16000 });
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.proc = this.ctx.createScriptProcessor(FRAME, 1, 1);
    this.chunks = [];
    this.startedAt = performance.now();
    this.speechAt = 0;
    this.lastVoice = 0;
    this.fired = false;
    this.monitor = !!opts.monitor;
    this.monitorAt = this.startedAt;
    this.voicedFrames = 0;
    this.outRecent = [];
    this.proc.onaudioprocess = (e) => this.frame(e.inputBuffer.getChannelData(0));
    this.source.connect(this.proc);
    this.proc.connect(this.ctx.destination); // the processor only runs while connected; it outputs silence
  }

  /**
   * From listening-while-PLAG-talks to recording what you say, without reopening the mic: for a follow-up after
   * PLAG's reply, or at once when you cut in (then the last ~0.8 s is kept, so your first words are in).
   */
  record(opts: { noSpeechMs?: number; preRoll?: boolean } = {}): void {
    if (!this.ctx) return;
    const now = performance.now();
    this.noSpeechMs = opts.noSpeechMs ?? 7000;
    this.monitor = false;
    this.fired = false;
    this.startedAt = now;
    if (!opts.preRoll) this.chunks = [];
    this.speechAt = opts.preRoll ? now : 0;
    this.lastVoice = opts.preRoll ? now : 0;
  }

  /** Realtime hearing: what's been recorded so far (the words you said as you cut in), then every new frame. */
  streamTo(fn: (pcm: ArrayBuffer) => void): void {
    for (const c of this.chunks) fn(toPcm16(c));
    this.onPcm = fn;
  }

  private frame(data: Float32Array): void {
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const rms = Math.sqrt(sum / data.length);
    const now = performance.now();
    live.mic = Math.min(1, rms / 0.1);
    if (this.monitor) {
      this.chunks.push(new Float32Array(data));
      if (this.chunks.length > PREROLL_FRAMES) this.chunks.shift();
      this.watchForBargeIn(rms, now);
      return;
    }
    this.chunks.push(new Float32Array(data));
    if (this.onPcm) this.onPcm(toPcm16(data));
    const t = now - this.startedAt;
    if (t < 350 && !this.speechAt) this.noise = Math.max(0.004, this.noise * 0.6 + rms * 0.4);
    if (rms > (this.holding ? Math.max(0.07, this.noise * 9) : Math.max(0.02, this.noise * 3.2))) {
      if (!this.speechAt) this.speechAt = now;
      this.lastVoice = now;
    }
    let reason: AutoStopReason | null = null;
    if (this.speechAt && now - this.lastVoice > endpoint.silenceMs) reason = 'silence'; // you stopped talking
    else if (!this.speechAt && t > this.noSpeechMs) reason = 'nospeech';
    else if (t > 20000) reason = 'max';
    if (reason && !this.fired) {
      this.fired = true;
      this.onAutoStop?.(reason);
    }
  }

  /**
   * You, talking over PLAG? The mic's echo canceller removes most of PLAG's own voice; what's left is roughly
   * proportional to how loud PLAG is (the "leak", learned while it talks). Your voice is louder than that leak for
   * several frames in a row, and louder than the room.
   */
  private watchForBargeIn(rms: number, now: number): void {
    const out = this.echoLevel();
    this.outRecent.push(out);
    if (this.outRecent.length > 5) this.outRecent.shift(); // the echo trails the sound by up to ~0.3 s
    const outMax = Math.max(...this.outRecent);
    if (outMax < 0.004) this.noise = Math.max(0.004, this.noise * 0.9 + rms * 0.1); // a pause: the room's own level
    const echo = this.leak * outMax;
    const loud = rms > Math.max(0.035, this.noise * 5, echo * 3.5 + 0.02);
    if (loud) this.voicedFrames += 1;
    else {
      this.voicedFrames = Math.max(0, this.voicedFrames - 1);
      // no one talking over PLAG: whatever the mic hears now is PLAG's echo, so learn it (slowly forget the peak)
      if (outMax > 0.01) this.leak = Math.min(1.2, Math.max(this.leak * 0.995, (rms / outMax) * 0.9));
    }
    const settled = now - this.monitorAt > 350; // the first moments teach the echo level
    if (settled && this.voicedFrames >= BARGE_FRAMES && !this.fired) {
      this.fired = true;
      this.record({ preRoll: true });
      this.onBargeIn?.();
    }
  }

  async stop(): Promise<ArrayBuffer | null> {
    const chunks = this.chunks;
    const rate = this.ctx?.sampleRate ?? 16000;
    await this.teardown();
    return chunks.length ? encodeWav(chunks, rate) : null;
  }

  async cancel(): Promise<void> {
    await this.teardown();
  }

  private async teardown() {
    this.onPcm = undefined;
    this.onBargeIn = undefined;
    this.monitor = false;
    if (this.proc) this.proc.onaudioprocess = null;
    this.proc?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    const ctx = this.ctx;
    this.ctx = undefined;
    this.proc = undefined;
    this.source = undefined;
    this.stream = undefined;
    this.chunks = [];
    this.startedAt = 0;
    live.mic = 0;
    await ctx?.close().catch(() => undefined);
  }
}

function toPcm16(data: Float32Array): ArrayBuffer {
  const out = new Int16Array(data.length);
  for (let i = 0; i < data.length; i++) {
    const s = Math.max(-1, Math.min(1, data[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out.buffer;
}

function encodeWav(chunks: Float32Array[], rate: number): ArrayBuffer {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, s: string) => [...s].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)));
  text(0, 'RIFF');
  view.setUint32(4, 36 + length * 2, true);
  text(8, 'WAVE');
  text(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, 'data');
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, chunk[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
  }
  return buffer;
}

let outCtx: AudioContext | null = null;
const output = () => (outCtx ??= new AudioContext());

export class Speaker {
  private node?: AudioBufferSourceNode;
  private raf = 0;
  private streamed: AudioBufferSourceNode[] = []; // the pieces of a streamed reply, scheduled back to back
  private gen = 0; // bumps on stop(): a stream still arriving stops scheduling
  private release?: () => void;
  private analyser?: AnalyserNode; // the reply playing now, for level()
  private levelBuf = new Float32Array(512);

  get playing() {
    return !!this.node || this.streamed.length > 0;
  }

  /** How loud PLAG is right now (RMS), read straight from the audio graph, so it works while hidden in the tray. */
  level(): number {
    const a = this.analyser;
    if (!a) return 0;
    a.getFloatTimeDomainData(this.levelBuf);
    let sum = 0;
    for (let i = 0; i < this.levelBuf.length; i++) sum += this.levelBuf[i] * this.levelBuf[i];
    return Math.sqrt(sum / this.levelBuf.length);
  }

  /**
   * Play 16-bit mono PCM while it's still arriving (ElevenLabs streaming): sound starts as soon as the first tenth
   * of a second is in, and each next piece is scheduled right after the last. `estimateSec` paces the word
   * highlighting until the real length is known.
   */
  async playStream(body: ReadableStream<Uint8Array>, rate: number, estimateSec: number, onStart?: () => void): Promise<void> {
    this.stop();
    const gen = ++this.gen;
    const ctx = output();
    if (ctx.state === 'suspended') await ctx.resume();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.connect(ctx.destination);
    this.analyser = analyser;
    const reader = body.getReader();
    const sources: AudioBufferSourceNode[] = [];
    this.streamed = sources;
    const levels = new Float32Array(analyser.fftSize);
    let t0 = 0;
    let at = 0;
    let total = 0;
    let finished = false;
    const tick = () => {
      analyser.getFloatTimeDomainData(levels);
      let sum = 0;
      for (let i = 0; i < levels.length; i++) sum += levels[i] * levels[i];
      live.speak = Math.min(1, Math.sqrt(sum / levels.length) / 0.14);
      live.speakProgress = Math.min(1, Math.max(0, ctx.currentTime - t0) / (finished ? total : Math.max(estimateSec, total)));
      this.raf = requestAnimationFrame(tick);
    };
    const schedule = (bytes: Uint8Array) => {
      const n = bytes.length >> 1;
      if (!n) return;
      const view = new DataView(bytes.buffer, bytes.byteOffset, n * 2);
      const audio = ctx.createBuffer(1, n, rate);
      const ch = audio.getChannelData(0);
      for (let i = 0; i < n; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
      const src = ctx.createBufferSource();
      src.buffer = audio;
      src.connect(analyser);
      if (!t0) {
        t0 = at = ctx.currentTime + 0.04;
        live.speakProgress = 0;
        onStart?.();
        tick();
      }
      if (at < ctx.currentTime) at = ctx.currentTime + 0.02; // the network fell behind: carry on right away
      src.start(at);
      at += audio.duration;
      total += audio.duration;
      sources.push(src);
    };
    const minBytes = Math.round(rate * 2 * 0.1); // schedule in pieces of at least 0.1 s
    let pending = new Uint8Array(0);
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (gen !== this.gen) {
          void reader.cancel().catch(() => undefined);
          return;
        }
        if (done) break;
        const joined = new Uint8Array(pending.length + value.length);
        joined.set(pending);
        joined.set(value, pending.length);
        pending = joined;
        if (pending.length >= minBytes) {
          const even = pending.length & ~1; // a sample can be split across network chunks
          schedule(pending.subarray(0, even));
          pending = pending.slice(even);
        }
      }
      schedule(pending);
    } catch (e) {
      if (gen !== this.gen) return;
      if (!sources.length) throw e; // nothing played: the caller falls back to another voice
    }
    finished = true;
    if (gen !== this.gen || !sources.length) return;
    await new Promise<void>((resolve) => {
      this.release = resolve;
      sources[sources.length - 1].onended = () => resolve();
    });
    if (gen === this.gen) {
      cancelAnimationFrame(this.raf);
      live.speak = 0;
      live.speakProgress = 1;
      this.streamed = [];
    }
  }

  /** Play one WAV. `offset`/`span` place it within a longer reply so the words light up across all phrases. */
  async play(wav: ArrayBuffer, offset = 0, span = 1): Promise<void> {
    this.stop();
    const ctx = output();
    if (ctx.state === 'suspended') await ctx.resume();
    const audio = await ctx.decodeAudioData(wav.slice(0));
    const src = ctx.createBufferSource();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    src.buffer = audio;
    src.connect(analyser);
    analyser.connect(ctx.destination);
    this.node = src;
    this.analyser = analyser;
    const buf = new Float32Array(analyser.fftSize);
    const t0 = ctx.currentTime;
    const tick = () => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      live.speak = Math.min(1, Math.sqrt(sum / buf.length) / 0.14);
      live.speakProgress = offset + span * Math.min(1, (ctx.currentTime - t0) / audio.duration);
      this.raf = requestAnimationFrame(tick);
    };
    return new Promise<void>((resolve) => {
      src.onended = () => {
        cancelAnimationFrame(this.raf);
        live.speak = 0;
        live.speakProgress = offset + span;
        if (this.node === src) this.node = undefined;
        resolve();
      };
      live.speakProgress = offset;
      src.start();
      tick();
    });
  }

  stop() {
    const node = this.node;
    this.node = undefined;
    this.analyser = undefined;
    this.gen++;
    cancelAnimationFrame(this.raf);
    live.speak = 0;
    for (const n of [node, ...this.streamed]) {
      try {
        n?.stop();
      } catch {
        /* already stopped, or never started */
      }
    }
    this.streamed = [];
    this.release?.();
    this.release = undefined;
  }
}

/** Short, quiet interface sounds: start listening, still listening after a reply (softer), stop, error. */
export function earcon(kind: 'start' | 'follow' | 'stop' | 'error') {
  const ctx = output();
  if (ctx.state === 'suspended') void ctx.resume();
  const notes: Record<typeof kind, [number, number][]> = {
    start: [[660, 0], [990, 0.07]],
    follow: [[880, 0]],
    stop: [[880, 0], [587, 0.07]],
    error: [[233, 0], [196, 0.12]],
  };
  const now = ctx.currentTime;
  for (const [freq, at] of notes[kind]) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0, now + at);
    gain.gain.linearRampToValueAtTime(0.05, now + at + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.16);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now + at);
    osc.stop(now + at + 0.18);
  }
}
