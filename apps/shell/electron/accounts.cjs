// Connected accounts: Gmail, LinkedIn, Instagram, X or any site you add by its address.
//
// You sign in yourself, in a PLAG window that shows the real site (PLAG never sees or stores your password). Each
// account keeps its own private browser session (partition "persist:acct-<id>"), so it stays signed in and accounts
// never share cookies with each other or with the dashboard.
//
// Watching: a hidden window keeps each account's messages page open. Sites announce new messages with the browser's
// own notifications ("Rahul Sharma: are you free tomorrow?"); PLAG gives these windows notification permission, catches
// each one before it would pop up, and hands it to plag-core, which drafts a reply (inbox.py). When a site only updates
// its unread count in the tab title ("(3) Messaging | LinkedIn"), that is passed on too. PLAG only reads: it never
// types or sends anything on these sites.
const { BrowserWindow, app, ipcMain, session, shell } = require('electron');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const FILE = () => path.join(app.getPath('userData'), 'accounts.json');
const MAX_ACCOUNTS = 12;
const MAX_WATCHING = 6; // each watcher is a browser page (~100-200 MB): enough for the accounts that matter
const RELOAD_MS = 20 * 60 * 1000; // a fresh page every 20 minutes, in case a site's live connection went quiet

// Known sites: their name, where their messages are, and a colour for the dashboard. Anything else is named after its host.
const KNOWN = [
  { host: 'mail.google.com', match: /(^|\.)mail\.google\.com$|(^|\.)gmail\.com$/, name: 'Gmail', service: 'gmail',
    home: 'https://mail.google.com/mail/u/0/', watch: 'https://mail.google.com/mail/u/0/#inbox', color: '#ea4335' },
  { host: 'linkedin.com', match: /(^|\.)linkedin\.com$/, name: 'LinkedIn', service: 'linkedin',
    home: 'https://www.linkedin.com/login', watch: 'https://www.linkedin.com/messaging/', color: '#0a66c2' },
  { host: 'instagram.com', match: /(^|\.)instagram\.com$/, name: 'Instagram', service: 'instagram',
    home: 'https://www.instagram.com/accounts/login/', watch: 'https://www.instagram.com/direct/inbox/', color: '#e1306c' },
  { host: 'x.com', match: /(^|\.)(x|twitter)\.com$/, name: 'X', service: 'x',
    home: 'https://x.com/login', watch: 'https://x.com/messages', color: '#e7e9ea' },
  { host: 'facebook.com', match: /(^|\.)facebook\.com$|(^|\.)messenger\.com$/, name: 'Facebook', service: 'facebook',
    home: 'https://www.facebook.com/login', watch: 'https://www.facebook.com/messages/', color: '#1877f2' },
  { host: 'outlook.live.com', match: /(^|\.)outlook\.(live|office)\.com$/, name: 'Outlook', service: 'outlook',
    home: 'https://outlook.live.com/mail/', watch: 'https://outlook.live.com/mail/0/', color: '#0078d4' },
  { host: 'web.whatsapp.com', match: /^web\.whatsapp\.com$/, name: 'WhatsApp Web', service: 'whatsapp',
    home: 'https://web.whatsapp.com/', watch: 'https://web.whatsapp.com/', color: '#25d366' },
  { host: 'web.telegram.org', match: /^web\.telegram\.org$/, name: 'Telegram', service: 'telegram',
    home: 'https://web.telegram.org/', watch: 'https://web.telegram.org/', color: '#2aabee' },
  { host: 'app.slack.com', match: /(^|\.)slack\.com$/, name: 'Slack', service: 'slack',
    home: 'https://app.slack.com/', watch: 'https://app.slack.com/', color: '#e01e5a' },
  { host: 'discord.com', match: /(^|\.)discord\.com$/, name: 'Discord', service: 'discord',
    home: 'https://discord.com/login', watch: 'https://discord.com/channels/@me', color: '#5865f2' },
];
// Pages that mean "not signed in yet"
const LOGIN_RX = /(\/login|\/signin|\/sign-in|\/sign_in|\/accounts\/login|\/uas\/login|\/checkpoint|\/i\/flow\/login|accounts\.google\.com|login\.live\.com|login\.microsoftonline\.com|\/authwall)/i;

let ctx = null; // { core(): {port, token}, notify(channel, payload), isTrusted(url) }
let accounts = [];
const live = new Map(); // id -> { watcher, login, state, unread, lastNotice, lastCount, events: [] }

// ---------------------------------------------------------------- storage

function load() {
  try {
    const raw = JSON.parse(fs.readFileSync(FILE(), 'utf8'));
    accounts = Array.isArray(raw) ? raw.filter((a) => a && typeof a.id === 'string' && /^https:\/\//.test(a.url)) : [];
  } catch {
    accounts = [];
  }
}

function save() {
  try {
    fs.writeFileSync(FILE(), JSON.stringify(accounts, null, 1));
  } catch {
    // a failed save must never break PLAG; the list lives on until the next change
  }
}

function known(host) {
  return KNOWN.find((k) => k.match.test(host)) || null;
}

/** "linkedin.com", "https://www.instagram.com/x" -> a clean https URL and what PLAG knows about the site. */
function parse(input) {
  let text = String(input || '').trim();
  if (!text) return null;
  if (!/^[a-z]+:\/\//i.test(text)) text = `https://${text}`;
  let u;
  try {
    u = new URL(text);
  } catch {
    return null;
  }
  if (u.protocol !== 'https:' || !u.hostname.includes('.') || u.username || u.password) return null;
  const host = u.hostname.toLowerCase().replace(/^www\./, '');
  const k = known(host);
  const name = k ? k.name : host.split('.').slice(-2, -1)[0].replace(/^./, (c) => c.toUpperCase());
  return {
    host,
    name,
    service: k ? k.service : host.replace(/[^a-z0-9]/g, ''),
    color: k ? k.color : '#d6f24b',
    home: k ? k.home : u.toString(),
    watch: k ? k.watch : u.toString(),
  };
}

function publicView(a) {
  const l = live.get(a.id) || {};
  return {
    id: a.id, name: a.name, host: a.host, service: a.service, color: a.color, url: a.url, watch: a.watch !== false,
    state: l.state || (a.watch === false ? 'paused' : 'starting'), unread: l.unread || 0, added: a.added,
  };
}

function push() {
  ctx?.notify('plag:accounts', accounts.map(publicView));
}

// ---------------------------------------------------------------- browser sessions

const UA = () => session.defaultSession.getUserAgent().replace(/\s(?:plag-shell|Electron)\/\S+/g, '');

function sessionFor(a) {
  const ses = session.fromPartition(`persist:acct-${a.id}`);
  if (!ses.__plag) {
    ses.__plag = true;
    ses.setUserAgent(UA()); // a normal Chrome: some sites refuse sign-in from an "Electron" browser
    // notifications are how sites tell PLAG about new messages; nothing else (no camera, mic, location, clipboard)
    ses.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === 'notifications'));
    ses.setPermissionCheckHandler((_wc, permission) => permission === 'notifications');
  }
  return ses;
}

function webPrefs(a) {
  return {
    session: sessionFor(a),
    preload: path.join(__dirname, 'account-preload.cjs'),
    contextIsolation: true,
    sandbox: true,
    nodeIntegration: false,
    spellcheck: true,
    backgroundThrottling: false, // a hidden watcher must still hear its site's live updates
  };
}

function guard(wc, a) {
  // links and pop-ups ("Sign in with Google") stay inside this account's session; only https
  wc.setWindowOpenHandler(({ url }) => {
    if (!/^https:\/\//.test(url)) return { action: 'deny' };
    return { action: 'allow', overrideBrowserWindowOptions: { autoHideMenuBar: true, webPreferences: webPrefs(a) } };
  });
  wc.on('will-navigate', (e, url) => {
    if (!/^https:\/\//.test(url)) e.preventDefault();
  });
}

function stateOf(url) {
  return LOGIN_RX.test(url || '') ? 'signin' : 'watching';
}

function setState(a, state) {
  const l = live.get(a.id);
  if (!l || l.state === state) return;
  l.state = state;
  push();
}

/** The window you sign in and reply in: the real site, in this account's session. */
function openWindow(a, url) {
  const l = live.get(a.id) || {};
  live.set(a.id, l);
  if (l.login && !l.login.isDestroyed()) {
    if (url) l.login.loadURL(url);
    l.login.show();
    l.login.focus();
    return;
  }
  const w = new BrowserWindow({
    width: 1180, height: 840, title: `${a.name} · PLAG`, backgroundColor: '#ffffff', autoHideMenuBar: true,
    webPreferences: webPrefs(a),
  });
  l.login = w;
  guard(w.webContents, a);
  w.webContents.setUserAgent(UA());
  w.loadURL(url || a.home);
  w.on('page-title-updated', (e, title) => {
    e.preventDefault();
    w.setTitle(`${title} · ${a.name} · PLAG`);
  });
  w.webContents.on('did-navigate', (_e, u) => { if (stateOf(u) === 'watching' && l.state === 'signin') setState(a, 'starting'); });
  w.on('closed', () => {
    l.login = null;
    if (a.watch !== false) restartWatcher(a); // signed in just now: start watching with the fresh session
  });
}

// ---------------------------------------------------------------- watching

function post(pathname, body) {
  const core = ctx?.core();
  if (!core || !core.port) return;
  fetch(`http://127.0.0.1:${core.port}${pathname}`, {
    method: 'POST', headers: { 'X-PLAG-Token': core.token, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).catch(() => undefined); // the core restarting: this one message is missed, the next one gets through
}

function allowEvent(l) {
  const now = Date.now();
  l.events = (l.events || []).filter((t) => now - t < 60_000);
  if (l.events.length >= 30) return false; // a site gone haywire can't flood PLAG
  l.events.push(now);
  return true;
}

function onNotice(a, title, body, tag) {
  const l = live.get(a.id);
  if (!l || !allowEvent(l)) return;
  l.lastNotice = Date.now();
  post('/v1/inbox/event', { account: a.id, service_url: a.host, title, body, url: '', kind: 'message' });
  void tag;
}

const COUNT_RX = [/^\((\d+)\)/, /\((\d+)\)/, /\b(\d+)\s+(?:new|unread)\b/i];

function onTitle(a, title) {
  const l = live.get(a.id);
  if (!l) return;
  let n = 0;
  for (const rx of COUNT_RX) {
    const m = rx.exec(title || '');
    if (m) { n = Number(m[1]); break; }
  }
  const before = l.unread || 0;
  l.unread = n;
  if (n !== before) push();
  if (n > before && before >= 0 && l.primed) {
    // wait a moment: the site usually sends a proper notification (with who and what) right after the count changes
    const seen = n;
    setTimeout(() => {
      if (Date.now() - (l.lastNotice || 0) < 12_000 || l.unread !== seen || !allowEvent(l)) return;
      post('/v1/inbox/event', { account: a.id, service_url: a.host, title: String(title || '').slice(0, 200), body: '',
        url: '', kind: 'count', count: seen });
    }, 6000);
  }
}

function stopWatcher(a) {
  const l = live.get(a.id);
  if (!l) return;
  clearInterval(l.timer);
  if (l.watcher && !l.watcher.isDestroyed()) l.watcher.destroy();
  l.watcher = null;
}

function restartWatcher(a) {
  stopWatcher(a);
  startWatcher(a);
}

function watchingCount() {
  let n = 0;
  for (const l of live.values()) if (l.watcher && !l.watcher.isDestroyed()) n += 1;
  return n;
}

function startWatcher(a) {
  const l = live.get(a.id) || {};
  live.set(a.id, l);
  if (a.watch === false) { l.state = 'paused'; push(); return; }
  if (watchingCount() >= MAX_WATCHING) { l.state = 'limit'; push(); return; }
  const w = new BrowserWindow({ show: false, width: 1280, height: 900, webPreferences: webPrefs(a) });
  l.watcher = w;
  l.primed = false; // the first unread count is what was already there, not new
  l.state = 'starting';
  const wc = w.webContents;
  wc.setAudioMuted(true);
  wc.setUserAgent(UA());
  guard(wc, a);
  wc.setWindowOpenHandler(() => ({ action: 'deny' })); // nothing pops up from a hidden watcher
  wc.on('page-title-updated', (_e, title) => onTitle(a, title));
  wc.on('did-navigate', (_e, url) => setState(a, stateOf(url)));
  wc.on('did-navigate-in-page', (_e, url) => setState(a, stateOf(url)));
  wc.on('did-finish-load', () => {
    setState(a, stateOf(wc.getURL()));
    setTimeout(() => { l.primed = true; }, 8000);
  });
  wc.on('did-fail-load', (_e, code, _desc, _url, main) => { if (main && code !== -3) setState(a, 'offline'); });
  wc.on('render-process-gone', () => { setState(a, 'offline'); setTimeout(() => restartWatcher(a), 30_000); });
  w.loadURL(a.watch_url || a.watchUrl || parse(a.url)?.watch || a.url);
  l.timer = setInterval(() => { if (!w.isDestroyed()) { l.primed = false; w.reload(); } }, RELOAD_MS);
  push();
}

// ---------------------------------------------------------------- IPC

function sender(event) {
  return ctx && ctx.isTrusted(event.senderFrame?.url || '');
}

function accountOf(webContents) {
  for (const [id, l] of live) {
    for (const w of [l.watcher, l.login]) {
      if (w && !w.isDestroyed() && w.webContents.id === webContents.id) return accounts.find((a) => a.id === id) || null;
    }
  }
  // pop-ups opened from an account window share its session
  const p = webContents.session;
  return accounts.find((a) => session.fromPartition(`persist:acct-${a.id}`) === p) || null;
}

function init(options) {
  ctx = options;
  load();

  ipcMain.handle('plag:accounts', (event) => (sender(event) ? accounts.map(publicView) : []));

  ipcMain.handle('plag:account-add', (event, input) => {
    if (!sender(event)) return { error: 'denied' };
    const site = parse(input);
    if (!site) return { error: 'That doesn’t look like a website address. Try “linkedin.com”.' };
    if (accounts.length >= MAX_ACCOUNTS) return { error: `PLAG connects up to ${MAX_ACCOUNTS} accounts.` };
    const a = { id: crypto.randomBytes(5).toString('hex'), url: `https://${site.host}/`, host: site.host, name: site.name,
      service: site.service, color: site.color, home: site.home, watch_url: site.watch, watch: true, added: new Date().toISOString() };
    accounts.push(a);
    save();
    live.set(a.id, { state: 'signin' });
    openWindow(a); // you sign in there; closing it starts the watcher
    push();
    return { account: publicView(a) };
  });

  ipcMain.handle('plag:account-open', (event, id, url) => {
    if (!sender(event)) return false;
    const a = accounts.find((x) => x.id === id);
    if (!a) {
      // Gmail through the Google connection: its links open in your normal browser
      if (typeof url === 'string' && /^https:\/\/mail\.google\.com\//.test(url)) { shell.openExternal(url); return true; }
      return false;
    }
    let target = null;
    if (typeof url === 'string' && /^https:\/\//.test(url)) {
      try {
        const h = new URL(url).hostname.toLowerCase().replace(/^www\./, '');
        if (h === a.host || h.endsWith(`.${a.host}`)) target = url; // only this account's own site
      } catch {
        target = null;
      }
    }
    openWindow(a, target || a.watch_url || a.home);
    return true;
  });

  ipcMain.handle('plag:account-watch', (event, id, on) => {
    if (!sender(event)) return false;
    const a = accounts.find((x) => x.id === id);
    if (!a) return false;
    a.watch = Boolean(on);
    save();
    if (a.watch) startWatcher(a); else { stopWatcher(a); live.get(a.id).state = 'paused'; push(); }
    return true;
  });

  ipcMain.handle('plag:account-remove', async (event, id) => {
    if (!sender(event)) return false;
    const a = accounts.find((x) => x.id === id);
    if (!a) return false;
    stopWatcher(a);
    const l = live.get(a.id);
    if (l?.login && !l.login.isDestroyed()) l.login.destroy();
    live.delete(a.id);
    accounts = accounts.filter((x) => x.id !== id);
    save();
    // signed out for good: cookies, storage and cache of that account are wiped from this laptop
    await session.fromPartition(`persist:acct-${a.id}`).clearStorageData().catch(() => undefined);
    await session.fromPartition(`persist:acct-${a.id}`).clearCache().catch(() => undefined);
    push();
    return true;
  });

  // a site's notification, caught in an account window (account-preload.cjs)
  ipcMain.on('plag:acct-notification', (event, title, body, tag) => {
    const a = accountOf(event.sender);
    if (!a) return;
    onNotice(a, String(title || '').slice(0, 300), String(body || '').slice(0, 2000), String(tag || '').slice(0, 100));
  });
}

/** After the core is up: start watching every account (a few seconds apart, so start-up stays light). */
function startAll() {
  accounts.forEach((a, i) => setTimeout(() => startWatcher(a), 4000 + i * 2500));
}

function stopAll() {
  for (const a of accounts) stopWatcher(a);
}

module.exports = { init, startAll, stopAll };
