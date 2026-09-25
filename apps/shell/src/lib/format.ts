export const pct = (v?: number | null) => (v == null ? '—' : String(Math.round(v)));

export function rate(bps?: number | null): string {
  if (bps == null) return '—';
  const k = bps / 1024;
  if (k < 1) return `${Math.round(bps)} B/s`;
  if (k < 1024) return `${k < 10 ? k.toFixed(1) : Math.round(k)} KB/s`;
  const m = k / 1024;
  return `${m < 10 ? m.toFixed(1) : Math.round(m)} MB/s`;
}

export const mb = (v: number) => (v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${Math.round(v)} MB`);

export function secs(ms?: number | null): string {
  if (ms == null) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function greeting(d = new Date()): string {
  const h = d.getHours();
  if (h < 5) return 'Up late.';
  if (h < 12) return 'Good morning.';
  if (h < 17) return 'Good afternoon.';
  return 'Good evening.';
}

export const langTag = (l?: string) => (l === 'hi' ? 'हिं' : l === 'mixed' ? 'Hinglish' : l === 'en' ? 'EN' : '');
