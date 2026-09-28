import { create } from 'zustand';
import type { CoreEvent } from '../lib/core';
import { live } from '../lib/live';

export type Status =
  | 'booting' | 'idle' | 'listening' | 'thinking' | 'executing' | 'speaking' | 'halted' | 'offline' | 'watching';
export type Lang = 'auto' | 'en' | 'hi';
export type StepState = 'queued' | 'running' | 'done' | 'warn' | 'failed' | 'waiting' | 'skipped';

export interface Step {
  step: string;
  state: StepState;
  detail?: string;
  ms?: number | null;
  label?: string | null; // a plan's step: "Search YouTube: honey singh"
}
export interface Task {
  id: string | null;
  input: 'voice' | 'text' | 'camera' | 'approval';
  steps: Step[];
  done: boolean;
  ms?: number;
}
export interface Msg {
  id: string;
  role: 'you' | 'plag';
  text: string;
  lang?: string;
  meta?: string;
  failed?: boolean;
  links?: { title: string; url: string; site: string }[]; // sources you can open (only when you click)
}
export interface Metrics {
  cpu: number;
  cpu_freq: { current: number; max: number } | null;
  cores: number;
  mem: { pct: number; used_gb: number; total_gb: number };
  disk: { busy_pct: number; read_bps: number; write_bps: number };
  net: { up_bps: number; down_bps: number };
  battery: { pct: number; plugged: boolean } | null;
}
export interface Proc {
  name: string;
  count: number;
  cpu: number;
  mem_mb: number;
}
export interface Connector {
  id: string;
  name: string;
  role: string;
  state: string;
  detail: string;
  action?: 'connect' | 'disconnect'; // a button on the row (Google)
}
export interface Approval {
  id: string;
  kind: 'whatsapp' | 'calendar';
  name: string;
  phone_tail: string;
  message: string;
  expiresAt: number;
}
export interface TurnResult {
  task_id: string;
  transcript: string;
  language: string;
  reply: string;
  action: { type: string; label?: string };
  result: { ok: boolean; detail: string } | null;
  approval?: (Omit<Approval, 'expiresAt'> & { expires_in: number }) | null;
  approval_done?: string | null;
  client?: {
    type: string; command?: string; question?: string; style?: string; id?: string; prompt?: string;
    hours?: number[]; variable?: string; label?: string; // a weather simulation's maps
    title?: string; kind?: string; summary?: string; words?: number; sources?: number; // a PDF PLAG wrote
    steps?: RouteStep[]; // directions (the rest of RouteView comes with them)
    text?: string; // copy: a booking link put on the clipboard
  } | null;
  vision?: { label: string; confidence: string } | null;
  sources?: { title: string; url: string; site: string }[]; // Wikipedia and news links behind an answer
  mood?: string;
  model: string;
  ms: number;
  expects_reply?: boolean; // PLAG asked something: listen for the answer without "PLAG"
  silent?: boolean; // a follow-up window heard only noise: nothing to show or say
}
export interface WakeStatus {
  enabled: boolean;
  state: 'off' | 'starting' | 'listening' | 'error';
  error?: string | null;
}
export interface Memory {
  id: string;
  text: string;
  created: string;
}
export interface Reminder {
  id: string;
  text: string;
  due: string; // local ISO time
}
export type Tab = 'conversation' | 'inbox' | 'memory' | 'reminders';
/** An account you connected by its address: you signed in on the real site; PLAG watches it for new messages. */
export interface Account {
  id: string;
  name: string; // "LinkedIn"
  host: string; // "linkedin.com"
  service: string;
  color: string;
  url: string;
  watch: boolean;
  state: 'starting' | 'signin' | 'watching' | 'paused' | 'offline' | 'limit';
  unread: number;
  added: string;
}
/** A new message on one of your accounts, with the reply PLAG drafted (you send it). */
export interface InboxItem {
  id: string;
  account: string; // an Account id, or "google" for Gmail through the Google connection
  service: string;
  service_name: string;
  sender: string;
  subject: string;
  text: string;
  url: string;
  kind: 'message' | 'code' | 'count';
  summary: string;
  reply: string;
  urgency: 'low' | 'normal' | 'high';
  status: 'new' | 'done' | 'dismissed';
  created: string;
}
export interface GenImage {
  id: string;
  prompt: string;
  url: string; // blob: URL of the file in Pictures\PLAG
  local?: boolean; // a picture you attached in the chat (no Open / Show in folder: it's your file)
}
export interface Doc {
  id: string;
  title: string;
  kind: string; // essay, report, letter…
  summary: string;
  words: number;
  sources: number; // a news report's sources
  saved?: boolean; // a draft until you say "save" (then it's in Documents\PLAG)
}
export interface Model3D {
  id: string;
  prompt: string;
  url: string; // blob: URL of the .glb (a draft in PLAG's memory until you save it)
  saved?: boolean;
}
export type Turn = 'depart' | 'left' | 'right' | 'slight-left' | 'slight-right' | 'sharp-left' | 'sharp-right' | 'uturn'
  | 'roundabout' | 'straight' | 'arrive';
export interface RouteStep { text: string; turn: Turn; distance_m: number; lat: number; lng: number }
/** Directions on the live map: the route from the core, and where you are now (updated while you travel). */
export interface RouteView {
  dest: { name: string; address: string; lat: number; lng: number };
  origin: { lat: number; lng: number };
  distance_m: number;
  duration_s: number;
  steps: RouteStep[];
  path: [number, number][];
  by: string;
  traffic: boolean;
  lang: string;
  here: { lat: number; lng: number; accuracy_m?: number };
  next: number; // index of the next turn
  toNext: number; // metres to it
}
export interface WeatherSim {
  id: string;
  label: string; // "temperature"
  hours: number[]; // hours ahead of each map: 0, 6, 12…
  urls: string[]; // blob: URLs of the maps in Pictures\PLAG\Weather
}
/** Listening without "PLAG" after a reply: for the answer to PLAG's question, or a short window for a follow-up. */
export type FollowUp = 'answer' | 'window' | null;

type ServerState = 'idle' | 'thinking' | 'executing' | 'halted';
type Series = { cpu: number[]; mem: number[]; disk: number[]; down: number[] };
type Prefs = { lang: Lang; voice: boolean; wake: boolean };

const HISTORY = 60; // one minute of vitals at 1 Hz
const push = (a: number[], v: number) => (a.length >= HISTORY ? [...a.slice(1), v] : [...a, v]);

function loadPrefs(): Prefs {
  try {
    const p = JSON.parse(localStorage.getItem('plag.prefs') ?? '{}');
    return { lang: ['auto', 'en', 'hi'].includes(p.lang) ? p.lang : 'auto', voice: p.voice !== false, wake: p.wake !== false };
  } catch {
    return { lang: 'auto', voice: true, wake: true };
  }
}
function savePrefs(p: Prefs) {
  try {
    localStorage.setItem('plag.prefs', JSON.stringify(p));
  } catch {
    /* storage unavailable: preferences last for this session */
  }
}

function upsertStep(steps: Step[], next: Step): Step[] {
  const i = steps.findIndex((s) => s.step === next.step);
  if (i === -1) return [...steps, next];
  const copy = steps.slice();
  copy[i] = { ...copy[i], ...next };
  return copy;
}

export interface PlagState {
  booted: boolean;
  coreUp: boolean;
  server: ServerState;
  listening: boolean;
  speaking: boolean;
  halted: boolean;
  pending: boolean;
  cloud: boolean;
  lang: Lang;
  voiceOn: boolean;
  wakeOn: boolean;
  wake: WakeStatus;
  woke: number; // timestamp of the last wake word, drives a flash in the UI
  cameraOn: boolean;
  vision: { label: string; confidence: string } | null;
  approval: Approval | null;
  heard: string;
  heardLang?: string;
  reply: string;
  replyLang?: string;
  replyId: number;
  error: string | null;
  notice: string | null;
  task: Task | null;
  messages: Msg[];
  memories: Memory[];
  reminders: Reminder[];
  inbox: InboxItem[];
  accounts: Account[];
  tab: Tab;
  image: GenImage | null;
  model3d: Model3D | null;
  sim: WeatherSim | null;
  followUp: FollowUp;
  visible: boolean; // false while hidden in the tray or minimized: nothing is drawn
  doc: Doc | null; // the PDF PLAG just wrote
  alwaysListen: boolean; // the ear button
  agentMode: 'speaking' | 'listening' | null; // a live ElevenLabs agent conversation (null: none)
  metrics: Metrics | null;
  series: Series;
  procs: Proc[];
  connectors: Connector[];

  setLang(lang: Lang): void;
  setVoice(on: boolean): void;
  setWakeOn(on: boolean): void;
  setCoreUp(up: boolean): void;
  handleEvent(e: CoreEvent): void;
  markWoke(): void;
  beginListening(followUp?: FollowUp): void;
  setHeard(text: string): void;
  endListening(ms: number, heard: boolean): void;
  beginTurn(text: string | null, input?: Task['input']): void;
  completeTurn(r: TurnResult): void;
  failTurn(message: string): void;
  setSpeaking(on: boolean): void;
  setCloud(on: boolean): void;
  setNotice(n: string | null): void;
  setError(e: string | null): void;
  setHalted(h: boolean): void;
  setCamera(on: boolean): void;
  setApproval(a: Approval | null): void;
  sayLocal(text: string, lang?: string): void;
  note(text: string, meta: string, lang?: string): void;
  setMemories(m: Memory[]): void;
  setReminders(r: Reminder[]): void;
  setInbox(i: InboxItem[]): void;
  setAccounts(a: Account[]): void;
  setTab(t: Tab): void;
  setImage(i: GenImage | null): void;
  setModel3d(m: Model3D | null): void;
  setSim(w: WeatherSim | null): void;
  route: RouteView | null;
  setRoute(r: RouteView | null): void;
  setVisible(v: boolean): void;
  setDoc(d: Doc | null): void;
  setAlwaysListen(on: boolean): void;
  setAgentMode(m: 'speaking' | 'listening' | null): void;
  agentSaid(role: 'user' | 'agent', text: string): void;
  addStep(s: Step): void;
  settle(): void;
}

const prefs = loadPrefs();

// Closing what's on the stage frees its file from memory (blob: URLs hold the whole file).
function clearImage(s: PlagState): Partial<PlagState> {
  if (s.image) URL.revokeObjectURL(s.image.url);
  return { image: null };
}
function clearModel(s: PlagState): Partial<PlagState> {
  if (s.model3d) URL.revokeObjectURL(s.model3d.url);
  return { model3d: null };
}
function clearSim(s: PlagState): Partial<PlagState> {
  s.sim?.urls.forEach((u) => URL.revokeObjectURL(u));
  return { sim: null };
}

export const useStore = create<PlagState>()((set, get) => ({
  booted: false,
  coreUp: false,
  server: 'idle',
  listening: false,
  speaking: false,
  halted: false,
  pending: false,
  cloud: false,
  lang: prefs.lang,
  voiceOn: prefs.voice,
  wakeOn: prefs.wake,
  wake: { enabled: false, state: 'off' },
  woke: 0,
  cameraOn: false,
  vision: null,
  approval: null,
  heard: '',
  reply: '',
  replyId: 0,
  error: null,
  notice: null,
  task: null,
  messages: [],
  memories: [],
  reminders: [],
  inbox: [],
  accounts: [],
  tab: 'conversation',
  image: null,
  model3d: null,
  sim: null,
  route: null,
  setRoute: (route) => set({ route }),
  followUp: null,
  visible: true,
  doc: null,
  alwaysListen: false,
  agentMode: null,
  metrics: null,
  series: { cpu: [], mem: [], disk: [], down: [] },
  procs: [],
  connectors: [],

  setLang: (lang) => {
    set({ lang });
    const s = get();
    savePrefs({ lang, voice: s.voiceOn, wake: s.wakeOn });
  },
  setVoice: (voiceOn) => {
    set({ voiceOn });
    const s = get();
    savePrefs({ lang: s.lang, voice: voiceOn, wake: s.wakeOn });
  },
  setWakeOn: (wakeOn) => {
    set({ wakeOn });
    const s = get();
    savePrefs({ lang: s.lang, voice: s.voiceOn, wake: wakeOn });
  },
  setCoreUp: (up) => set((s) => ({ coreUp: up, booted: s.booted || up })),

  handleEvent: (e) =>
    set((s): Partial<PlagState> => {
      switch (e.topic) {
        case 'hello': {
          const halted = e.data.state === 'halted';
          return {
            booted: true, halted, server: halted ? 'halted' : 'idle',
            connectors: e.data.connectors ?? s.connectors, wake: e.data.wake ?? s.wake,
          };
        }
        case 'status.changed': {
          const st = e.data.state as ServerState;
          if (st === 'halted') return { server: 'halted', halted: true };
          return { server: st, halted: st === 'idle' ? false : s.halted };
        }
        case 'task.started': {
          // a voice task already has its local "listen" step; keep it and adopt the core's id
          const carry = s.task && !s.task.id && s.task.input === 'voice' ? s.task.steps : [];
          return { task: { id: e.task_id ?? null, input: e.data.input, steps: carry, done: false } };
        }
        case 'task.step':
          if (!s.task || (s.task.id && e.task_id !== s.task.id)) return {};
          return { task: { ...s.task, steps: upsertStep(s.task.steps, e.data as Step) } };
        case 'task.completed':
          if (!s.task || e.task_id !== s.task.id) return {};
          return { task: { ...s.task, done: true, ms: e.data.ms } };
        case 'system.metrics': {
          const m = e.data as Metrics;
          return {
            metrics: m,
            series: {
              cpu: push(s.series.cpu, m.cpu),
              mem: push(s.series.mem, m.mem.pct),
              disk: push(s.series.disk, m.disk.busy_pct),
              down: push(s.series.down, m.net.down_bps),
            },
          };
        }
        case 'system.processes':
          return { procs: e.data as Proc[] };
        case 'connectors':
          return { connectors: e.data as Connector[] };
        case 'wake.state':
          return { wake: e.data as WakeStatus };
        case 'wake.level':
          live.ambient = Math.min(1, (e.data.v ?? 0) / 55);
          return {};
        case 'killswitch.engaged':
          return { halted: true, server: 'halted', approval: null };
        default:
          return {};
      }
    }),

  markWoke: () => set({ woke: Date.now(), error: null, notice: null }),
  beginListening: (followUp = null) =>
    set({
      listening: true,
      followUp,
      error: null,
      notice: null,
      task: { id: null, input: 'voice', steps: [{ step: 'listen', state: 'running',
        detail: followUp === 'answer' ? 'Listening for your answer, no “PLAG” needed'
          : followUp === 'window' ? 'Still listening for a follow-up' : 'Microphone on' }], done: false },
    }),
  setHeard: (heard) => set({ heard, heardLang: undefined }),
  endListening: (ms, heard) =>
    set((s) => ({
      listening: false,
      followUp: null,
      task: s.task
        ? {
            ...s.task,
            steps: upsertStep(s.task.steps, {
              step: 'listen',
              state: heard ? 'done' : 'failed',
              detail: heard ? 'Voice captured, microphone off' : 'No speech heard',
              ms: Math.round(ms),
            }),
            done: !heard,
          }
        : null,
    })),
  beginTurn: (text, input = 'text') =>
    set((s) => ({
      pending: true,
      cloud: true,
      server: 'thinking',
      error: null,
      notice: null,
      reply: '', // a new command never shows the previous answer
      heard: text ?? (input === 'camera' ? '' : s.heard),
      heardLang: undefined,
      task: input === 'voice' && s.task ? s.task : { id: null, input, steps: [], done: false },
    })),
  completeTurn: (r) =>
    set((s) => {
      if (s.messages.some((m) => m.id === `${r.task_id}:plag`)) return {};
      const failed = r.result ? !r.result.ok : false;
      const meta = `${r.model} · ${r.ms < 1000 ? `${r.ms} ms` : `${(r.ms / 1000).toFixed(1)} s`}`;
      const next: Msg[] = [];
      if (r.transcript) next.push({ id: `${r.task_id}:you`, role: 'you', text: r.transcript, lang: r.language });
      next.push({ id: `${r.task_id}:plag`, role: 'plag', text: r.reply, lang: r.language, meta, failed,
        links: r.sources?.length ? r.sources : undefined });
      const approval = r.approval
        ? { id: r.approval.id, kind: r.approval.kind, name: r.approval.name, phone_tail: r.approval.phone_tail,
            message: r.approval.message, expiresAt: Date.now() + r.approval.expires_in * 1000 }
        : r.approval_done && s.approval?.id === r.approval_done ? null : s.approval;
      return {
        pending: false,
        cloud: false,
        heard: r.transcript || s.heard,
        heardLang: r.language,
        reply: r.reply,
        replyLang: r.language,
        replyId: s.replyId + 1,
        approval,
        vision: r.vision ?? s.vision,
        messages: [...s.messages, ...next].slice(-60),
      };
    }),
  failTurn: (message) =>
    set((s) => ({
      pending: false,
      cloud: false,
      error: message,
      task: s.task
        ? { ...s.task, done: true, steps: s.task.steps.map((st) => (st.state === 'running' ? { ...st, state: 'failed' } : st)) }
        : null,
    })),
  setSpeaking: (on) => set({ speaking: on }),
  setCloud: (on) => set({ cloud: on }),
  setNotice: (notice) => set({ notice }),
  setError: (error) => set({ error }),
  setHalted: (halted) =>
    set({ halted, server: halted ? 'halted' : 'idle', listening: false, speaking: false, pending: false, approval: halted ? null : get().approval }),
  setCamera: (cameraOn) => set({ cameraOn, vision: cameraOn ? get().vision : null }),
  setApproval: (approval) => set({ approval }),
  sayLocal: (text, lang) => set((s) => ({ reply: text, replyLang: lang, replyId: s.replyId + 1 })),
  // PLAG speaking up on its own (a reminder): shown on the stage and in the conversation
  note: (text, meta, lang) =>
    set((s) => ({
      reply: text,
      replyLang: lang,
      replyId: s.replyId + 1,
      messages: [...s.messages, { id: `note:${Date.now()}`, role: 'plag' as const, text, lang, meta }].slice(-60),
    })),
  setMemories: (memories) => set({ memories }),
  setReminders: (reminders) => set({ reminders }),
  setInbox: (inbox) => set({ inbox }),
  setAccounts: (accounts) => set({ accounts }),
  setTab: (tab) => set({ tab }),
  // steps measured here, not in the core (how soon PLAG's voice started)
  addStep: (st) => set((s) => (s.task ? { task: { ...s.task, steps: upsertStep(s.task.steps, st) } } : {})),
  // one thing on the stage at a time: a picture, a 3D model or a weather simulation
  setImage: (image) =>
    set((s) => {
      if (s.image && s.image.url !== image?.url) URL.revokeObjectURL(s.image.url); // free the previous picture
      return image ? { image, ...clearModel(s), ...clearSim(s), doc: null } : { image };
    }),
  setModel3d: (model3d) =>
    set((s) => (model3d ? { ...clearModel(s), ...clearImage(s), ...clearSim(s), model3d } : clearModel(s))),
  setSim: (sim) =>
    set((s) => (sim ? { ...clearSim(s), ...clearImage(s), ...clearModel(s), sim } : clearSim(s))),
  // hidden: the live charts start fresh when you come back (a minute of history isn't worth holding in the tray)
  setVisible: (visible) => set(visible ? { visible } : { visible, series: { cpu: [], mem: [], disk: [], down: [] }, procs: [] }),
  // a document card takes the stage like a picture does
  setDoc: (doc) => set((s) => (doc ? { ...clearImage(s), ...clearModel(s), ...clearSim(s), doc } : { doc })),
  setAlwaysListen: (alwaysListen) => set({ alwaysListen }),
  setAgentMode: (agentMode) => set({ agentMode }),
  // the agent conversation, on the stage and in the conversation panel
  agentSaid: (role, text) =>
    set((s) => {
      const msg: Msg = { id: `agent:${Date.now()}:${s.messages.length}`, role: role === 'user' ? 'you' : 'plag', text,
        meta: role === 'user' ? undefined : 'ElevenLabs agent' };
      const messages = [...s.messages, msg].slice(-60);
      return role === 'user'
        ? { messages, heard: text, heardLang: undefined, error: null, notice: null }
        : { messages, reply: text, replyLang: undefined, replyId: s.replyId + 1 };
    }),
  settle: () => set((s) => ({ pending: false, cloud: false, server: s.halted ? 'halted' : 'idle' })),
}));

// Dev only: `?state=thinking` pins the orb to one state for design review.
const demo = import.meta.env.DEV ? (new URLSearchParams(location.search).get('state') as Status | null) : null;

export function selectStatus(s: PlagState): Status {
  if (demo) return demo;
  if (s.halted) return 'halted';
  if (!s.coreUp) return s.booted ? 'offline' : 'booting';
  if (s.agentMode) return s.agentMode; // talking with the ElevenLabs agent
  if (s.listening) return 'listening';
  if (s.speaking) return 'speaking';
  if (s.server === 'executing') return 'executing';
  if (s.server === 'thinking' || s.pending) return 'thinking';
  if (s.cameraOn) return 'watching';
  return 'idle';
}
