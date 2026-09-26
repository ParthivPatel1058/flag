/// <reference types="vite/client" />

interface PlagBridge {
  getConfig(): Promise<{ port: number; token: string } | null>;
  onPushToTalk(fn: () => void): () => void;
  onKill(fn: () => void): () => void;
  onCoreStatus(fn: (s: { state: 'starting' | 'exited'; code?: number | null }) => void): () => void;
  onSetWake(fn: (on: boolean) => void): () => void;
  /** The window was hidden to the tray or minimized (false), or shown again (true). */
  onVisible(fn: (visible: boolean) => void): () => void;
  /** Clears the dashboard's own web, shader and script caches. */
  clearCache(): Promise<boolean>;
  captureScreen(): Promise<string | null>; // a JPEG data URL of the screen, or null
  notify(title: string, body: string): void;
  pickGoogleClient(): Promise<string | null>;
  /** Bring the dashboard window to the front (used by the wake word). */
  reveal(): void;
  platform: string;
}

interface Window {
  plag?: PlagBridge;
}

interface ImportMetaEnv {
  /** Browser-preview only: port and token of a core started by hand. */
  readonly VITE_PLAG_PORT?: string;
  readonly VITE_PLAG_TOKEN?: string;
}
