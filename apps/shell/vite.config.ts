import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// Production CSP: scripts and fonts only from the app itself; network only to the local PLAG core, plus ElevenLabs
// for a live agent conversation (its SDK connects from the page with a one-time link the core made). blob: lets the
// 3D viewer read a model already in memory and the agent's audio worklets load; nothing else from outside.
// (2026-09-24: without the ElevenLabs and blob: entries the agent never connected and 3D models never showed.)
const CSP = [
  "default-src 'self'",
  "script-src 'self' blob:",
  "worker-src 'self' blob:",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "img-src 'self' data: blob:",
  "media-src 'self' blob: data:",
  'connect-src http://127.0.0.1:* ws://127.0.0.1:* wss://api.elevenlabs.io wss://*.elevenlabs.io https://api.elevenlabs.io https://*.elevenlabs.io blob: data:',
].join('; ');

const csp: Plugin = {
  name: 'plag-csp',
  apply: 'build',
  transformIndexHtml: (html) =>
    html.replace('<head>', `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
};

export default defineConfig({
  base: './',
  plugins: [react(), csp],
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  build: { outDir: 'dist', emptyOutDir: true, target: 'chrome130' },
});
