// Realtime hearing (NVIDIA Parakeet, or ElevenLabs Scribe v2 Realtime, through the core): while you speak, the
// microphone is streamed to the core, which relays it on; the words come back as you say them, and the final text is
// ready a moment after you stop (~0.3 s with NVIDIA), so there's no upload-then-transcribe wait. When neither is set
// up the core says so at once and the recording is uploaded as before.
import { coreConfig } from './core';

export type Heard = { text: string; lang: string };

export class LiveTranscriber {
  private ws: WebSocket;
  private queue: (ArrayBuffer | string)[] = [];
  private finalWaiter?: (h: Heard | null) => void;
  private finalResult: Heard | null | undefined;
  realtime: boolean | null = null; // null until the core answers
  by = 'realtime hearing'; // who hears you: "NVIDIA Parakeet" or ElevenLabs
  partial = '';

  private constructor(ws: WebSocket, private onPartial: (text: string) => void) {
    this.ws = ws;
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => {
      for (const b of this.queue) ws.send(b);
      this.queue = [];
    };
    ws.onmessage = (m) => {
      let msg: { type?: string; realtime?: boolean; text?: string; ok?: boolean; lang?: string; by?: string };
      try {
        msg = JSON.parse(m.data as string);
      } catch {
        return;
      }
      if (msg.by) this.by = msg.by;
      if (msg.type === 'ready') {
        this.realtime = !!msg.realtime;
        if (!this.realtime) this.resolve(null);
      } else if (msg.type === 'partial' && msg.text) {
        this.partial = msg.text;
        this.onPartial(msg.text);
      } else if (msg.type === 'final') {
        this.resolve(msg.ok && msg.text ? { text: msg.text, lang: msg.lang ?? '' } : null);
      }
    };
    ws.onclose = () => this.resolve(null);
    ws.onerror = () => this.resolve(null);
  }

  /** Opens right away; audio pushed before the socket is up is queued, not lost. */
  static async open(onPartial: (text: string) => void): Promise<LiveTranscriber | null> {
    const cfg = await coreConfig();
    if (!cfg) return null;
    return new LiveTranscriber(new WebSocket(`ws://127.0.0.1:${cfg.port}/ws/listen`, ['plag.v1', `token.${cfg.token}`]), onPartial);
  }

  private send(data: ArrayBuffer | string): void {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(data);
    else if (this.ws.readyState === WebSocket.CONNECTING && this.queue.length < 250) this.queue.push(data);
  }

  push(pcm: ArrayBuffer): void {
    if (this.realtime === false || this.finalResult !== undefined) return;
    this.send(pcm);
  }

  /** You stopped talking: the final text, or null (not set up, nothing heard, or too slow) to fall back. */
  finish(timeoutMs = 3500): Promise<Heard | null> {
    if (this.finalResult !== undefined) return Promise.resolve(this.finalResult);
    this.send(JSON.stringify({ type: 'end' }));
    return new Promise<Heard | null>((resolve) => {
      const timer = window.setTimeout(() => this.resolve(null), timeoutMs);
      this.finalWaiter = (h) => {
        window.clearTimeout(timer);
        resolve(h);
      };
      if (this.finalResult !== undefined) this.finalWaiter(this.finalResult);
    });
  }

  cancel(): void {
    try {
      if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: 'cancel' }));
    } catch {
      /* already closed */
    }
    this.resolve(null);
  }

  private resolve(h: Heard | null): void {
    if (this.finalResult !== undefined) return;
    this.finalResult = h;
    this.finalWaiter?.(h);
    try {
      this.ws.close();
    } catch {
      /* already closed */
    }
  }
}
