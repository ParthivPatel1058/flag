// The conversation loop: wake -> listen -> send to core -> show reply -> speak it -> follow up.
import { MicRecorder, Speaker, earcon, endpoint } from './audio';
import { camera, cameraError } from './camera';
import { call, CoreError, stream } from './core';
import { agentLive, onAgentResult, startAgent, stopAgent } from './agent';
import { LiveTranscriber } from './listen';
import { localVoice, speakLocal, stopLocal } from './tts';
import {
  useStore, type FollowUp, type Lang, type Memory, type Reminder, type RouteStep, type RouteView, type TurnResult, type WakeStatus,
} from '../state/store';

const mic = new MicRecorder();
const speaker = new Speaker();
let inflight: AbortController | null = null;
let followUpApproval: string | null = null;
let live: LiveTranscriber | null = null; // realtime hearing for the command being spoken now
let listenMode: FollowUp = null; // listening without "PLAG": for an answer, or a short follow-up window

let bargedIn = false; // you cut in while PLAG was talking: it's listening to you now
let arming: Promise<void> | null = null; // the mic opening for that, alongside the voice starting

/** From Settings (and what the core can do right now), kept here so the voice loop never waits to look them up. */
const prefs = { followUp: 'always' as 'off' | 'questions' | 'always', realtime: false, stream: false, maxChars: 1000, alwaysListen: false,
  voiceAgent: false, bargeIn: true };
// Replies after which PLAG keeps listening for a few seconds: it talked, rather than opening or playing something
// you're now looking at (a video's sound must never become your next command).
const CONVERSATIONAL = new Set(['none', 'chat', 'time', 'recall', 'reminders', 'remember', 'forget', 'remind',
  'reminder_cancel', 'gmail_check', 'calendar_check', 'system_status', 'weather', 'camera_look', 'where', 'lookup', 'list_files']);

const text = (lang: Lang, en: string, hi: string) => (lang === 'hi' ? hi : en);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const isAbort = (e: unknown) => (e as Error)?.name === 'AbortError';

function micError(e: unknown, lang: Lang): string {
  const name = (e as DOMException)?.name;
  if (name === 'NotAllowedError')
    return text(lang, 'Microphone access is off. Turn on "Let desktop apps access your microphone" in Windows privacy settings.',
      'माइक्रोफ़ोन बंद है। Windows privacy settings में "Let desktop apps access your microphone" चालू करें।');
  if (name === 'NotFoundError') return text(lang, 'No microphone found. Plug one in and try again.', 'कोई माइक्रोफ़ोन नहीं मिला। माइक लगाकर फिर कोशिश करें।');
  return text(lang, "The microphone couldn't start.", 'माइक्रोफ़ोन शुरू नहीं हो पाया।');
}

function explain(e: CoreError, lang: Lang): string {
  switch (e.code) {
    case 'no_key':
      return text(lang, 'The Gemini key is missing. Save it in Windows Credential Manager as PLAG / gemini_api_key.',
        'Gemini key नहीं मिली। उसे Windows Credential Manager में PLAG / gemini_api_key नाम से सेव करें।');
    case 'offline':
      return text(lang, 'You\'re offline. Typed commands like "open YouTube" still work.',
        'इंटरनेट नहीं है। "YouTube खोलो" जैसे टाइप किए गए कमांड फिर भी चलेंगे।');
    case 'quota_day':
      return text(lang, "Today's free Gemini limit is used up. Typed commands still work; it resets tomorrow.",
        'आज की फ्री Gemini लिमिट खत्म हो गई। टाइप किए कमांड चलते रहेंगे, कल फिर से शुरू होगी।');
    case 'unavailable':
    case 'timeout':
      return text(lang, `Gemini is busy right now (tried ${e.tried.length || 'all'} models). Try again in a minute.`,
        'Gemini अभी व्यस्त है। एक मिनट बाद फिर कोशिश करें।');
    case 'halted':
      return text(lang, 'PLAG is halted. Resume it to continue.', 'PLAG रुका हुआ है। आगे बढ़ने के लिए Resume करें।');
    case 'core_unreachable':
      return text(lang, "PLAG core isn't responding. It restarts on its own; try again in a few seconds.",
        'PLAG core जवाब नहीं दे रहा। वह अपने आप restart होता है, कुछ सेकंड में फिर कोशिश करें।');
    default:
      return e.message;
  }
}

function toBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

// ---------------------------------------------------------------- speaking

/** Split a reply into short phrases so the first one can play while the rest are being voiced. */
function phrases(text: string): string[] {
  const parts = text.match(/[^.!?।,;:]+[.!?।,;:]*/g)?.map((p) => p.trim()).filter(Boolean) ?? [text];
  const out: string[] = [];
  for (const p of parts) {
    if (out.length && (out[out.length - 1].length < 14 || p.length < 6)) out[out.length - 1] += ` ${p}`;
    else out.push(p);
  }
  return out;
}

const voiceWav = (text: string, mood: string, signal?: AbortSignal) =>
  call<ArrayBuffer>('/v1/tts', { json: { text, mood }, signal });

/**
 * Streamed (Leo on NVIDIA, or ElevenLabs): the whole reply in one request (natural flow), playing from the first
 * tenth of a second of audio instead of after the reply is fully made. false = no streamed voice right now; the
 * caller then voices the reply phrase by phrase.
 */
async function sayStreamed(reply: string, mood: string, ctrl: AbortController | undefined, t0: number, measure: boolean): Promise<boolean> {
  if (!prefs.stream || reply.length > prefs.maxChars) return false;
  let res: Response;
  try {
    res = await stream('/v1/tts/stream', { json: { text: reply, mood }, signal: ctrl?.signal });
  } catch (e) {
    if (isAbort(e)) throw e;
    return false; // 409: not set up, resting, or out of credits
  }
  if (ctrl?.signal.aborted || useStore.getState().halted) return true;
  const type = res.headers.get('content-type') ?? '';
  const who = /rate=24000/.test(type) ? 'ElevenLabs' : 'Leo on NVIDIA'; // ElevenLabs streams 24 kHz, NVIDIA 22.05 kHz
  const started = () => {
    const ms = Math.round(performance.now() - t0);
    if (measure) useStore.getState().addStep({ step: 'speak', state: 'done', detail: `First audio after ${ms} ms · ${who}, streamed`, ms, label: 'Speak' });
  };
  if (type.includes('audio/wav')) { // said before: the cached recording, free
    const wav = await res.arrayBuffer();
    started();
    await speaker.play(wav);
    return true;
  }
  if (!res.body) return false;
  const rate = Number(/rate=(\d+)/.exec(type)?.[1] ?? 24000);
  try {
    await speaker.playStream(res.body, rate, Math.max(1.2, reply.length / 14), started);
  } catch {
    return false; // the stream broke before any sound: use the other voices
  }
  return true;
}

/**
 * While PLAG talks, keep an ear open for you: start talking and it stops mid-word and listens (the way ElevenLabs'
 * agents take turns). The mic's echo canceller and the speaker's own level keep PLAG from interrupting itself.
 */
async function armBargeIn(ctrl?: AbortController): Promise<void> {
  bargedIn = false;
  if (!prefs.bargeIn || mic.active || useStore.getState().halted) return;
  try {
    await mic.start({ monitor: true });
  } catch {
    return; // no mic right now: PLAG just talks
  }
  mic.echoLevel = () => speaker.level();
  mic.onBargeIn = () => void bargeIn(ctrl);
}

/** You started talking over PLAG: the reply stops, and what you're saying (your first words included) is heard. */
async function bargeIn(ctrl?: AbortController): Promise<void> {
  bargedIn = true;
  speaker.stop();
  stopLocal();
  ctrl?.abort(); // the rest of the reply is dropped
  const s = useStore.getState();
  s.setSpeaking(false);
  followUpApproval = null;
  listenMode = 'window'; // no "PLAG" needed, and a cough or "hmm" is ignored like in any follow-up
  mic.onAutoStop = (reason) => void finishListening(reason === 'nospeech');
  s.beginListening('window');
  live?.cancel();
  live = null;
  if (prefs.realtime) {
    const lt = await LiveTranscriber.open((words) => useStore.getState().setHeard(words));
    if (!mic.active || mic.monitor) {
      lt?.cancel();
      return; // already over (you stopped, or PLAG was stopped)
    }
    live = lt;
    if (lt) {
      mic.streamTo((pcm) => lt.push(pcm)); // the words you said as you cut in go first
      s.setHeard('');
    }
  }
}

/** Speak a reply in PLAG's voice: streamed when possible (Leo on NVIDIA, or ElevenLabs), else phrase by phrase.
 * `bargeIn`: you may cut in (a spoken conversation), see armBargeIn. */
export async function say(reply: string, ctrl?: AbortController, mood = 'calm', measure = true,
  opts: { bargeIn?: boolean } = {}): Promise<void> {
  const s = useStore.getState();
  if (!s.voiceOn || !reply || s.halted) return;
  const parts = phrases(reply);
  const t0 = performance.now();
  s.setSpeaking(true);
  if (opts.bargeIn) arming = armBargeIn(ctrl); // not awaited: the mic opens while the voice is being made
  try {
    try {
      if (await sayStreamed(reply, mood, ctrl, t0, measure)) return;
    } catch (e) {
      if (isAbort(e)) return;
    }
    try {
      let next = voiceWav(parts[0], mood, ctrl?.signal);
      for (let i = 0; i < parts.length; i++) {
        const wav = await next;
        if (i + 1 < parts.length) {
          next = voiceWav(parts[i + 1], mood, ctrl?.signal); // voice the next phrase while this one plays
          next.catch(() => undefined); // if we stop early, an unfinished phrase is simply dropped
        }
        if (ctrl?.signal.aborted || useStore.getState().halted) return;
        if (i === 0 && measure) {
          const ms = Math.round(performance.now() - t0); // the developer panel: how soon the voice starts
          useStore.getState().addStep({ step: 'speak', state: 'done', detail: `First audio after ${ms} ms`, ms, label: 'Speak' });
        }
        await speaker.play(wav, i / parts.length, 1 / parts.length);
      }
      return;
    } catch (e) {
      if (isAbort(e)) return;
    }
    // natural voice unavailable: fall back to the Windows voice. It plays outside the app's audio, where the echo
    // canceller can't hear it, so cutting in is off for this one.
    await arming;
    if (mic.monitor) await mic.cancel();
    const voice = localVoice(/[ऀ-ॿ]/.test(reply) ? 'hi' : 'en');
    if (voice) await speakLocal(reply, voice, mood);
    else s.setNotice(text(s.lang, 'The voice is unavailable right now, so this reply is text only.',
      'आवाज़ अभी उपलब्ध नहीं है, इसलिए यह जवाब सिर्फ़ टेक्स्ट में है।'));
  } finally {
    s.setSpeaking(false);
  }
}

/** "Yes sir?" — instant, local, the moment PLAG hears its name. */
async function acknowledge(): Promise<void> {
  const s = useStore.getState();
  const hindi = s.lang === 'hi';
  const line = hindi ? 'जी सर?' : 'Yes sir?';
  s.sayLocal(line, hindi ? 'hi' : 'en');
  if (!s.voiceOn) return;
  s.setSpeaking(true);
  try {
    await speaker.play(await voiceWav(line, 'calm')); // pre-made at startup: instant
  } catch {
    const voice = localVoice(hindi ? 'hi' : 'en') ?? localVoice('en');
    if (voice) await speakLocal(hindi && !localVoice('hi') ? 'Yes sir?' : line, voice);
  } finally {
    s.setSpeaking(false);
  }
}

// ---------------------------------------------------------------- wake word

export async function onWake(evt: { text?: string; rest?: string; confidence?: number; audio?: string | null; hands_free?: boolean }): Promise<void> {
  const s = useStore.getState();
  if (s.halted || !s.coreUp || s.listening || mic.active || agentLive()) return;
  if (evt.hands_free) {
    // "Always listening": something said without "PLAG". Never over PLAG's own reply or a request in flight.
    if (s.pending || s.speaking || !evt.audio) return;
    if (prefs.voiceAgent && (await startAgent(evt.rest))) return; // the agent takes the conversation from here
    await runTurn({ kind: 'audio', wav: toBuffer(evt.audio), hint: evt.rest, followUp: 'window' });
    return;
  }
  // "PLAG, ..." while PLAG is talking or thinking: that's an interruption, the new request wins
  if (s.pending || s.speaking) await stopAll();
  window.plag?.reveal();
  s.markWoke();
  // your ElevenLabs agent answers first ("PLAG, open YouTube" is passed on as what you said); its own voice is the backup
  if (prefs.voiceAgent && (await startAgent(evt.rest || undefined))) return;
  if (evt.audio) {
    // "PLAG, open YouTube" in one breath: the listener already captured and transcribed the command
    await runTurn({ kind: 'audio', wav: toBuffer(evt.audio), hint: evt.rest });
    return;
  }
  // the mic opens now and "Yes sir?" plays at the same moment: you can start talking without waiting for it
  await startListening();
  if (!mic.active) return;
  mic.holding = true;
  await acknowledge();
  mic.holding = false;
}

let ackPlaying: Promise<void> | null = null;

/** "On it." the moment a slower plan starts; the result is spoken when it's done (after this, never over it). */
export function onAck(evt: { text?: string }): void {
  const s = useStore.getState();
  if (!evt.text || !s.voiceOn || s.speaking || s.listening || mic.active) return;
  ackPlaying = say(evt.text, inflight ?? undefined, 'calm', false).finally(() => { ackPlaying = null; });
}

/** "Stop" / "ruko" / "bas" heard by the offline listener: cuts PLAG off mid-sentence. Ignored when PLAG is quiet. */
export async function onVoiceStop(): Promise<void> {
  const s = useStore.getState();
  if (s.speaking || s.pending) await stopAll();
}

// The listener cuts phrases short while PLAG talks, so an interruption is heard within about two seconds.
useStore.subscribe((now, before) => {
  if (now.speaking !== before.speaking) {
    call('/v1/voice/speaking', { json: { speaking: now.speaking } }).catch(() => undefined);
  }
});

export async function setWake(on: boolean): Promise<void> {
  useStore.getState().setWakeOn(on);
  try {
    const st = await call<WakeStatus>('/v1/wake', { json: { enabled: on } });
    useStore.setState({ wake: st });
  } catch {
    /* applied when the core is back */
  }
}

export async function syncWake(): Promise<void> {
  await setWake(useStore.getState().wakeOn);
}

// ---------------------------------------------------------------- listening

/**
 * Open the mic. `followUp` means no "PLAG" was said: "answer" right after PLAG asked you something (waits up to
 * 9 s for you to start), "window" after a spoken reply (5 s, closes quietly if you say nothing).
 */
export async function startListening(opts: { approvalId?: string; followUp?: FollowUp } = {}): Promise<void> {
  const s = useStore.getState();
  if (s.halted || (mic.active && !mic.monitor)) return;
  // PLAG just finished talking with the mic already open (for cutting in): carry on with it, nothing to reopen
  const reuse = mic.active && mic.monitor;
  speaker.stop();
  stopLocal();
  inflight?.abort();
  followUpApproval = opts.approvalId ?? null;
  const mode = opts.followUp ?? null;
  listenMode = mode;
  live?.cancel();
  live = null;
  let lt: LiveTranscriber | null = null;
  if (prefs.realtime) {
    // NVIDIA (or ElevenLabs) hears you word by word while you speak; the recording is still kept as the fallback
    lt = await LiveTranscriber.open((words) => useStore.getState().setHeard(words));
    live = lt;
  }
  const noSpeechMs = mode === 'window' ? 5000 : mode === 'answer' ? 9000 : 7000;
  if (reuse && mic.active) {
    mic.onBargeIn = undefined;
    mic.record({ noSpeechMs });
    if (lt) mic.streamTo((pcm) => lt.push(pcm));
  } else {
    if (lt) mic.onPcm = (pcm) => lt.push(pcm);
    try {
      await mic.start({ noSpeechMs });
    } catch (e) {
      live?.cancel();
      live = null;
      listenMode = null;
      s.setError(micError(e, s.lang));
      earcon('error');
      return;
    }
  }
  mic.onAutoStop = (reason) => {
    void finishListening(reason === 'nospeech');
  };
  earcon(mode === 'window' ? 'follow' : 'start');
  s.beginListening(mode);
  if (live) s.setHeard('');
}

export async function finishListening(silent = false): Promise<void> {
  if (!mic.active) return;
  const heard = mic.heardSpeech && !silent;
  const ms = mic.durationMs;
  const wav = await mic.stop();
  const approvalId = followUpApproval;
  followUpApproval = null;
  const mode = listenMode;
  listenMode = null;
  const lt = live;
  live = null;
  if (heard || !mode) earcon('stop'); // a follow-up window you didn't use closes without a sound
  const s = useStore.getState();
  s.endListening(ms, heard);
  if (!wav || !heard) {
    lt?.cancel();
    if (!approvalId && !mode) s.setNotice(text(s.lang, "I didn't hear anything. Say “PLAG” or tap the mic.", 'मैंने कुछ नहीं सुना। “PLAG” बोलिए या माइक दबाइए।'));
    return;
  }
  if (lt) {
    const t = performance.now();
    const words = await lt.finish();
    if (words?.text) {
      const after = Math.round(performance.now() - t);
      const by = lt.by === 'realtime hearing' ? 'ElevenLabs realtime' : lt.by;
      useStore.getState().addStep({ step: 'hear', state: 'done', detail: `${by} · final words ${after} ms after you stopped`, ms: after, label: 'Hear' });
      await runTurn({ kind: 'text', text: words.text, heardBy: by, approvalId: approvalId ?? undefined, followUp: mode });
      return;
    }
  }
  await runTurn({ kind: 'audio', wav, approvalId: approvalId ?? undefined, followUp: mode });
}

export async function toggleListening(): Promise<void> {
  const s = useStore.getState();
  if (agentLive()) await stopAgent(); // tap the mic to end the agent conversation
  else if (mic.active) await finishListening();
  else if (s.pending || s.speaking) await stopAll(); // tap the mic to interrupt
  else if (prefs.voiceAgent && (await startAgent())) return;
  else await startListening();
}

export async function sendText(value: string): Promise<void> {
  const clean = value.trim();
  if (clean) await runTurn({ kind: 'text', text: clean });
}

// ---------------------------------------------------------------- turns

type Input =
  | { kind: 'audio'; wav: ArrayBuffer; approvalId?: string; hint?: string; followUp?: FollowUp }
  | { kind: 'text'; text: string; approvalId?: string; heardBy?: string; followUp?: FollowUp };

function liveApproval(): string | undefined {
  const a = useStore.getState().approval;
  return a && a.expiresAt > Date.now() ? a.id : undefined;
}

async function runTurn(input: Input): Promise<void> {
  const st = useStore.getState();
  if (st.halted) return;
  const lang = st.lang;
  inflight?.abort();
  speaker.stop();
  stopLocal();
  const ctrl = new AbortController();
  inflight = ctrl;
  // while a "Send it?" card is open, the next answer is offered to it first
  const approvalId = input.approvalId ?? liveApproval();
  const spoken = input.kind === 'audio' || !!input.heardBy;
  const followUp = input.followUp ?? null;
  const before = { reply: st.reply, replyLang: st.replyLang, heard: st.heard }; // shown again if it was only noise
  st.beginTurn(input.kind === 'text' ? input.text : null, spoken ? 'voice' : 'text');
  try {
    let q = approvalId ? `&approval_id=${encodeURIComponent(approvalId)}` : '';
    if (input.kind === 'audio' && input.hint) q += `&hint=${encodeURIComponent(input.hint)}`;
    if (followUp) q += `&followup=${followUp}`;
    const res =
      input.kind === 'audio'
        ? await call<TurnResult>(`/v1/turn/audio?lang=${lang}${q}`, { body: input.wav, contentType: 'audio/wav', signal: ctrl.signal })
        : await call<TurnResult>('/v1/turn/text', {
          json: { text: input.text, lang, approval_id: approvalId ?? null, heard_by: input.heardBy ?? null, followup: followUp ?? '' },
          signal: ctrl.signal,
        });
    if (ctrl.signal.aborted) return;
    if (res.silent) {
      // a follow-up window caught only room noise: say nothing, put the last answer back, wait for "PLAG"
      useStore.setState({ ...before, pending: false, cloud: false });
      return;
    }
    await handleResult(res, ctrl, spoken);
  } catch (e) {
    if (isAbort(e) || (e instanceof CoreError && e.code === 'cancelled')) return;
    useStore.getState().failTurn(e instanceof CoreError ? explain(e, lang) : String(e));
    earcon('error');
  } finally {
    if (inflight === ctrl) inflight = null;
    useStore.getState().settle();
  }
}

async function handleResult(res: TurnResult, ctrl: AbortController, spoken = false): Promise<void> {
  const s = useStore.getState();
  s.completeTurn(res);
  const client = res.client;
  if (client?.type === 'model3d' && client.id) await showModel3d(client.id, client.prompt ?? '');
  if (client?.type === 'document' && client.id) {
    useStore.getState().setDoc({ id: client.id, title: client.title ?? '', kind: client.kind ?? 'document',
      summary: client.summary ?? '', words: client.words ?? 0, sources: client.sources ?? 0 });
  }
  if (client?.type === 'weather_sim' && client.id) await showSim(client.id, client.label ?? '', client.hours ?? []);
  if (client?.type === 'camera_look') {
    await lookThroughCamera(client.question ?? '', ctrl);
    return;
  }
  if (client?.type === 'screen_look') {
    await lookAtScreen(client.question ?? '', ctrl);
    return;
  }
  if (client?.type === 'camera_imagine') {
    // "gen an image of this": say "let me look" while the frame is taken and the picture is drawn
    await imagineThroughCamera(client.style ?? '', ctrl, say(res.reply, ctrl, 'calm'));
    return;
  }
  if (client?.type === 'image' && client.id) await showImage(client.id, client.prompt ?? '');
  if (client?.type === 'draft_saved' && client.id) markSaved(client.id); // "save": the card shows it's kept
  if (client?.type === 'route' && client.steps) startNav(client as unknown as RouteClient);
  await ackPlaying?.catch(() => undefined); // let "On it." finish before the result
  if (client?.type === 'ui' && client.command) {
    if (client.command === 'halt') {
      await haltPlag();
      return;
    }
    if (client.command === 'stop') {
      await stopAll();
      return;
    }
    await applyUi(client.command);
  }
  // a spoken conversation: you can cut PLAG off by just talking, like with a person
  await say(res.reply, ctrl, res.mood, true, { bargeIn: spoken && prefs.bargeIn });
  await arming?.catch(() => undefined);
  arming = null;
  if (bargedIn) {
    bargedIn = false;
    return; // you cut in: PLAG is already listening to you
  }
  try {
    // "Send it?" — listen for the answer right away, no wake word needed
    if (res.approval && !ctrl.signal.aborted && useStore.getState().approval && !useStore.getState().halted) {
      await startListening({ approvalId: res.approval.id });
      return;
    }
    await keepListening(res, ctrl, spoken);
  } finally {
    if (mic.monitor) await mic.cancel(); // no follow-up after all: the mic closes
  }
}

/**
 * The conversation carries on without "PLAG": after PLAG asks you something ("What should I send to Rahul?",
 * "Which one?") the mic opens for your answer, and after a spoken answer it stays open a few seconds for a follow-up.
 * Only when you were talking (not typing), and never after opening or playing something.
 */
async function keepListening(res: TurnResult, ctrl: AbortController, spoken: boolean): Promise<void> {
  const s = useStore.getState();
  if (prefs.alwaysListen) return; // the ear button is on: the listener already hears everything you say next
  if (!spoken || ctrl.signal.aborted || s.halted || prefs.followUp === 'off' || (mic.active && !mic.monitor) || !s.coreUp) return;
  if (res.expects_reply) {
    await startListening({ followUp: 'answer' });
  } else if (prefs.followUp === 'always' && CONVERSATIONAL.has(res.action?.type ?? '') && res.result?.ok !== false) {
    await startListening({ followUp: 'window' });
  }
}

async function applyUi(command: string): Promise<void> {
  const s = useStore.getState();
  switch (command) {
    case 'mute': s.setVoice(false); break;
    case 'unmute': s.setVoice(true); break;
    case 'lang_hi': s.setLang('hi'); break;
    case 'lang_en': s.setLang('en'); break;
    case 'lang_auto': s.setLang('auto'); break;
    case 'camera_on': await openCamera(); break;
    case 'camera_off': camera.stop(); break;
    case 'stop_nav': await stopNav(false); break;
    case 'wake_off': await setWake(false); break;
  }
}

// ---------------------------------------------------------------- approvals

/** The Send / Cancel buttons on the WhatsApp card. */
export async function decideApproval(approve: boolean): Promise<void> {
  const s = useStore.getState();
  const a = s.approval;
  if (!a) return;
  if (mic.active) {
    const ms = mic.durationMs;
    await mic.cancel();
    s.endListening(ms, false);
  }
  followUpApproval = null;
  listenMode = null;
  live?.cancel();
  live = null;
  inflight?.abort();
  const ctrl = new AbortController();
  inflight = ctrl;
  s.beginTurn(null, 'approval');
  try {
    const res = await call<TurnResult>(`/v1/approvals/${encodeURIComponent(a.id)}`, { json: { approve, lang: s.lang }, signal: ctrl.signal });
    useStore.getState().setApproval(null);
    if (!ctrl.signal.aborted) await handleResult(res, ctrl);
  } catch (e) {
    if (isAbort(e)) return;
    useStore.getState().failTurn(e instanceof CoreError ? explain(e, s.lang) : String(e));
  } finally {
    if (inflight === ctrl) inflight = null;
    useStore.getState().settle();
  }
}

// ---------------------------------------------------------------- camera

export async function openCamera(): Promise<boolean> {
  const s = useStore.getState();
  try {
    await camera.start();
    return true;
  } catch (e) {
    s.setError(cameraError(e, s.lang === 'hi'));
    return false;
  }
}

export async function toggleCamera(): Promise<void> {
  if (camera.active) camera.stop();
  else await openCamera();
}

/** "What is this?" — one frame to Gemini, answer shown and spoken. */
export async function lookThroughCamera(question = '', parent?: AbortController): Promise<void> {
  const s = useStore.getState();
  if (s.halted) return;
  const fresh = !camera.active;
  if (fresh && !(await openCamera())) return;
  await camera.ready();
  if (fresh) await sleep(700); // let exposure settle
  const image = camera.capture();
  if (!image) {
    s.setError(text(s.lang, 'The camera has no picture yet. Try again.', 'कैमरा में अभी तस्वीर नहीं है। फिर कोशिश करें।'));
    return;
  }
  const ctrl = parent ?? new AbortController();
  if (!parent) {
    inflight?.abort();
    inflight = ctrl;
  }
  s.beginTurn(null, 'camera');
  try {
    const res = await call<TurnResult>('/v1/vision', { json: { image, lang: s.lang, question }, signal: ctrl.signal });
    if (ctrl.signal.aborted) return;
    useStore.getState().completeTurn(res);
    await say(res.reply, ctrl, 'curious');
  } catch (e) {
    if (isAbort(e)) return;
    useStore.getState().failTurn(e instanceof CoreError ? explain(e, s.lang) : String(e));
    earcon('error');
  } finally {
    if (!parent && inflight === ctrl) inflight = null;
    useStore.getState().settle();
  }
}

/** "Look at my screen" / "screen dekho": one screenshot (PLAG's window fades out for it), then the AI reads it and
 * answers: what's going on, what a message or error says (translated, in any language), what to do next. */
export async function lookAtScreen(question = '', parent?: AbortController): Promise<void> {
  const s = useStore.getState();
  if (s.halted) return;
  const image = await window.plag?.captureScreen().catch(() => null);
  if (!image) {
    s.setError(text(s.lang, "I couldn't take a screenshot of your screen.", 'स्क्रीन का स्क्रीनशॉट नहीं ले पाया।'));
    return;
  }
  const ctrl = parent ?? new AbortController();
  if (!parent) {
    inflight?.abort();
    inflight = ctrl;
  }
  s.beginTurn(null, 'camera');
  try {
    const res = await call<TurnResult>('/v1/vision', { json: { image, lang: s.lang, question, source: 'screen' }, signal: ctrl.signal });
    if (ctrl.signal.aborted) return;
    useStore.getState().completeTurn(res);
    await say(res.reply, ctrl, res.mood ?? 'calm');
  } catch (e) {
    if (isAbort(e)) return;
    useStore.getState().failTurn(e instanceof CoreError ? explain(e, s.lang) : String(e));
    earcon('error');
  } finally {
    if (!parent && inflight === ctrl) inflight = null;
    useStore.getState().settle();
  }
}

// ---------------------------------------------------------------- generated images

/** Show a picture PLAG just drew (the file is in Pictures\PLAG). */
export async function showImage(id: string, prompt: string): Promise<void> {
  try {
    const bytes = await call<ArrayBuffer>(`/v1/images/${encodeURIComponent(id)}`);
    const url = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }));
    useStore.getState().setImage({ id, prompt, url });
  } catch (e) {
    useStore.getState().setError(e instanceof CoreError ? e.message : String(e));
  }
}

export async function openImage(id: string): Promise<void> {
  await call(`/v1/images/${encodeURIComponent(id)}/open`, { method: 'POST' }).catch(() => undefined);
}

export async function revealImage(id: string): Promise<void> {
  await call(`/v1/images/${encodeURIComponent(id)}/reveal`, { method: 'POST' }).catch(() => undefined);
}

// ---------------------------------------------------------------- 3D models and weather simulations

/** Show a 3D model PLAG just built (the .glb is in Documents\PLAG\3D). */
async function showModel3d(id: string, prompt: string): Promise<void> {
  try {
    const bytes = await call<ArrayBuffer>(`/v1/models3d/${encodeURIComponent(id)}`);
    const url = URL.createObjectURL(new Blob([bytes], { type: 'model/gltf-binary' }));
    useStore.getState().setModel3d({ id, prompt, url });
  } catch (e) {
    useStore.getState().setError(e instanceof CoreError ? e.message : String(e));
  }
}

export async function openModel3d(id: string): Promise<void> {
  try {
    await call(`/v1/models3d/${encodeURIComponent(id)}/open`, { method: 'POST' });
  } catch (e) {
    useStore.getState().setError(e instanceof CoreError ? e.message : String(e));
  }
}

export async function revealModel3d(id: string): Promise<void> {
  await call(`/v1/models3d/${encodeURIComponent(id)}/reveal`, { method: 'POST' }).catch(() => undefined);
}

/** Show a FourCastNet simulation: one global map per 6 hours, played like a short film. */
async function showSim(id: string, label: string, hours: number[]): Promise<void> {
  try {
    const frames = await Promise.all(hours.map((_, i) => call<ArrayBuffer>(`/v1/weather/${encodeURIComponent(id)}/${i}`)));
    const urls = frames.map((b) => URL.createObjectURL(new Blob([b], { type: 'image/png' })));
    useStore.getState().setSim({ id, label, hours, urls });
  } catch (e) {
    useStore.getState().setError(e instanceof CoreError ? e.message : String(e));
  }
}

export async function revealSim(id: string): Promise<void> {
  await call(`/v1/weather/${encodeURIComponent(id)}/reveal`, { method: 'POST' }).catch(() => undefined);
}

/** "Gen an image of this": one camera frame to the core, which describes it (Gemini) and draws it (FLUX). */
export async function imagineThroughCamera(style: string, parent: AbortController, intro?: Promise<void>): Promise<void> {
  const s = useStore.getState();
  if (s.halted) return;
  const fresh = !camera.active;
  if (fresh && !(await openCamera())) return;
  await camera.ready();
  if (fresh) await sleep(700); // let exposure settle
  const image = camera.capture();
  if (!image) {
    s.setError(text(s.lang, 'The camera has no picture yet. Try again.', 'कैमरा में अभी तस्वीर नहीं है। फिर कोशिश करें।'));
    return;
  }
  s.beginTurn(null, 'camera');
  try {
    const res = await call<TurnResult>('/v1/imagine', { json: { image, lang: s.lang, style }, signal: parent.signal });
    if (parent.signal.aborted) return;
    useStore.getState().completeTurn(res);
    if (res.client?.type === 'image' && res.client.id) await showImage(res.client.id, res.client.prompt ?? '');
    await intro?.catch(() => undefined);
    await say(res.reply, parent, res.mood ?? 'cheerful');
  } catch (e) {
    if (isAbort(e)) return;
    useStore.getState().failTurn(e instanceof CoreError ? explain(e, s.lang) : String(e));
    earcon('error');
  } finally {
    useStore.getState().settle();
  }
}

// ---------------------------------------------------------------- memory and reminders

export async function refreshMemory(): Promise<void> {
  try {
    useStore.getState().setMemories((await call<{ memories: Memory[] }>('/v1/memory')).memories);
  } catch {
    /* shown as empty until the core answers */
  }
}

export async function refreshReminders(): Promise<void> {
  try {
    useStore.getState().setReminders((await call<{ reminders: Reminder[] }>('/v1/reminders')).reminders);
  } catch {
    /* shown as empty until the core answers */
  }
}

export async function deleteMemory(id: string): Promise<void> {
  await call(`/v1/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(() => undefined);
  await refreshMemory();
}

export async function cancelReminder(id: string): Promise<void> {
  await call(`/v1/reminders/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(() => undefined);
  await refreshReminders();
}

/** A reminder is due: a tray notification right away, then PLAG says it as soon as it isn't busy. */
export async function onReminder(r: { text: string; late_minutes?: number }): Promise<void> {
  const s = useStore.getState();
  const hindi = s.lang === 'hi';
  const late = (r.late_minutes ?? 0) >= 2;
  const line = hindi
    ? `रिमाइंडर: ${r.text}` + (late ? ` (${r.late_minutes} मिनट पहले का)` : '')
    : `Reminder: ${r.text}` + (late ? ` (from ${r.late_minutes} minutes ago)` : '');
  window.plag?.notify('PLAG reminder', r.text);
  for (let i = 0; i < 40; i++) {
    const now = useStore.getState();
    if (!(now.listening || now.pending || now.speaking || mic.active)) break;
    await sleep(500); // don't talk over the user or over PLAG
  }
  if (useStore.getState().halted) return;
  useStore.getState().note(line, 'reminder', hindi ? 'hi' : 'en');
  earcon('start');
  await say(line, undefined, 'calm');
}

/** PLAG speaking up on its own when a background job ends: a tray notification, then said once it isn't busy. */
async function speakUp(text: string, kind: string, lang = 'en', mood = 'calm'): Promise<void> {
  window.plag?.notify(`PLAG · ${kind}`, text);
  for (let i = 0; i < 40; i++) {
    const now = useStore.getState();
    if (!(now.listening || now.pending || now.speaking || mic.active)) break;
    await sleep(500); // don't talk over the user or over PLAG
  }
  if (useStore.getState().halted) return;
  useStore.getState().note(text, kind, lang === 'en' ? 'en' : 'hi');
  earcon('start');
  await say(text, undefined, mood);
}

/** A 3D model built in the background is ready: into the viewer, and PLAG says so. */
export async function onModel3dReady(m: { id: string; prompt: string; text: string; lang?: string }): Promise<void> {
  await showModel3d(m.id, m.prompt);
  await speakUp(m.text, '3D model', m.lang, 'cheerful');
}

/** Something PLAG has to tell you that isn't an answer to a request (a background build failed). */
export async function onPlagSay(m: { text: string; kind?: string; lang?: string; mood?: string }): Promise<void> {
  await speakUp(m.text, m.kind ?? 'PLAG', m.lang, m.mood ?? 'calm');
}

// ---------------------------------------------------------------- settings (ElevenLabs, listening)

export interface Settings {
  eleven_voice_id: string;
  eleven_model: string;
  eleven_speak: boolean;
  eleven_hear: boolean;
  eleven_realtime: boolean;
  eleven_max_chars: number;
  eleven_reserve_pct: number;
  speed: number;
  turn: 'fast' | 'normal' | 'patient';
  wake_sensitivity: 'low' | 'normal' | 'high';
  follow_up: 'off' | 'questions' | 'always';
  home_city: string;
  use_location: boolean;
  always_listen: boolean;
  voice_agent: boolean;
  eleven_agent_id: string;
  barge_in?: boolean; // talk over PLAG to interrupt it
  voice_engine: VoiceEngine;
  sarvam_speaker: string;
  edge_voice: string;
}

export type VoiceEngine = 'auto' | 'sarvam' | 'nvidia' | 'edge' | 'elevenlabs' | 'local';
/** What the core can do right now, and who speaks first. */
export interface Live {
  hear: boolean;
  stream: boolean;
  agent: boolean;
  voice: VoiceEngine | 'none';
  sarvam: { configured: boolean; usable: boolean; error: string | null; speaker: string; speakers: string[] };
  nvidia: { configured: boolean; usable: boolean };
  edge: { available: boolean; usable: boolean; voices: string[] };
}

/** Settings -> Clear cache: saved spoken replies and idle models in the core, and the dashboard's own caches. */
export async function clearCaches(): Promise<string> {
  let freed = '';
  try {
    const r = await call<{ files: number; mb: number }>('/v1/cache/clear', { method: 'POST' });
    freed = `${r.files} saved replies (${r.mb} MB)`;
  } catch {
    freed = 'the core’s cache (not reachable right now)';
  }
  await window.plag?.clearCache().catch(() => false);
  return `Cleared ${freed} and the dashboard’s web caches.`;
}
export interface ElevenStatus {
  configured: boolean;
  usable: boolean;
  error: string | null;
  usage: { used: number; limit: number; reset: number } | null;
  voice: string;
  model: string;
}
export type SettingsReply = { settings: Settings; eleven: ElevenStatus; live?: Live };

// how long a pause ends your turn: with streamed hearing the words are ready ~0.3 s later, so PLAG answers sooner
// 2026-09-26: at 0.45 s ("fast") a short pause mid-sentence ended the turn ("Send a message" / "Chachu bangalore" came
// as two commands). Patient waits 1.3 s, so you can think between words.
const SILENCE_MS = { fast: 700, normal: 1000, patient: 1300 };

function applySettings(r: SettingsReply): SettingsReply {
  const s = r.settings;
  endpoint.silenceMs = SILENCE_MS[s.turn] ?? 650;
  prefs.followUp = s.follow_up ?? 'always';
  // what the core can do right now (NVIDIA or ElevenLabs); an older core only reports ElevenLabs
  prefs.realtime = r.live ? r.live.hear : r.eleven.configured && r.eleven.usable && s.eleven_hear && s.eleven_realtime;
  prefs.stream = r.live ? r.live.stream : r.eleven.configured && r.eleven.usable && s.eleven_speak;
  prefs.maxChars = s.eleven_max_chars;
  prefs.alwaysListen = s.always_listen;
  // out of ElevenLabs credits: don't try the agent (and wait for it to fail) on every "PLAG"
  prefs.voiceAgent = r.live ? r.live.agent : s.voice_agent && r.eleven.configured;
  prefs.bargeIn = s.barge_in ?? true;
  useStore.getState().setAlwaysListen(s.always_listen);
  return r;
}

// What a command the agent asked for made (a picture, a 3D model, a PDF, a weather film), shown on the stage. The
// agent says the result itself, so nothing is spoken here.
onAgentResult(async (res) => {
  const c = res.client;
  if (!c) return;
  if (c.type === 'image' && c.id) await showImage(c.id, c.prompt ?? '');
  else if (c.type === 'model3d' && c.id) await showModel3d(c.id, c.prompt ?? '');
  else if (c.type === 'weather_sim' && c.id) await showSim(c.id, c.label ?? '', c.hours ?? []);
  else if (c.type === 'document' && c.id) {
    useStore.getState().setDoc({ id: c.id, title: c.title ?? '', kind: c.kind ?? 'document', summary: c.summary ?? '',
      words: c.words ?? 0, sources: c.sources ?? 0 });
  } else if (c.type === 'ui' && c.command && !['halt', 'stop'].includes(c.command)) await applyUi(c.command);
  else if (c.type === 'ui' && c.command === 'halt') await haltPlag();
});

/**
 * The ear button. On: PLAG hears everything you say, no "PLAG" needed (it never takes its own voice for you).
 * Off: it answers only after "PLAG". The wake listener has to be on for either.
 */
export async function setAlwaysListen(on: boolean): Promise<void> {
  useStore.getState().setAlwaysListen(on);
  prefs.alwaysListen = on;
  if (on && !useStore.getState().wakeOn) await setWake(true);
  try {
    await saveSettings({ always_listen: on });
  } catch {
    useStore.getState().setAlwaysListen(!on);
    prefs.alwaysListen = !on;
  }
}

// ---------------------------------------------------------------- documents (writing and reports, as PDFs)

export async function openDoc(id: string): Promise<void> {
  await call(`/v1/docs/${encodeURIComponent(id)}/open`, { method: 'POST' }).catch(() => undefined);
}

export async function revealDoc(id: string): Promise<void> {
  await call(`/v1/docs/${encodeURIComponent(id)}/reveal`, { method: 'POST' }).catch(() => undefined);
}

// ---------------------------------------------------------------- directions (live map + turn call-outs)

type RouteClient = Omit<RouteView, 'here' | 'next' | 'toNext'>;
let navTimer = 0;
let navSaid = { step: -1, near: -1, arrived: false }; // what's been called out, so nothing is said twice

function metres(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const r = Math.PI / 180;
  const dla = (b.lat - a.lat) * r;
  const dln = (b.lng - a.lng) * r;
  const h = Math.sin(dla / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dln / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

/** The next turn from where you are: the one after the nearest turn point you've reached (within 40 m). */
function nextStep(steps: RouteStep[], here: { lat: number; lng: number }): [number, number] {
  if (!steps.length) return [0, 0];
  let nearest = 0;
  steps.forEach((s, i) => { if (metres(here, s) < metres(here, steps[nearest])) nearest = i; });
  const i = metres(here, steps[nearest]) < 40 ? Math.min(nearest + 1, steps.length - 1) : nearest;
  return [i, metres(here, steps[i])];
}

const say2 = (lang: string, en: string, hi: string) => (lang === 'en' ? en : hi);
const dist = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(1).replace(/\.0$/, '')} km` : `${Math.max(50, Math.round(m / 50) * 50)} metres`);

function startNav(r: RouteClient): void {
  window.clearInterval(navTimer);
  const here = r.origin;
  const [next, toNext] = nextStep(r.steps, here);
  navSaid = { step: next, near: -1, arrived: false };
  useStore.getState().setRoute({ ...r, here, next, toNext });
  navTimer = window.setInterval(() => void followNav(), 15000);
}

/** Every 15 s while a trip is on: where you are now, the next turn, and a call-out when it's close. */
async function followNav(): Promise<void> {
  const r = useStore.getState().route;
  if (!r) { window.clearInterval(navTimer); return; }
  let here: { lat: number; lng: number; accuracy_m?: number };
  try {
    here = await call<{ lat: number; lng: number; accuracy_m: number }>('/v1/location/now');
  } catch {
    return; // no fix this time: try again at the next tick
  }
  const [next, toNext] = nextStep(r.steps, here);
  useStore.getState().setRoute({ ...r, here, next, toNext });
  const s = useStore.getState();
  if (s.speaking || s.listening || s.pending) return; // never talk over you or a reply
  const step = r.steps[next];
  if (!navSaid.arrived && metres(here, r.dest) < 60) {
    navSaid.arrived = true;
    window.clearInterval(navTimer);
    await say(say2(r.lang, `You have arrived at ${r.dest.name}, sir.`, `Sir, aap ${r.dest.name} pahunch gaye.`), undefined, 'cheerful', false);
    return;
  }
  if (!step) return;
  if (toNext < 150 && navSaid.near !== next) {
    navSaid = { ...navSaid, step: next, near: next };
    await say(say2(r.lang, `Now, ${step.text.toLowerCase()}.`, `Ab, ${step.text}.`), undefined, 'calm', false);
  } else if (navSaid.step !== next) {
    navSaid = { ...navSaid, step: next };
    await say(say2(r.lang, `In ${dist(toNext)}, ${step.text.toLowerCase()}.`, `${dist(toNext)} mein, ${step.text}.`), undefined, 'calm', false);
  }
}

/** End the trip: the map closes and PLAG stops asking where you are. */
export async function stopNav(announce = true): Promise<void> {
  window.clearInterval(navTimer);
  navTimer = 0;
  useStore.getState().setRoute(null);
  if (announce) await say(text(useStore.getState().lang, 'Navigation ended, sir.', 'नेविगेशन बंद, सर।'), undefined, 'calm', false);
}

/** The card's viewer shows it as saved (after the Save button, or "save" said to PLAG). */
function markSaved(id: string): void {
  const s = useStore.getState();
  if (s.doc?.id === id) s.setDoc({ ...s.doc, saved: true });
  if (s.model3d?.id === id) s.setModel3d({ ...s.model3d, saved: true });
}

/** Save: the PDF or 3D model goes to Documents\PLAG. Nothing PLAG makes is written to the laptop before this. */
export async function saveDraft(id: string): Promise<void> {
  try {
    const r = await call<{ path: string; folder: string }>(`/v1/drafts/${encodeURIComponent(id)}/save`, { method: 'POST' });
    markSaved(id);
    useStore.getState().setNotice(`Saved to ${r.folder}`);
  } catch (e) {
    useStore.getState().setError(e instanceof CoreError ? e.message : String(e));
  }
}

// ---------------------------------------------------------------- a picture attached in the chat

/** A picture you chose, made into a JPEG of at most 1600 px (smaller upload, same detail for the AI). */
export async function pictureToJpeg(file: File): Promise<{ jpeg: string; url: string }> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, 'image/jpeg', 0.88));
  if (!blob) throw new Error('That picture could not be read.');
  const jpeg = await new Promise<string>((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result));
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(blob);
  });
  return { jpeg, url: URL.createObjectURL(blob) };
}

/** Ask about a picture you attached ("what's written here?", "solve this"), answered by Gemini and spoken. */
export async function askAboutPicture(jpeg: string, url: string, question: string): Promise<void> {
  const s = useStore.getState();
  if (s.halted) return;
  inflight?.abort();
  const ctrl = new AbortController();
  inflight = ctrl;
  s.setImage({ id: '', prompt: question || 'Your picture', url, local: true });
  s.beginTurn(question || null, 'text');
  try {
    const res = await call<TurnResult>('/v1/vision', { json: { image: jpeg, lang: s.lang, question, source: 'upload' }, signal: ctrl.signal });
    if (ctrl.signal.aborted) return;
    useStore.getState().completeTurn(res);
    await say(res.reply, ctrl, 'calm');
  } catch (e) {
    if (isAbort(e)) return;
    useStore.getState().failTurn(e instanceof CoreError ? explain(e, s.lang) : String(e));
    earcon('error');
  } finally {
    if (inflight === ctrl) inflight = null;
    useStore.getState().settle();
  }
}

export async function loadSettings(): Promise<SettingsReply | null> {
  try {
    return applySettings(await call<SettingsReply>('/v1/settings'));
  } catch {
    return null;
  }
}

export async function saveSettings(changes: Partial<Settings>): Promise<SettingsReply> {
  return applySettings(await call<SettingsReply>('/v1/settings', { json: { changes } }));
}

/** Your key goes to the core, which checks it with ElevenLabs and keeps it in Windows Credential Manager. */
export async function saveElevenKey(key: string): Promise<string | null> {
  try {
    await call('/v1/elevenlabs/key', { json: { key } });
    return null;
  } catch (e) {
    return e instanceof CoreError ? e.message : String(e);
  }
}

export async function removeElevenKey(): Promise<void> {
  await call('/v1/elevenlabs/key', { method: 'DELETE' }).catch(() => undefined);
}

/** Your Sarvam key goes to the core, which checks it with Sarvam and keeps it in Windows Credential Manager. */
export async function saveSarvamKey(key: string): Promise<string | null> {
  try {
    await call('/v1/sarvam/key', { json: { key } });
    await loadSettings(); // the voice switches to Sarvam (Auto) right away
    return null;
  } catch (e) {
    return e instanceof CoreError ? e.message : String(e);
  }
}

export async function removeSarvamKey(): Promise<void> {
  await call('/v1/sarvam/key', { method: 'DELETE' }).catch(() => undefined);
  await loadSettings();
}

export async function elevenVoices(): Promise<{ id: string; name: string; category: string }[]> {
  try {
    return (await call<{ voices: { id: string; name: string; category: string }[] }>('/v1/elevenlabs/voices')).voices;
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------- Google

/** Connect on the Google row: saves your OAuth client file the first time, then opens Google's sign-in page. */
export async function connectGoogle(): Promise<void> {
  const s = useStore.getState();
  try {
    await call('/v1/google/connect', { method: 'POST' });
  } catch (e) {
    if (!(e instanceof CoreError) || e.code !== 'no_client') {
      s.setError(e instanceof CoreError ? e.message : String(e));
      return;
    }
    const file = await window.plag?.pickGoogleClient();
    if (!file) return; // picker closed
    try {
      await call('/v1/google/client', { json: { file } });
      await call('/v1/google/connect', { method: 'POST' });
    } catch (err) {
      s.setError(err instanceof CoreError ? err.message : String(err));
      return;
    }
  }
  s.setNotice(text(s.lang, 'Finish signing in to Google in your browser, then come back.',
    'ब्राउज़र में Google साइन-इन पूरा कीजिए, फिर वापस आइए।'));
}

export async function disconnectGoogle(): Promise<void> {
  await call('/v1/google/disconnect', { method: 'POST' }).catch(() => undefined);
}

// ---------------------------------------------------------------- stop / halt

/** Stop whatever PLAG is doing right now (Esc, tapping the mic, "PLAG, stop"). */
export async function stopAll(): Promise<void> {
  void stopAgent();
  inflight?.abort();
  inflight = null;
  followUpApproval = null;
  listenMode = null;
  live?.cancel();
  live = null;
  speaker.stop();
  stopLocal();
  bargedIn = false;
  if (mic.active) {
    const ms = mic.durationMs;
    const wasListening = !mic.monitor; // the mic may only have been waiting for you to cut in
    await mic.cancel();
    if (wasListening) useStore.getState().endListening(ms, false);
  }
  call('/v1/cancel', { method: 'POST' }).catch(() => undefined);
  const s = useStore.getState();
  s.setSpeaking(false);
  s.settle();
}

/** Kill switch: stop everything, close the camera, refuse new work until resumed. */
export async function haltPlag(): Promise<void> {
  await stopAll();
  camera.stop();
  useStore.getState().setHalted(true);
  try {
    await call('/v1/killswitch', { method: 'POST' });
  } catch {
    /* core unreachable: the UI stays halted and the mic stays off */
  }
}

export async function resumePlag(): Promise<void> {
  try {
    await call('/v1/resume', { method: 'POST' });
    useStore.getState().setHalted(false);
  } catch (e) {
    useStore.getState().setError(e instanceof CoreError ? explain(e, useStore.getState().lang) : String(e));
  }
}
