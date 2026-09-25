// Talking to plag-core over loopback. The token never goes in a URL.
export type CoreConfig = { port: number; token: string };

export class CoreError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public tried: string[] = [],
  ) {
    super(message);
  }
}

let configPromise: Promise<CoreConfig | null> | null = null;

export function coreConfig(): Promise<CoreConfig | null> {
  if (!configPromise) {
    configPromise = (async () => {
      if (window.plag) return window.plag.getConfig();
      const port = Number(import.meta.env.VITE_PLAG_PORT);
      const token = import.meta.env.VITE_PLAG_TOKEN;
      return port && token ? { port, token } : null;
    })();
  }
  return configPromise;
}

type CallInit = { method?: string; json?: unknown; body?: BodyInit; contentType?: string; signal?: AbortSignal };

export async function call<T>(path: string, init: CallInit = {}): Promise<T> {
  const cfg = await coreConfig();
  if (!cfg) throw new CoreError(0, 'core_unreachable', 'PLAG core is not configured');
  const headers: Record<string, string> = { 'X-PLAG-Token': cfg.token };
  let body = init.body;
  if (init.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(init.json);
  } else if (init.contentType) {
    headers['Content-Type'] = init.contentType;
  }
  let res: Response;
  try {
    res = await fetch(`http://127.0.0.1:${cfg.port}${path}`, {
      method: init.method ?? (body ? 'POST' : 'GET'),
      headers,
      body,
      signal: init.signal,
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new CoreError(0, 'core_unreachable', 'PLAG core is not responding');
  }
  if (!res.ok) {
    let detail: { error?: string; message?: string; tried?: string[] } = {};
    try {
      detail = await res.json();
    } catch {
      /* non-JSON error body */
    }
    throw new CoreError(res.status, detail.error ?? 'http', detail.message ?? `HTTP ${res.status}`, detail.tried ?? []);
  }
  const type = res.headers.get('content-type') ?? '';
  return (type.includes('application/json') ? res.json() : res.arrayBuffer()) as Promise<T>;
}

/** Like call(), but hands back the open response so its body can be used while it's still arriving (voice). */
export async function stream(path: string, init: { json: unknown; signal?: AbortSignal }): Promise<Response> {
  const cfg = await coreConfig();
  if (!cfg) throw new CoreError(0, 'core_unreachable', 'PLAG core is not configured');
  let res: Response;
  try {
    res = await fetch(`http://127.0.0.1:${cfg.port}${path}`, {
      method: 'POST',
      headers: { 'X-PLAG-Token': cfg.token, 'Content-Type': 'application/json' },
      body: JSON.stringify(init.json),
      signal: init.signal,
    });
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e;
    throw new CoreError(0, 'core_unreachable', 'PLAG core is not responding');
  }
  if (!res.ok) {
    const detail: { error?: string; message?: string } = await res.json().catch(() => ({}));
    throw new CoreError(res.status, detail.error ?? 'http', detail.message ?? `HTTP ${res.status}`);
  }
  return res;
}

export type CoreEvent = { topic: string; task_id?: string | null; data: any };

/** Live event stream with automatic reconnect. The token travels as a WebSocket subprotocol. */
export function openEvents(onEvent: (e: CoreEvent) => void, onLink: (up: boolean) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: number | undefined;

  const connect = async () => {
    const cfg = await coreConfig();
    if (closed) return;
    if (!cfg) {
      onLink(false);
      timer = window.setTimeout(connect, 1500);
      return;
    }
    ws = new WebSocket(`ws://127.0.0.1:${cfg.port}/ws`, ['plag.v1', `token.${cfg.token}`]);
    ws.onopen = () => {
      attempt = 0;
      onLink(true);
    };
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      onLink(false);
      if (!closed) timer = window.setTimeout(connect, Math.min(4000, 350 * 2 ** attempt++));
    };
  };

  void connect();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    ws?.close();
  };
}
