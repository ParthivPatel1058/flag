// PLAG's main voice: a live conversation with your ElevenLabs agent (its voice, its turn-taking, its personality).
// When it's asked to do something, the agent calls the "plag" tool and PLAG does it (WhatsApp, YouTube, files…),
// then the agent says the result. PLAG's own voice loop is the backup: when the agent can't start (turned off, no key,
// no agent minutes left, offline), everything works exactly as before.
import type { Conversation } from '@elevenlabs/client';
import { call, CoreError } from './core';
import { live } from './live';
import { useStore, type TurnResult } from '../state/store';

let session: Conversation | null = null;
let starting: Promise<boolean> | null = null;
let raf = 0;
let showResult: ((res: TurnResult) => Promise<void>) | null = null; // voice.ts: pictures, 3D models, PDFs on the stage

/** voice.ts shows what a command made (a picture, a 3D model, a PDF) without speaking over the agent. */
export function onAgentResult(fn: (res: TurnResult) => Promise<void>): void {
  showResult = fn;
}

export const agentLive = (): boolean => !!session;

/** The agent's words without stage directions ("[excited]") or stray tags ("<OPENAI>"). */
export const cleanAgentText = (t: string): string =>
  t.replace(/<\/?[A-Za-z_]+>/g, '').replace(/\[[^\]]{1,24}\]\s*/g, '').replace(/\s+/g, ' ').trim();

/** The agent asked PLAG to do something: run it like a spoken command, show what it made, return what to say. */
async function runCommand(command: string): Promise<string> {
  if (!command.trim()) return 'No command was given.';
  const s = useStore.getState();
  try {
    const res = await call<TurnResult>('/v1/turn/text', { json: { text: command, lang: s.lang, heard_by: 'ElevenLabs agent' } });
    useStore.getState().completeTurn(res);
    await showResult?.(res);
    return res.reply || 'Done.';
  } catch (e) {
    return `That didn't work: ${e instanceof CoreError ? e.message : String(e)}`;
  } finally {
    useStore.getState().settle();
  }
}

function meter(): void {
  const tick = () => {
    if (!session) return;
    live.mic = Math.min(1, session.getInputVolume() * 1.6); // the orb and the mic rings follow the conversation
    live.speak = Math.min(1, session.getOutputVolume() * 1.4);
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
}

async function cleanup(): Promise<void> {
  cancelAnimationFrame(raf);
  live.mic = 0;
  live.speak = 0;
  session = null;
  useStore.getState().setAgentMode(null);
  await call('/v1/wake/pause', { json: { paused: false } }).catch(() => undefined); // "PLAG" works again
}

/**
 * Start talking with the agent (or, if already talking, pass `first` on as what you said).
 * false = the agent isn't available right now: the caller uses PLAG's own voice loop instead.
 */
export async function startAgent(first?: string): Promise<boolean> {
  if (session) {
    if (first) session.sendUserMessage(first);
    return true;
  }
  if (starting) return starting;
  starting = (async () => {
    let info: { signed_url: string; name: string };
    try {
      info = await call<{ signed_url: string; name: string }>('/v1/agent/session');
    } catch {
      return false; // off, not set up, or ElevenLabs unreachable
    }
    const { Conversation } = await import('@elevenlabs/client'); // loaded the first time only
    await call('/v1/wake/pause', { json: { paused: true } }).catch(() => undefined);
    try {
      session = await Conversation.startSession({
        signedUrl: info.signed_url,
        connectionType: 'websocket',
        clientTools: { plag: async (p: { command?: string }) => runCommand(String(p?.command ?? '')) },
        onMessage: ({ message, role }) => {
          const text = cleanAgentText(message);
          if (text) useStore.getState().agentSaid(role, text);
        },
        onModeChange: ({ mode }) => useStore.getState().setAgentMode(mode),
        onDisconnect: () => void cleanup(),
        onError: (message) => useStore.getState().setNotice(`ElevenLabs agent: ${message}`),
      });
    } catch (e) {
      await cleanup();
      // e.g. no agent minutes left this month: said on screen, and PLAG's own voice answers instead
      useStore.getState().setNotice(`ElevenLabs agent couldn't start (${e instanceof Error ? e.message : String(e)}), so PLAG's own voice is answering.`);
      return false;
    }
    useStore.getState().setAgentMode('listening');
    meter();
    if (first) session.sendUserMessage(first);
    return true;
  })();
  try {
    return await starting;
  } finally {
    starting = null;
  }
}

export async function stopAgent(): Promise<void> {
  const s = session;
  if (!s) return;
  await s.endSession().catch(() => undefined);
  await cleanup();
}
