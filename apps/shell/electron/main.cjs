// PLAG desktop shell: owns the window, the tray icon, global hotkeys, and the plag-core child process.
// Closing the window hides PLAG to the tray: the wake word, voice and reminders keep working. Quit from the tray.
const { app, BrowserWindow, Menu, Tray, dialog, globalShortcut, ipcMain, nativeImage, session, shell } = require('electron');
const { spawn, execFile } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..', '..', '..');
const CORE_DIR = path.join(ROOT, 'core');
const LOG_DIR = path.join(process.env.LOCALAPPDATA || app.getPath('userData'), 'PLAG', 'logs');
const DEV_URL = process.env.PLAG_DEV_URL || '';
const TOKEN = crypto.randomBytes(32).toString('hex');

const HIDDEN = process.argv.includes('--hidden'); // started with Windows: stay in the tray

let win = null;
let tray = null;
let core = null;
let corePort = 0;
let quitting = false;
let restarts = 0;
let toldAboutTray = false;

if (!app.requestSingleInstanceLock()) app.quit();

// Memory. Measured 2026-09-24 at rest: GPU process ~300 MB, page ~150 MB. PLAG draws two 2D canvases and a few panels:
// a small GPU tile budget is plenty, V8 favours size over speed, disk caches stay small, and there's no spare renderer.
app.commandLine.appendSwitch('force-gpu-mem-available-mb', '128');
app.commandLine.appendSwitch('js-flags', '--optimize-for-size --max-old-space-size=256');
app.commandLine.appendSwitch('disk-cache-size', String(8 * 1024 * 1024));
app.commandLine.appendSwitch('disable-features', 'SpareRendererForSitePerProcess,HardwareMediaKeyHandling,MediaSessionService');

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function coreCommand() {
  const venvPython = path.join(CORE_DIR, '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venvPython)) return [venvPython, ['-m', 'plag_core']];
  return ['uv', ['run', '--project', CORE_DIR, 'python', '-m', 'plag_core']];
}

function notify(channel, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
}

// The core's output also goes to a file: when PLAG starts from `plag` or Explorer there is no console to read.
let coreLog = null;
function logCore(text) {
  try {
    if (!coreLog) {
      fs.mkdirSync(LOG_DIR, { recursive: true });
      coreLog = fs.createWriteStream(path.join(LOG_DIR, 'core.log'), { flags: 'w' });
    }
    coreLog.write(text);
  } catch {
    // logging must never stop PLAG
  }
}

function startCore() {
  const [cmd, args] = coreCommand();
  logCore(`--- ${new Date().toISOString()} starting ${cmd} ${args.join(' ')}\n`);
  core = spawn(cmd, args, {
    cwd: CORE_DIR,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      PLAG_PORT: String(corePort),
      PLAG_TOKEN: TOKEN,
      PLAG_PARENT_PID: String(process.pid),
      PYTHONIOENCODING: 'utf-8',
      PYTHONUNBUFFERED: '1',
    },
  });
  notify('plag:core', { state: 'starting' });
  core.stdout.on('data', (d) => { process.stdout.write(`[core] ${d}`); logCore(d); });
  core.stderr.on('data', (d) => { process.stderr.write(`[core] ${d}`); logCore(d); });
  core.on('error', (err) => { console.error('[core] failed to start:', err.message); logCore(`failed to start: ${err.message}\n`); });
  core.on('exit', (code) => {
    logCore(`--- core exited with code ${code}\n`);
    core = null;
    notify('plag:core', { state: 'exited', code });
    if (quitting || restarts >= 5) return;
    restarts += 1;
    setTimeout(startCore, 1200 * restarts);
  });
}

function stopCore() {
  if (!core) return;
  // end the whole tree: uv (if used) -> python
  execFile('taskkill', ['/pid', String(core.pid), '/T', '/F'], () => {});
  core = null;
}

async function haltViaCore() {
  try {
    await fetch(`http://127.0.0.1:${corePort}/v1/killswitch`, { method: 'POST', headers: { 'X-PLAG-Token': TOKEN } });
  } catch {
    // core unreachable: the renderer still shows HALTED and stops mic/voice locally
  }
}

const isTrusted = (url) => (DEV_URL && url.startsWith(DEV_URL)) || url.startsWith('file://');

function lockDownSession() {
  const ses = session.defaultSession;
  // Microphone and camera, only for our own page (the camera opens only when you ask).
  // Screen capture, location, notifications and everything else: denied.
  ses.setPermissionRequestHandler((wc, permission, callback, details) => {
    const types = details.mediaTypes || [];
    const ok = permission === 'media' && isTrusted(details.requestingUrl || wc.getURL()) &&
      types.length > 0 && types.every((t) => t === 'audio' || t === 'video');
    callback(ok);
  });
  ses.setPermissionCheckHandler((wc, permission, origin) => permission === 'media' && isTrusted(origin || ''));
}

function createWindow() {
  win = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1120,
    minHeight: 720,
    show: false,
    title: 'PLAG',
    backgroundColor: '#050506',
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#050506', symbolColor: '#9A9C94', height: 46 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      spellcheck: false,
      backgroundThrottling: false, // hidden in the tray, PLAG still has to hear, think and speak on time
    },
  });
  win.once('ready-to-show', () => {
    if (!HIDDEN) win.show();
    notify('plag:visible', win.isVisible() && !win.isMinimized());
  });
  // With background throttling off (PLAG must hear and speak from the tray), the page can't tell it's hidden on its
  // own: tell it, so it stops drawing and lets the animation canvases go while nobody can see them.
  for (const [event, visible] of [['show', true], ['restore', true], ['hide', false], ['minimize', false]]) {
    win.on(event, () => notify('plag:visible', visible));
  }
  win.on('close', (e) => {
    if (quitting) return;
    e.preventDefault(); // closing hides to the tray; "Quit PLAG" in the tray menu really quits
    win.hide();
    if (!toldAboutTray && tray) {
      toldAboutTray = true;
      tray.displayBalloon({ title: 'PLAG is still listening', content: 'Say “PLAG” any time. Right-click the tray icon to quit.' });
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (url !== win.webContents.getURL()) e.preventDefault();
  });
  if (DEV_URL) win.loadURL(DEV_URL);
  else win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  win.on('closed', () => { win = null; });
}

function registerShortcuts() {
  // Talk from any app
  globalShortcut.register('Control+Space', () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    notify('plag:ptt');
  });
  // Kill switch: halts even if the dashboard is frozen
  globalShortcut.register('Control+Alt+Shift+X', () => {
    haltViaCore();
    notify('plag:kill');
  });
}

ipcMain.handle('plag:config', (event) => {
  if (!isTrusted(event.senderFrame?.url || '')) return null;
  return { port: corePort, token: TOKEN };
});

function showWindow() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

// The wake word brings the dashboard forward, unless it's hidden in the tray: then PLAG answers by voice only.
ipcMain.on('plag:reveal', (event) => {
  if (!win || !isTrusted(event.senderFrame?.url || '') || !win.isVisible()) return;
  if (win.isMinimized()) win.restore();
  win.moveTop();
});

// Connect Google: you pick the OAuth client file Google Cloud Console gave you; its text goes to the core, which keeps it
// in Windows Credential Manager. Only a .json file you choose is read.
ipcMain.handle('plag:pick-google-client', async (event) => {
  if (!win || !isTrusted(event.senderFrame?.url || '')) return null;
  const pick = await dialog.showOpenDialog(win, {
    title: 'Choose your Google OAuth client file (Desktop app)',
    filters: [{ name: 'Google OAuth client', extensions: ['json'] }],
    properties: ['openFile'],
  });
  if (pick.canceled || !pick.filePaths[0]) return null;
  const file = pick.filePaths[0];
  if (fs.statSync(file).size > 20000) return null;
  return fs.readFileSync(file, 'utf8');
});

// Settings -> Clear cache: the dashboard's own caches (web, shader and script caches). Saved settings stay.
ipcMain.handle('plag:clear-cache', async (event) => {
  if (!isTrusted(event.senderFrame?.url || '')) return false;
  const ses = session.defaultSession;
  await ses.clearCache();
  await ses.clearCodeCaches({});
  await ses.clearStorageData({ storages: ['shadercache', 'cachestorage', 'serviceworkers'] });
  return true;
});

// Reminders: a notification from the tray icon (works for this unpackaged app, unlike Windows toasts).
ipcMain.on('plag:notify', (event, title, body) => {
  if (!tray || !isTrusted(event.senderFrame?.url || '')) return;
  tray.displayBalloon({ title: String(title || 'PLAG'), content: String(body || ''), noSound: false });
});

// ---------------------------------------------------------------- tray and start with Windows

// The login entry runs this same app hidden (Electron quotes the app folder, which has spaces).
const loginItem = () => ({ name: 'PLAG', path: process.execPath, args: [app.getAppPath(), '--hidden'] });

// Electron writes the entry fine but can't read it back for an unpackaged app, so ask the registry directly.
function startsWithWindows() {
  return new Promise((resolve) => {
    execFile('reg', ['query', 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', '/v', 'PLAG'],
      { windowsHide: true }, (err) => resolve(!err));
  });
}

function trayIcon() {
  // a lime ring with a dot, drawn here so the app ships no image files
  const size = 32;
  const c = (size - 1) / 2;
  const px = Buffer.alloc(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x - c, y - c);
      const ring = Math.min(15.5 - d, d - 10.5);
      const dot = 5.5 - d;
      const a = Math.max(0, Math.min(1, Math.max(ring, dot)));
      const i = (y * size + x) * 4; // BGRA, premultiplied
      px[i] = Math.round(0x4b * a);
      px[i + 1] = Math.round(0xf2 * a);
      px[i + 2] = Math.round(0xd6 * a);
      px[i + 3] = Math.round(255 * a);
    }
  }
  return nativeImage.createFromBitmap(px, { width: size, height: size });
}

async function wakeEnabled() {
  try {
    const r = await fetch(`http://127.0.0.1:${corePort}/v1/wake`, { headers: { 'X-PLAG-Token': TOKEN } });
    return Boolean((await r.json()).enabled);
  } catch {
    return false;
  }
}

async function trayMenu() {
  return Menu.buildFromTemplate([
    { label: 'Open PLAG', click: showWindow },
    { type: 'separator' },
    // the dashboard owns the wake setting, so the tray asks it to switch (it runs even while hidden)
    { label: 'Listen for “PLAG”', type: 'checkbox', checked: await wakeEnabled(), click: (item) => notify('plag:set-wake', item.checked) },
    {
      label: 'Start with Windows', type: 'checkbox', checked: await startsWithWindows(),
      click: (item) => app.setLoginItemSettings({ ...loginItem(), openAtLogin: item.checked }),
    },
    { type: 'separator' },
    { label: 'Quit PLAG', click: () => { quitting = true; app.quit(); } },
  ]);
}

function createTray() {
  tray = new Tray(trayIcon());
  tray.setToolTip('PLAG · say “PLAG”');
  tray.on('click', showWindow);
  tray.on('right-click', async () => tray.popUpContextMenu(await trayMenu()));
}

app.on('second-instance', showWindow); // typing `plag` again opens the dashboard

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  lockDownSession();
  corePort = await freePort();
  startCore();
  createWindow();
  createTray();
  registerShortcuts();
});

app.on('before-quit', () => {
  quitting = true;
  globalShortcut.unregisterAll();
  stopCore();
});
