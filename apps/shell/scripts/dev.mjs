// `npm run dev`: start Vite, then Electron pointed at it. Closing the window stops both.
import { spawn } from 'node:child_process';
import { createServer } from 'vite';
import electronPath from 'electron';

const server = await createServer({ configFile: 'vite.config.ts' });
await server.listen();
const url = server.resolvedUrls?.local[0] ?? 'http://127.0.0.1:5173/';
console.log(`PLAG dashboard dev server: ${url}`);

const child = spawn(electronPath, ['.'], { stdio: 'inherit', env: { ...process.env, PLAG_DEV_URL: url } });
child.on('exit', async (code) => {
  await server.close();
  process.exit(code ?? 0);
});
