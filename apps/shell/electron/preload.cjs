// The only bridge between the page and Electron: config, and a few one-way signals.
const { contextBridge, ipcRenderer } = require('electron');

function listen(channel, fn) {
  const handler = (_event, payload) => fn(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld('plag', {
  getConfig: () => ipcRenderer.invoke('plag:config'),
  onPushToTalk: (fn) => listen('plag:ptt', fn),
  onKill: (fn) => listen('plag:kill', fn),
  onCoreStatus: (fn) => listen('plag:core', fn),
  onSetWake: (fn) => listen('plag:set-wake', fn),
  onVisible: (fn) => listen('plag:visible', fn),
  clearCache: () => ipcRenderer.invoke('plag:clear-cache'),
  captureScreen: () => ipcRenderer.invoke('plag:capture-screen'),
  reveal: () => ipcRenderer.send('plag:reveal'),
  notify: (title, body) => ipcRenderer.send('plag:notify', String(title).slice(0, 64), String(body).slice(0, 240)),
  pickGoogleClient: () => ipcRenderer.invoke('plag:pick-google-client'),
  // connected accounts: you sign in on the real site in a PLAG window; PLAG watches for new messages, never sends
  accounts: () => ipcRenderer.invoke('plag:accounts'),
  addAccount: (url) => ipcRenderer.invoke('plag:account-add', String(url).slice(0, 300)),
  openAccount: (id, url) => ipcRenderer.invoke('plag:account-open', String(id), url ? String(url).slice(0, 600) : undefined),
  watchAccount: (id, on) => ipcRenderer.invoke('plag:account-watch', String(id), Boolean(on)),
  removeAccount: (id) => ipcRenderer.invoke('plag:account-remove', String(id)),
  onAccounts: (fn) => listen('plag:accounts', fn),
  platform: process.platform,
});
