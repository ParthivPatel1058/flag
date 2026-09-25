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
  reveal: () => ipcRenderer.send('plag:reveal'),
  notify: (title, body) => ipcRenderer.send('plag:notify', String(title).slice(0, 64), String(body).slice(0, 240)),
  pickGoogleClient: () => ipcRenderer.invoke('plag:pick-google-client'),
  platform: process.platform,
});
