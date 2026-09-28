import { useEffect, useId, useRef, useState } from 'react';
import { ThinkingOrb } from 'thinking-orbs';
import type { OrbState } from 'thinking-orbs';
import { langTag, mb, pct, rate, secs } from '../lib/format';
import { cancelReminder, connectGoogle, deleteMemory, disconnectGoogle, resumePlag, sendText } from '../lib/voice';
import {
  QUICK_SITES, addAccount, copyReply, dismiss, markDone, openAccount, openItem, pauseAccount, redraft, removeAccount,
} from '../lib/inbox';
import { TrashIcon } from './icons';
import { useStore, type Account, type Connector, type InboxItem } from '../state/store';

function Sparkline({ values, max, tone = 'lime' }: { values: number[]; max?: number; tone?: 'lime' | 'bone' | 'warn' }) {
  const gid = `g${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const W = 120;
  const H = 34;
  if (values.length < 2) {
    return (
      <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
        <line x1="0" y1={H - 1} x2={W} y2={H - 1} className="spark-base" />
      </svg>
    );
  }
  const top = max ?? Math.max(1, ...values) * 1.15;
  const stepX = W / 59;
  const pts = values.map((v, i) => [W - (values.length - 1 - i) * stepX, H - 2 - Math.min(1, v / top) * (H - 5)]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
  const area = `${line}L${pts[pts.length - 1][0].toFixed(1)},${H}L${pts[0][0].toFixed(1)},${H}Z`;
  return (
    <svg className={`spark tone-${tone}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="currentColor" stopOpacity="0.28" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={line} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Metric(props: { label: string; value: string; unit?: string; sub?: string; series: number[]; max?: number; warn?: boolean; tone?: 'lime' | 'bone' }) {
  const { label, value, unit, sub, series, max, warn, tone } = props;
  return (
    <div className={`metric ${warn ? 'warn' : ''}`}>
      <div className="m-top">
        <span className="m-label">{label}</span>
        <span className="m-sub">{sub}</span>
      </div>
      <div className="m-body">
        <span className="m-value">
          {value}
          {unit ? <small>{unit}</small> : null}
        </span>
        <Sparkline values={series} max={max} tone={warn ? 'warn' : tone} />
      </div>
    </div>
  );
}

export function VitalsPanel() {
  const m = useStore((s) => s.metrics);
  const series = useStore((s) => s.series);
  const procs = useStore((s) => s.procs);
  const busy = useStore((s) => s.pending || s.halted || !s.coreUp);
  return (
    <section className="panel vitals" aria-labelledby="vitals-h">
      <header className="panel-h">
        <h2 id="vitals-h">This laptop</h2>
        <span className={`live ${m ? 'on' : ''}`}>{m ? 'Live' : 'Waiting'}</span>
      </header>
      <Metric label="CPU" value={pct(m?.cpu)} unit="%" series={series.cpu} max={100} warn={(m?.cpu ?? 0) >= 80}
        sub={m?.cpu_freq ? `${(m.cpu_freq.current / 1000).toFixed(1)} GHz · ${m.cores} threads` : ''} />
      <Metric label="Memory" value={pct(m?.mem.pct)} unit="%" series={series.mem} max={100} warn={(m?.mem.pct ?? 0) >= 85}
        sub={m ? `${m.mem.used_gb} of ${m.mem.total_gb} GB` : ''} />
      <Metric label="Disk" value={pct(m?.disk.busy_pct)} unit="% busy" series={series.disk} max={100} warn={(m?.disk.busy_pct ?? 0) >= 85}
        sub={m ? `R ${rate(m.disk.read_bps)} · W ${rate(m.disk.write_bps)}` : ''} />
      <Metric label="Network" value={rate(m?.net.down_bps)} series={series.down} tone="bone"
        sub={m ? `up ${rate(m.net.up_bps)}` : ''} />
      {m?.battery ? (
        <div className="battery">
          <span>Battery</span>
          <span className="mono">{m.battery.pct}% · {m.battery.plugged ? 'plugged in' : 'on battery'}</span>
        </div>
      ) : null}
      <h3 className="sub-h">Using the most</h3>
      {procs.length ? (
        <ol className="procs">
          <li className="p-head"><span>App</span><span>CPU</span><span>Memory</span></li>
          {procs.map((p) => (
            <li key={p.name}>
              <span className="p-name">{p.name}{p.count > 1 ? <em>×{p.count}</em> : null}</span>
              <span className="p-cpu">{p.cpu.toFixed(0)}%</span>
              <span className="p-mem">{mb(p.mem_mb)}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="empty">Reading running apps…</p>
      )}
      <button className="ghost-btn" disabled={busy} onClick={() => void sendText('why is my laptop slow')}>
        Why is it slow?
      </button>
    </section>
  );
}

const STEP_LABEL: Record<string, string> = { listen: 'Listen', understand: 'Understand', look: 'Look', act: 'Act', reply: 'Reply', speak: 'Speak' };
const STEP_ORB: Record<string, OrbState> = { listen: 'listening', understand: 'solving', look: 'searching', act: 'working', reply: 'composing' };
// a plan's steps ("act0", "act1"…) sit between understanding and the reply, in order
const rank = (name: string) => {
  const m = /^act(\d+)$/.exec(name);
  if (m) return 3 + Number(m[1]) / 100;
  return ({ listen: 0, understand: 1, look: 2, act: 3, reply: 4, speak: 5 } as Record<string, number>)[name] ?? 4.5;
};

export function NowPanel() {
  const task = useStore((s) => s.task);
  const steps = task ? [...task.steps].sort((a, b) => rank(a.step) - rank(b.step)) : [];
  const state = !task ? 'Idle' : task.done ? (task.ms != null ? secs(task.ms) : 'Done') : 'Working';
  return (
    <section className="panel now" aria-labelledby="now-h">
      <header className="panel-h">
        <h2 id="now-h">Now</h2>
        <span className="mono dim">{state}</span>
      </header>
      {steps.length ? (
        <ol className="steps">
          {steps.map((st) => (
            <li key={st.step} className={`step s-${st.state}`}>
              <span className="step-icon">
                {st.state === 'running' ? <ThinkingOrb state={STEP_ORB[st.step.replace(/\d+$/, '')] ?? 'working'} size={20} theme="dark" color="#D6F24B" /> : <i />}
              </span>
              <span className="step-name">{st.label ?? STEP_LABEL[st.step] ?? st.step}</span>
              <span className="step-ms mono">{secs(st.ms)}</span>
              {st.detail ? <span className="step-detail">{st.detail}</span> : null}
            </li>
          ))}
        </ol>
      ) : (
        <p className="empty">Ask something. Each step PLAG takes shows up here with its timing.</p>
      )}
    </section>
  );
}

function when(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  if (d.toDateString() === now.toDateString()) return `Today ${time}`;
  if (d.toDateString() === tomorrow.toDateString()) return `Tomorrow ${time}`;
  return `${d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })} ${time}`;
}

function MemoryView() {
  const memories = useStore((s) => s.memories);
  if (!memories.length) {
    return <p className="empty">Say “PLAG, remember that…” and it shows up here. PLAG never stores passwords or keys.</p>;
  }
  return (
    <ul className="item-list">
      {memories.map((m) => (
        <li key={m.id}>
          <span className="item-main">{m.text}<span className="mono">{when(m.created)}</span></span>
          <button className="item-del" onClick={() => void deleteMemory(m.id)} aria-label={`Forget: ${m.text}`} title="Forget">
            <TrashIcon />
          </button>
        </li>
      ))}
    </ul>
  );
}

function RemindersView() {
  const reminders = useStore((s) => s.reminders);
  if (!reminders.length) {
    return <p className="empty">Say “PLAG, remind me at 7 pm to call mom” or “10 minute baad yaad dilana”.</p>;
  }
  return (
    <ul className="item-list">
      {reminders.map((r) => (
        <li key={r.id}>
          <span className="item-main">{r.text}<span className="mono">{when(r.due)}</span></span>
          <button className="item-del" onClick={() => void cancelReminder(r.id)} aria-label={`Cancel reminder: ${r.text}`} title="Cancel">
            <TrashIcon />
          </button>
        </li>
      ))}
    </ul>
  );
}

function ago(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins} min`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)} h`;
  return new Date(iso).toLocaleDateString([], { day: 'numeric', month: 'short' });
}

const SERVICE_COLOR: Record<string, string> = {
  gmail: '#ea4335', linkedin: '#0a66c2', instagram: '#e1306c', x: '#e7e9ea', facebook: '#1877f2', outlook: '#0078d4',
  whatsapp: '#25d366', telegram: '#2aabee', slack: '#e01e5a', discord: '#5865f2',
};

function Badge({ name, color }: { name: string; color?: string }) {
  return (
    <span className="svc" style={{ ['--svc' as string]: color ?? '#d6f24b' }} aria-hidden="true">
      {name.charAt(0).toUpperCase()}
    </span>
  );
}

/** One new message: who and what, PLAG's summary, and the reply it drafted (edit it, copy it, open the chat). */
function InboxCard({ item }: { item: InboxItem }) {
  const [reply, setReply] = useState(item.reply);
  const [ask, setAsk] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => setReply(item.reply), [item.reply]);
  const code = item.kind === 'code';
  const onRedraft = async (instruction: string) => {
    setBusy(true);
    const r = await redraft(item, instruction);
    if (r !== null) setReply(r);
    setAsk('');
    setBusy(false);
  };
  return (
    <li className={`ibx u-${item.urgency} ${item.status !== 'new' ? 'is-done' : ''}`}>
      <div className="ibx-top">
        <Badge name={item.service_name} color={SERVICE_COLOR[item.service]} />
        <span className="ibx-who">
          <b>{item.sender}</b>
          <span className="mono">{item.service_name} · {ago(item.created)}{item.urgency === 'high' ? ' · urgent' : ''}</span>
        </span>
        <button className="item-del" onClick={() => void dismiss(item)} aria-label={`Dismiss the message from ${item.sender}`} title="Dismiss">
          <TrashIcon />
        </button>
      </div>
      {item.summary ? <p className="ibx-sum">{item.summary}</p> : null}
      {item.kind !== 'count' ? (
        <button className="ibx-orig" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? (item.subject ? `${item.subject} — ` : '') + item.text : `${open ? 'Hide' : 'Show'} the message`}
        </button>
      ) : null}
      {code ? (
        <p className="ibx-note">A security code or sign-in alert. PLAG doesn’t draft replies to these or read them out.</p>
      ) : item.kind === 'count' ? null : (
        <>
          <label className="ibx-label" htmlFor={`r-${item.id}`}>
            {reply ? 'PLAG’s draft · edit it, then copy and send it yourself' : 'No reply needed · or ask PLAG for one below'}
          </label>
          {reply ? (
            <textarea id={`r-${item.id}`} className="ibx-reply" value={reply} onChange={(e) => setReply(e.target.value)}
              rows={Math.min(7, Math.max(2, Math.ceil(reply.length / 52)))} spellCheck />
          ) : null}
          <form className="ibx-ask" onSubmit={(e) => { e.preventDefault(); void onRedraft(ask); }}>
            <input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="How should I answer? e.g. “yes, 5 pm works”"
              aria-label="Tell PLAG how to answer" maxLength={300} />
            <button type="submit" disabled={busy}>{busy ? 'Writing…' : reply ? 'Redraft' : 'Draft'}</button>
          </form>
        </>
      )}
      <div className="ibx-actions">
        {reply && !code ? (
          <button className="ibx-btn primary" onClick={() => void copyReply(item, reply).then((ok) => { setCopied(ok); setTimeout(() => setCopied(false), 1800); })}>
            {copied ? 'Copied' : 'Copy reply'}
          </button>
        ) : null}
        <button className="ibx-btn" onClick={() => void openItem(item)}>Open {item.service_name}</button>
        {item.status === 'new' ? <button className="ibx-btn" onClick={() => void markDone(item)}>Done</button> : null}
      </div>
    </li>
  );
}

function InboxView() {
  const inbox = useStore((s) => s.inbox);
  const accounts = useStore((s) => s.accounts);
  if (!inbox.length) {
    return (
      <p className="empty">
        {accounts.length
          ? 'No new messages. When someone writes to you on a connected account, it shows up here with a reply PLAG drafted.'
          : 'Connect Gmail, LinkedIn, Instagram or any site under Connections. New messages show up here with a reply PLAG drafted for you to send.'}
      </p>
    );
  }
  return <ul className="ibx-list">{inbox.map((i) => <InboxCard key={i.id} item={i} />)}</ul>;
}

const TABS = [
  { id: 'conversation', label: 'Conversation' },
  { id: 'inbox', label: 'Inbox' },
  { id: 'memory', label: 'Memory' },
  { id: 'reminders', label: 'Reminders' },
] as const;

export function ConversationPanel() {
  const messages = useStore((s) => s.messages);
  const tab = useStore((s) => s.tab);
  const setTab = useStore((s) => s.setTab);
  const nMemories = useStore((s) => s.memories.length);
  const nReminders = useStore((s) => s.reminders.length);
  const nInbox = useStore((s) => s.inbox.filter((i) => i.status === 'new').length);
  const counts = { conversation: 0, inbox: nInbox, memory: nMemories, reminders: nReminders };
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [messages.length, tab]);
  return (
    <section className="panel convo" aria-label="Conversation, memory and reminders">
      <header className="panel-h tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'on' : ''} onClick={() => setTab(t.id)}>
            {t.label}{counts[t.id] ? ` · ${counts[t.id]}` : ''}
          </button>
        ))}
      </header>
      {tab === 'memory' ? <MemoryView /> : tab === 'reminders' ? <RemindersView /> : tab === 'inbox' ? <InboxView /> : (
      <div className="convo-list">
        {messages.length ? (
          messages.map((m) => (
            <div key={m.id} className={`msg ${m.role} ${m.failed ? 'failed' : ''}`}>
              <div className="msg-top">
                <span className="msg-who">{m.role === 'you' ? 'You' : 'PLAG'}</span>
                {m.lang ? <span className="tag">{langTag(m.lang)}</span> : null}
              </div>
              <p lang={m.lang === 'hi' ? 'hi' : undefined}>{m.text}</p>
              {m.links ? (
                <ul className="msg-links" aria-label="Sources">
                  {m.links.map((l) => (
                    <li key={l.url}>
                      <a href={l.url} target="_blank" rel="noreferrer" title={l.title}><b>{l.site}</b> {l.title}</a>
                    </li>
                  ))}
                </ul>
              ) : null}
              {m.meta ? <span className="msg-meta mono">{m.meta}</span> : null}
            </div>
          ))
        ) : (
          <p className="empty">Your conversation with PLAG shows up here, in English, हिंदी or Hinglish.</p>
        )}
        <div ref={end} />
      </div>
      )}
    </section>
  );
}

const CONN_LABEL: Record<string, string> = { online: 'Online', ready: 'Ready', degraded: 'Trouble', off: 'Off', planned: 'Not yet' };

const ACCOUNT_STATE: Record<Account['state'], [string, string]> = {
  watching: ['online', 'Watching for new messages'],
  starting: ['ready', 'Opening…'],
  signin: ['degraded', 'Sign in needed'],
  paused: ['off', 'Paused'],
  offline: ['degraded', 'Can’t reach the site right now'],
  limit: ['off', 'Paused: PLAG watches up to 6 accounts at once'],
};

/** Connect an account by its address: PLAG opens the real sign-in page; you sign in; it watches for new messages. */
function AccountsBlock() {
  const accounts = useStore((s) => s.accounts);
  const [adding, setAdding] = useState(false);
  const [url, setUrl] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const connect = async (address: string) => {
    setBusy(true);
    setError(null);
    const err = await addAccount(address);
    setBusy(false);
    if (err) setError(err);
    else { setUrl(''); setAdding(false); }
  };
  return (
    <div className="acct">
      <div className="acct-h">
        <h3 className="sub-h">Your accounts</h3>
        <button className="conn-btn" onClick={() => { setAdding(!adding); setError(null); }} aria-expanded={adding}>
          {adding ? 'Cancel' : '+ Connect'}
        </button>
      </div>
      {adding ? (
        <form className="acct-form" onSubmit={(e) => { e.preventDefault(); void connect(url); }}>
          <label htmlFor="acct-url">Website address</label>
          <div className="acct-row">
            <input id="acct-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="linkedin.com" autoFocus
              autoComplete="off" spellCheck={false} inputMode="url" />
            <button type="submit" disabled={busy || !url.trim()}>Sign in</button>
          </div>
          <div className="acct-chips">
            {QUICK_SITES.map((q) => (
              <button type="button" key={q.url} disabled={busy} onClick={() => void connect(q.url)}>{q.label}</button>
            ))}
          </div>
          {error ? <p className="set-status warn">{error}</p> : null}
          <p className="acct-note">You sign in on the real site in a PLAG window; PLAG never sees your password. It reads new
            messages and drafts replies. It never sends anything for you.</p>
        </form>
      ) : null}
      {accounts.length ? (
        <ul className="conn-list acct-list">
          {accounts.map((a) => {
            const [tone, label] = ACCOUNT_STATE[a.state] ?? ['ready', a.state];
            return (
              <li key={a.id} className={`conn c-${tone}`}>
                <Badge name={a.name} color={a.color} />
                <div className="conn-main">
                  <span className="conn-name">{a.name}{a.unread ? <em className="acct-unread">{a.unread}</em> : null}</span>
                  <span className="conn-detail">{a.host} · {label}</span>
                </div>
                <span className="acct-actions">
                  <button className="conn-btn" onClick={() => void openAccount(a)}>{a.state === 'signin' ? 'Sign in' : 'Open'}</button>
                  <button className="acct-mini" onClick={() => void pauseAccount(a)} title={a.watch ? 'Pause watching' : 'Resume watching'}
                    aria-label={`${a.watch ? 'Pause' : 'Resume'} ${a.name}`}>{a.watch ? 'Ⅱ' : '▶'}</button>
                  <button className="item-del" onClick={() => void removeAccount(a)} title="Disconnect and sign out"
                    aria-label={`Disconnect ${a.name}`}><TrashIcon /></button>
                </span>
              </li>
            );
          })}
        </ul>
      ) : !adding ? (
        <p className="empty acct-empty">Connect Gmail, LinkedIn, Instagram or any site by its address.</p>
      ) : null}
    </div>
  );
}

export function ConnectionsPanel() {
  const items = useStore((s) => s.connectors);
  const listening = useStore((s) => s.listening);
  const coreUp = useStore((s) => s.coreUp);
  const cameraOn = useStore((s) => s.cameraOn);
  const all: Connector[] = [
    { id: 'core', name: 'PLAG core', role: '', state: coreUp ? 'online' : 'degraded', detail: coreUp ? 'Running on this laptop' : 'Not responding yet' },
    { id: 'mic', name: 'Microphone', role: '', state: listening ? 'online' : 'ready', detail: listening ? 'Recording your command' : 'Records only after “PLAG” or the mic button' },
    { id: 'camera', name: 'Camera', role: '', state: cameraOn ? 'online' : 'ready', detail: cameraOn ? 'On · a frame goes to Gemini only when you ask' : 'Off until you turn it on' },
    ...items,
  ];
  return (
    <section className="panel conns" aria-labelledby="conns-h">
      <header className="panel-h">
        <h2 id="conns-h">Connections</h2>
      </header>
      <AccountsBlock />
      <h3 className="sub-h">PLAG</h3>
      <ul className="conn-list">
        {all.map((c) => (
          <li key={c.id} className={`conn c-${c.state}`}>
            <i className="conn-dot" />
            <div className="conn-main">
              <span className="conn-name">{c.name}</span>
              <span className="conn-detail">{c.detail}</span>
            </div>
            {c.action ? (
              <button className="conn-btn" onClick={() => void (c.action === 'connect' ? connectGoogle() : disconnectGoogle())}>
                {c.action === 'connect' ? 'Connect' : 'Disconnect'}
              </button>
            ) : (
              <span className="conn-state">{CONN_LABEL[c.state] ?? c.state}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function HaltOverlay() {
  return (
    <div className="halt-overlay" role="alertdialog" aria-modal="true" aria-labelledby="halt-title" aria-describedby="halt-desc">
      <div className="halt-card">
        <span className="halt-ring" aria-hidden="true" />
        <h2 id="halt-title">PLAG is halted</h2>
        <p id="halt-desc">Tasks, voice and the microphone are stopped. Nothing runs until you resume.</p>
        <button className="resume-btn" autoFocus onClick={() => void resumePlag()}>
          Resume PLAG
        </button>
      </div>
    </div>
  );
}
