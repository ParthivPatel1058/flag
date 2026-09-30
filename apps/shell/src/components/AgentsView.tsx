// The Agents tab: your own AI workers. Each card is one agent — what it does, which brain it uses, which tools it
// may touch — and you can run it, edit it, switch it off, or describe a new one and have Muse build it.
import { useEffect, useMemo, useState } from 'react';
import {
  answerAgent, buildAgent, connectMail, deleteAgent, disconnectMail, loadAgents, loadRuns, mailServers, runAgent,
  saveAgent, stopAgentRun, type Agent, type AgentRun, type AgentStep,
} from '../lib/agents';
import { useStore } from '../state/store';

// The narrow panel can't carry a sentence here, so each tool gets a short badge; the tooltip says it in full.
const LEVEL_NOTE: Record<string, string> = { read: 'reads', open: 'opens', approval: 'asks you' };
const LEVEL_FULL: Record<string, string> = {
  read: 'Only looks: nothing changes and nothing leaves the laptop.',
  open: 'Opens something on your screen.',
  approval: 'Leaves the laptop or changes it, so PLAG asks you first — every single time.',
};

const STATE_LABEL: Record<AgentRun['state'], string> = {
  running: 'Working…', done: 'Done', waiting: 'Waiting for you', refused: 'You said no',
  failed: 'Failed', halted: 'Halted', out_of_steps: 'Ran out of steps',
};

function relative(iso: string): string {
  if (!iso) return 'never';
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 90) return 'just now';
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)} h ago`;
  return `${Math.round(secs / 86400)} d ago`;
}

/** One line per step, so you can watch the agent think and see exactly which tool touched what. */
function StepLine({ step }: { step: AgentStep }) {
  if (step.kind === 'error') return <li className="ag-step failed">{step.text}</li>;
  if (step.kind === 'refused') return <li className="ag-step refused">{step.text}</li>;
  const args = Object.entries(step.args ?? {}).filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${k}: ${String(v).slice(0, 70)}`).join(' · ');
  return (
    <li className={`ag-step ${step.state ?? ''}`}>
      <span className="ag-tool">{step.tool}</span>
      {step.approved ? <span className="ag-badge">you approved it</span> : null}
      {args ? <span className="ag-args">{args}</span> : null}
      {step.thought ? <span className="ag-thought">{step.thought}</span> : null}
      {step.result ? <span className="ag-result">{step.result}</span> : null}
    </li>
  );
}

function RunView({ run }: { run: AgentRun }) {
  const [busy, setBusy] = useState(false);
  const answer = async (yes: boolean) => {
    setBusy(true);
    await answerAgent(run.id, yes);
    setBusy(false);
  };
  return (
    <div className={`ag-run ${run.state}`}>
      <div className="ag-run-top">
        <span className={`ag-state ${run.state}`}>{STATE_LABEL[run.state] ?? run.state}</span>
        {run.brain ? <span className="tag">{run.brain}</span> : null}
        {run.ms ? <span className="tag">{(run.ms / 1000).toFixed(1)} s</span> : null}
        {run.state === 'running' ? (
          <button className="link-btn" onClick={() => void stopAgentRun(run.id)}>Stop</button>
        ) : null}
      </div>
      {run.steps.length ? <ul className="ag-steps">{run.steps.map((s, i) => <StepLine key={i} step={s} />)}</ul> : null}
      {run.state === 'waiting' && run.pending ? (
        <div className="ag-approve">
          <p>
            <b>{run.pending.summary?.agent ?? 'This agent'}</b> wants to <b>{run.pending.tool.replace(/_/g, ' ')}</b>.
            Nothing has happened yet.
          </p>
          <ul className="ag-approve-what">
            {Object.entries(run.pending.summary ?? {})
              .filter(([k]) => k !== 'agent' && k !== 'tool')
              .map(([k, v]) => <li key={k}><span>{k}</span>{String(v)}</li>)}
          </ul>
          <div className="ag-approve-btns">
            <button disabled={busy} onClick={() => void answer(true)}>Yes, do it</button>
            <button className="link-btn" disabled={busy} onClick={() => void answer(false)}>No</button>
          </div>
        </div>
      ) : null}
      {run.answer ? <p className="ag-answer">{run.answer}</p> : null}
    </div>
  );
}

function AgentEditor({ agent, onClose }: { agent: Agent | null; onClose: () => void }) {
  const brains = useStore((s) => s.agentBrains);
  const tools = useStore((s) => s.agentTools);
  const [name, setName] = useState(agent?.name ?? '');
  const [purpose, setPurpose] = useState(agent?.purpose ?? '');
  const [instructions, setInstructions] = useState(agent?.instructions ?? '');
  const [brain, setBrain] = useState(agent?.brain ?? 'glm');
  const [picked, setPicked] = useState<string[]>(agent?.tools ?? []);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const toggle = (t: string) =>
    setPicked((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]));

  const save = async () => {
    setBusy(true);
    const error = await saveAgent({ id: agent?.id, name, purpose, instructions, brain, tools: picked,
      enabled: agent?.enabled ?? true });
    setBusy(false);
    if (error) setErr(error);
    else onClose();
  };

  return (
    <form className="ag-editor" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      <label>Name
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder="Job Hunt" autoFocus />
      </label>
      <label>What it does
        <input value={purpose} onChange={(e) => setPurpose(e.target.value)} maxLength={400}
          placeholder="Finds internships that suit me and writes them up." />
      </label>
      <label>How it should work
        <textarea value={instructions} onChange={(e) => setInstructions(e.target.value)} rows={4} maxLength={4000}
          placeholder="Search at least two job sites. Skip anything asking for a fee. Put the deadline first." />
      </label>
      <label>Brain
        <select value={brain} onChange={(e) => setBrain(e.target.value)}>
          {brains.map((b) => (
            <option key={b.id} value={b.id} disabled={!b.ready}>
              {b.name}{b.ready ? '' : ' — no key saved'}
            </option>
          ))}
        </select>
      </label>
      <p className="set-note">{brains.find((b) => b.id === brain)?.what}</p>
      <fieldset className="ag-tools">
        <legend>What it may use ({picked.length} picked)</legend>
        {tools.map((t) => (
          <label key={t.name} className={`ag-tool-row ${picked.includes(t.name) ? 'on' : ''}`}>
            <input type="checkbox" checked={picked.includes(t.name)} onChange={() => toggle(t.name)} />
            <span className="ag-tool-name">{t.name}</span>
            <span className={`ag-lvl ag-lvl-${t.level}`} title={LEVEL_FULL[t.level]}>{LEVEL_NOTE[t.level]}</span>
            <span className="ag-tool-what">{t.what}</span>
          </label>
        ))}
      </fieldset>
      {err ? <p className="set-status warn">{err}</p> : null}
      <div className="ag-editor-btns">
        <button type="submit" disabled={busy || !name.trim() || !picked.length}>
          {busy ? 'Saving…' : agent ? 'Save' : 'Create agent'}
        </button>
        <button type="button" className="link-btn" onClick={onClose}>Cancel</button>
      </div>
    </form>
  );
}

function AgentCard({ agent }: { agent: Agent }) {
  const runs = useStore((s) => s.agentRuns);
  const mail = useStore((s) => s.mail);
  const [task, setTask] = useState('');
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState<AgentRun[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  // the runs of this agent in this session, newest first
  const mine = useMemo(
    () => Object.values(runs).filter((r) => r.agent_id === agent.id).sort((a, b) => b.started.localeCompare(a.started)),
    [runs, agent.id],
  );
  const live = mine[0];
  const needsMail = agent.tools.some((t) => t.startsWith('mail_')) && !mail.connected;

  const start = async () => {
    const what = task.trim();
    if (!what) return;
    setTask('');
    await runAgent(agent.id, what);
  };

  return (
    <li className={`ag-card ${agent.enabled ? '' : 'off'}`}>
      <div className="ag-head">
        <div>
          <h4>{agent.name}{agent.builtin ? <span className="tag">built in</span> : null}</h4>
          <p className="ag-purpose">{agent.purpose}</p>
        </div>
        <label className="ag-switch" title={agent.enabled ? 'Switch off' : 'Switch on'}>
          <input type="checkbox" checked={agent.enabled}
            onChange={() => void saveAgent({ ...agent, enabled: !agent.enabled })} />
          <span />
        </label>
      </div>
      <p className="ag-meta">
        <span className="tag">{agent.brain_label}</span>
        {agent.tools.map((t) => <span key={t} className="tag dim">{t}</span>)}
        <span className="ag-runs">{agent.runs ? `${agent.runs} runs · last ${relative(agent.last_run)}` : 'never run'}</span>
      </p>
      {needsMail ? (
        <p className="set-status warn">This agent reads your email. Connect an account at the top of this tab first.</p>
      ) : null}
      {editing ? <AgentEditor agent={agent} onClose={() => setEditing(false)} /> : (
        <>
          <form className="ag-run-form" onSubmit={(e) => { e.preventDefault(); void start(); }}>
            <input value={task} onChange={(e) => setTask(e.target.value)} maxLength={1000}
              placeholder={`Tell ${agent.name} what to do…`} disabled={!agent.enabled} />
            <button type="submit" disabled={!agent.enabled || !task.trim() || live?.state === 'running'}>
              {live?.state === 'running' ? 'Working…' : 'Run'}
            </button>
          </form>
          <div className="ag-actions">
            <button className="link-btn" onClick={() => setEditing(true)}>Edit</button>
            <button className="link-btn" onClick={() => {
              setShowHistory((v) => !v);
              if (!showHistory) void loadRuns(agent.id).then(setHistory);
            }}>{showHistory ? 'Hide past runs' : 'Past runs'}</button>
            {agent.builtin ? null : (
              <button className="link-btn danger" onClick={() => void deleteAgent(agent.id)}>Delete</button>
            )}
          </div>
        </>
      )}
      {mine.map((r) => <RunView key={r.id} run={r} />)}
      {showHistory ? (
        <ul className="ag-history">
          {history.filter((h) => !runs[h.id]).map((h) => (
            <li key={h.id}>
              <span className={`ag-state ${h.state}`}>{STATE_LABEL[h.state] ?? h.state}</span>
              <span className="ag-hist-task">{h.task}</span>
              <span className="ag-hist-when">{relative(h.started)}</span>
              {h.answer ? <p>{h.answer}</p> : null}
            </li>
          ))}
          {history.filter((h) => !runs[h.id]).length ? null : <li className="dim">Nothing yet.</li>}
        </ul>
      ) : null}
    </li>
  );
}

/** Connect a real mailbox: one app password, and the Inbox agent can read and (with your yes) send. */
function MailBlock() {
  const mail = useStore((s) => s.mail);
  const [open, setOpen] = useState(false);
  const [address, setAddress] = useState('');
  const [password, setPassword] = useState('');
  const [imap, setImap] = useState('');
  const [smtp, setSmtp] = useState('');
  const [known, setKnown] = useState<{ known: boolean; imap: string; smtp: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!address.includes('@')) { setKnown(null); return; }
    let live = true;
    const t = setTimeout(() => void mailServers(address).then((k) => live && setKnown(k)), 350);
    return () => { live = false; clearTimeout(t); };
  }, [address]);

  const connect = async () => {
    setBusy(true);
    setMsg(null);
    const r = await connectMail({ address, password, imap, smtp });
    setBusy(false);
    if (r.error) setMsg({ ok: false, text: r.error });
    else {
      setMsg({ ok: true, text: `Connected. ${r.unread ?? 0} unread right now.` });
      setOpen(false);
      setPassword('');
    }
  };

  if (mail.connected) {
    return (
      <div className="ag-mail ok">
        <span><b>Email connected</b> · {mail.address} — PLAG can read your mail, and send once you say yes.</span>
        <button className="link-btn" onClick={() => void disconnectMail()}>Disconnect</button>
      </div>
    );
  }
  return (
    <div className="ag-mail">
      <div className="set-row">
        <span><b>Email not connected.</b> The Inbox agent needs one app password to read and send your mail.</span>
        <button className="link-btn" onClick={() => setOpen((v) => !v)}>{open ? 'Cancel' : 'Connect email'}</button>
      </div>
      {open ? (
        <form className="ag-mail-form" onSubmit={(e) => { e.preventDefault(); void connect(); }}>
          <input type="email" value={address} onChange={(e) => setAddress(e.target.value)}
            placeholder="you@gmail.com" autoComplete="off" autoFocus />
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder="app password (not your normal password)" autoComplete="off" />
          {known && !known.known && address.includes('@') ? (
            <>
              <input value={imap} onChange={(e) => setImap(e.target.value)} placeholder="IMAP server (imap.yoursite.com)" />
              <input value={smtp} onChange={(e) => setSmtp(e.target.value)} placeholder="SMTP server (smtp.yoursite.com)" />
            </>
          ) : null}
          <button type="submit" disabled={busy || !address.includes('@') || password.length < 4}>
            {busy ? 'Signing in…' : 'Connect'}
          </button>
          <p className="set-note">
            {known?.known ? `PLAG will use ${known.imap} and ${known.smtp}. ` : ''}
            An app password is a one-off password just for PLAG — your real password is never used or stored.
            Gmail: <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">myaccount.google.com/apppasswords</a>.
            PLAG signs in to check it before saving anything.
          </p>
        </form>
      ) : null}
      {msg ? <p className={`set-status ${msg.ok ? 'ok' : 'warn'}`}>{msg.text}</p> : null}
    </div>
  );
}

export default function AgentsView() {
  const agents = useStore((s) => s.agents);
  const [idea, setIdea] = useState('');
  const [building, setBuilding] = useState(false);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { void loadAgents(); }, []);

  const build = async () => {
    const what = idea.trim();
    if (!what) return;
    setBuilding(true);
    setErr(null);
    const { error } = await buildAgent(what);
    setBuilding(false);
    if (error) setErr(error);
    else setIdea('');
  };

  return (
    <div className="ag-view">
      <MailBlock />
      <form className="ag-new" onSubmit={(e) => { e.preventDefault(); void build(); }}>
        <input value={idea} onChange={(e) => setIdea(e.target.value)} maxLength={600} disabled={building}
          placeholder="Describe an agent — “one that watches my college site and tells me about new notices”" />
        <button type="submit" disabled={building || idea.trim().length < 3}>
          {building ? 'Muse is designing it…' : 'Build it'}
        </button>
        <button type="button" className="link-btn" onClick={() => setCreating((v) => !v)}>
          {creating ? 'Cancel' : 'Or set one up myself'}
        </button>
      </form>
      {err ? <p className="set-status warn">{err}</p> : null}
      {creating ? <AgentEditor agent={null} onClose={() => setCreating(false)} /> : null}
      {agents.length ? (
        <ul className="ag-list">{agents.map((a) => <AgentCard key={a.id} agent={a} />)}</ul>
      ) : (
        <p className="empty">No agents yet. Describe one above and PLAG will build it.</p>
      )}
    </div>
  );
}
