import { useEffect, useId, useRef } from 'react';
import { ThinkingOrb } from 'thinking-orbs';
import type { OrbState } from 'thinking-orbs';
import { langTag, mb, pct, rate, secs } from '../lib/format';
import { cancelReminder, connectGoogle, deleteMemory, disconnectGoogle, resumePlag, sendText } from '../lib/voice';
import { TrashIcon } from './icons';
import { useStore, type Connector } from '../state/store';

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

const TABS = [
  { id: 'conversation', label: 'Conversation' },
  { id: 'memory', label: 'Memory' },
  { id: 'reminders', label: 'Reminders' },
] as const;

export function ConversationPanel() {
  const messages = useStore((s) => s.messages);
  const tab = useStore((s) => s.tab);
  const setTab = useStore((s) => s.setTab);
  const nMemories = useStore((s) => s.memories.length);
  const nReminders = useStore((s) => s.reminders.length);
  const counts = { conversation: 0, memory: nMemories, reminders: nReminders };
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
      {tab === 'memory' ? <MemoryView /> : tab === 'reminders' ? <RemindersView /> : (
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
