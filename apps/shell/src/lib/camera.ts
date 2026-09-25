// The camera. Opens only when you ask, shows inside the orb, and a single frame is sent to Gemini only when you ask
// "what is this?". The camera light and the title-bar indicator are on whenever it runs.
import { useStore } from '../state/store';

class Camera {
  private stream?: MediaStream;
  private video: HTMLVideoElement | null = null;

  get active() {
    return !!this.stream;
  }

  attach(el: HTMLVideoElement | null) {
    this.video = el;
    if (el && this.stream && el.srcObject !== this.stream) {
      el.srcObject = this.stream;
      void el.play().catch(() => undefined);
    }
  }

  async start(): Promise<void> {
    if (this.stream) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
      audio: false,
    });
    useStore.getState().setCamera(true);
    this.attach(this.video);
  }

  stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = undefined;
    if (this.video) this.video.srcObject = null;
    useStore.getState().setCamera(false);
  }

  /** Wait until the video has real frames (exposure settles in the first moments). */
  async ready(timeoutMs = 2500): Promise<boolean> {
    const start = performance.now();
    while (performance.now() - start < timeoutMs) {
      if (this.video && this.video.readyState >= 2 && this.video.videoWidth > 0) return true;
      await new Promise((r) => setTimeout(r, 80));
    }
    return false;
  }

  /** One JPEG frame, base64, longest side at most `max` px. */
  capture(max = 896): string | null {
    const v = this.video;
    if (!v || !v.videoWidth) return null;
    const scale = Math.min(1, max / Math.max(v.videoWidth, v.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(v.videoWidth * scale);
    canvas.height = Math.round(v.videoHeight * scale);
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.82).split(',')[1] ?? null;
  }
}

export const camera = new Camera();

export function cameraError(e: unknown, hindi: boolean): string {
  const name = (e as DOMException)?.name;
  if (name === 'NotAllowedError')
    return hindi ? 'कैमरा की अनुमति बंद है। Windows privacy settings में "Let desktop apps access your camera" चालू करें।'
      : 'Camera access is off. Turn on "Let desktop apps access your camera" in Windows privacy settings.';
  if (name === 'NotFoundError') return hindi ? 'कोई कैमरा नहीं मिला।' : 'No camera found.';
  if (name === 'NotReadableError') return hindi ? 'कैमरा किसी और ऐप में चल रहा है।' : 'Another app is using the camera.';
  return hindi ? 'कैमरा शुरू नहीं हो पाया।' : "The camera couldn't start.";
}
