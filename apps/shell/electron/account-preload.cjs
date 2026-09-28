// Runs in every connected-account window (LinkedIn, Instagram, Gmail…), before the site's own scripts.
// Sites announce new messages with browser notifications; this catches each one ("Rahul: are you free?") and hands it
// to PLAG instead of popping it up. It reads nothing else from the page and can't type or click anything.
const { contextBridge, ipcRenderer, webFrame } = require('electron');

contextBridge.exposeInMainWorld('__plagAccount', {
  notice: (title, body, tag) =>
    ipcRenderer.send('plag:acct-notification', String(title ?? '').slice(0, 300), String(body ?? '').slice(0, 2000),
      String(tag ?? '').slice(0, 100)),
});

// Runs in the page's own world: a stand-in for window.Notification that tells PLAG and shows nothing.
function patch() {
  const bridge = window.__plagAccount;
  if (!bridge || window.__plagPatched) return;
  window.__plagPatched = true;
  const tell = (title, options) => {
    try {
      bridge.notice(title, (options && options.body) || '', (options && options.tag) || '');
    } catch {
      /* never break the site */
    }
  };
  function PlagNotification(title, options) {
    tell(title, options);
    const n = new EventTarget();
    Object.assign(n, { title: String(title ?? ''), body: (options && options.body) || '', tag: (options && options.tag) || '',
      data: options && options.data, onclick: null, onclose: null, onerror: null, onshow: null, close() {} });
    return n;
  }
  PlagNotification.permission = 'granted';
  PlagNotification.maxActions = 2;
  PlagNotification.requestPermission = (cb) => {
    if (typeof cb === 'function') cb('granted');
    return Promise.resolve('granted');
  };
  Object.defineProperty(window, 'Notification', { value: PlagNotification, writable: true, configurable: true });
  if (window.ServiceWorkerRegistration) {
    ServiceWorkerRegistration.prototype.showNotification = function showNotification(title, options) {
      tell(title, options);
      return Promise.resolve();
    };
    ServiceWorkerRegistration.prototype.getNotifications = () => Promise.resolve([]);
  }
  if (navigator.permissions && navigator.permissions.query) {
    const query = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (d) =>
      d && d.name === 'notifications'
        ? Promise.resolve({ name: 'notifications', state: 'granted', onchange: null, addEventListener() {}, removeEventListener() {} })
        : query(d);
  }
}

try {
  if (typeof contextBridge.executeInMainWorld === 'function') contextBridge.executeInMainWorld({ func: patch });
  else void webFrame.executeJavaScript(`(${patch.toString()})()`);
} catch {
  void webFrame.executeJavaScript(`(${patch.toString()})()`);
}
