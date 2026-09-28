// The inbox agent on the dashboard: connected accounts (Gmail, LinkedIn, Instagram…) and the new messages PLAG
// found on them, each with a drafted reply. PLAG drafts; you send: "Copy" puts the reply on the clipboard and
// "Open" shows the account's own site so you can paste it and press send.
import { call, CoreError } from './core';
import { onPlagSay } from './voice';
import { useStore, type Account, type InboxItem } from '../state/store';

export async function refreshInbox(): Promise<void> {
  try {
    const r = await call<{ items: InboxItem[] }>('/v1/inbox');
    useStore.getState().setInbox(r.items);
  } catch {
    /* core restarting: the list refreshes on the next event */
  }
}

/** A message arrived and its reply is drafted: into the Inbox tab, a tray notification, and PLAG says it. */
export async function onInboxNew(e: { item: InboxItem; say?: string }): Promise<void> {
  await refreshInbox();
  if (e.say) await onPlagSay({ text: e.say, kind: e.item.service_name || 'Inbox', lang: 'en', mood: e.item.urgency === 'high' ? 'serious' : 'calm' });
  else window.plag?.notify(`PLAG · ${e.item.service_name}`, e.item.summary || `New message from ${e.item.sender}`);
}

export async function copyReply(item: InboxItem, text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    useStore.getState().setError('Couldn’t copy the reply. Select it and press Ctrl+C.');
    return false;
  } finally {
    void item;
  }
}

/** Show the conversation on the account's own site (you paste the reply and press send there). */
export async function openItem(item: InboxItem): Promise<void> {
  const ok = await window.plag?.openAccount(item.account, item.url || undefined);
  if (!ok) useStore.getState().setError('That account isn’t connected any more. Add it again under Connections.');
}

export async function redraft(item: InboxItem, instruction: string): Promise<string | null> {
  try {
    const r = await call<{ reply: string }>(`/v1/inbox/${encodeURIComponent(item.id)}/redraft`, { json: { instruction } });
    await refreshInbox();
    return r.reply;
  } catch (e) {
    useStore.getState().setError(e instanceof CoreError ? e.message : 'The AI is busy. Try again in a moment.');
    return null;
  }
}

export async function markDone(item: InboxItem): Promise<void> {
  await call(`/v1/inbox/${encodeURIComponent(item.id)}/done`, { method: 'POST' }).catch(() => undefined);
  await refreshInbox();
}

export async function dismiss(item: InboxItem): Promise<void> {
  await call(`/v1/inbox/${encodeURIComponent(item.id)}`, { method: 'DELETE' }).catch(() => undefined);
  await refreshInbox();
}

// ---------------------------------------------------------------- accounts (kept by the desktop shell)

export function watchAccounts(): () => void {
  const bridge = window.plag;
  if (!bridge?.accounts) return () => undefined;
  void bridge.accounts().then((list) => useStore.getState().setAccounts(list));
  return bridge.onAccounts((list) => useStore.getState().setAccounts(list));
}

/** Connect: PLAG opens the site's sign-in page in its own window; you sign in there, then close it. */
export async function addAccount(url: string): Promise<string | null> {
  const bridge = window.plag;
  if (!bridge?.addAccount) return 'Connecting accounts works in the PLAG desktop app.';
  const r = await bridge.addAccount(url);
  if (r.error) return r.error;
  useStore.getState().setNotice(`Sign in to ${r.account?.name ?? 'the site'} in the window that opened, then close it. PLAG will start watching for new messages.`);
  return null;
}

export const openAccount = (a: Account) => window.plag?.openAccount(a.id);
export const pauseAccount = (a: Account) => window.plag?.watchAccount(a.id, !a.watch);
export const removeAccount = (a: Account) => window.plag?.removeAccount(a.id);

/** Addresses offered as one-click chips in the Connect form. */
export const QUICK_SITES = [
  { label: 'Gmail', url: 'mail.google.com' },
  { label: 'LinkedIn', url: 'linkedin.com' },
  { label: 'Instagram', url: 'instagram.com' },
  { label: 'X', url: 'x.com' },
  { label: 'Facebook', url: 'facebook.com' },
  { label: 'Outlook', url: 'outlook.live.com' },
  { label: 'Slack', url: 'app.slack.com' },
  { label: 'Discord', url: 'discord.com' },
];

// ---------------------------------------------------------------- the left panel's sections

export interface PanelWeather {
  place?: string;
  now?: { temp: number; feels: number; humidity: number; wind: number; sky: string };
  days?: { date: string; high: number; low: number; rain: number; sky: string }[];
  error?: string;
  message?: string;
}
export interface PanelNews {
  items: { title: string; source: string; link: string; date: string }[];
  error?: string;
  message?: string;
}

export async function loadWeather(): Promise<PanelWeather | null> {
  try {
    return await call<PanelWeather>('/v1/panel/weather');
  } catch {
    return null;
  }
}

export async function loadNews(): Promise<PanelNews | null> {
  try {
    return await call<PanelNews>('/v1/panel/news');
  } catch {
    return null;
  }
}
