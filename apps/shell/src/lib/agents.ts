// The Agents tab: your own AI workers. Everything real happens in the core (plag_core/agents.py); this keeps the
// list in the store, starts runs, and follows their steps live over the event bus.
import { call } from './core';
import { useStore } from '../state/store';

export type AgentBrain = { id: string; name: string; what: string; ready: boolean };
export type AgentTool = { name: string; what: string; level: 'read' | 'open' | 'approval' };

export type Agent = {
  id: string; name: string; kind: string; purpose: string; instructions: string;
  brain: string; brain_label: string; tools: string[];
  builtin: boolean; enabled: boolean; created: string; last_run: string; runs: number;
};

export type AgentStep = {
  kind: 'tool' | 'refused' | 'approval' | 'error';
  tool?: string; args?: Record<string, unknown>; thought?: string;
  state?: 'running' | 'done' | 'failed' | 'waiting' | 'refused';
  result?: string; text?: string; approval_id?: string; approved?: boolean;
};

export type AgentRun = {
  id: string; agent_id: string; task: string; ms: number; brain: string;
  state: 'running' | 'done' | 'waiting' | 'refused' | 'failed' | 'halted' | 'out_of_steps';
  answer: string; steps: AgentStep[]; started: string; finished: string;
  pending?: { tool: string; args: Record<string, unknown>; approval_id: string; summary: Record<string, string> };
};

export type MailStatus = { connected: boolean; address: string; imap?: string; smtp?: string; name?: string };

/** Everything the tab needs in one request: the agents, the brains that have a key, and the tool catalogue. */
export async function loadAgents(): Promise<void> {
  try {
    const d = await call<{ agents: Agent[]; brains: AgentBrain[]; tools: AgentTool[]; mail: MailStatus }>('/v1/agents');
    useStore.setState({ agents: d.agents, agentBrains: d.brains, agentTools: d.tools, mail: d.mail });
  } catch {
    /* the core is still starting: the tab shows its empty state and this runs again when it connects */
  }
}

export async function loadRuns(agentId = ''): Promise<AgentRun[]> {
  try {
    const d = await call<{ runs: AgentRun[] }>(`/v1/agents/runs${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''}`);
    return d.runs;
  } catch {
    return [];
  }
}

export async function saveAgent(a: Partial<Agent> & { id?: string }): Promise<string | null> {
  const body = {
    name: a.name ?? '', purpose: a.purpose ?? '', instructions: a.instructions ?? '',
    brain: a.brain ?? 'glm', tools: a.tools ?? [], enabled: a.enabled ?? true,
  };
  try {
    await call(a.id ? `/v1/agents/${a.id}` : '/v1/agents', { json: body, method: a.id ? 'PATCH' : 'POST' });
    await loadAgents();
    return null;
  } catch (e) {
    return (e as { message?: string }).message ?? 'That could not be saved.';
  }
}

export async function deleteAgent(id: string): Promise<string | null> {
  try {
    await call(`/v1/agents/${id}`, { method: 'DELETE' });
    await loadAgents();
    return null;
  } catch (e) {
    return (e as { message?: string }).message ?? 'That agent could not be removed.';
  }
}

/** "Make me an agent that…" — Muse designs it from the real tool list and it's saved ready to run. */
export async function buildAgent(description: string): Promise<{ agent?: Agent; error?: string }> {
  try {
    const a = await call<Agent>('/v1/agents/build', { json: { description } });
    await loadAgents();
    return { agent: a };
  } catch (e) {
    return { error: (e as { message?: string }).message ?? "I couldn't design that agent." };
  }
}

/** Start an agent. Returns at once with a run id; the steps arrive as events. */
export async function runAgent(id: string, task: string): Promise<string | null> {
  const s = useStore.getState();
  try {
    const d = await call<{ run_id: string }>(`/v1/agents/${id}/run`, { json: { task } });
    s.startAgentRun({ id: d.run_id, agent_id: id, task, state: 'running', answer: '', steps: [],
      started: new Date().toISOString(), finished: '', ms: 0, brain: '' });
    return d.run_id;
  } catch (e) {
    s.setError((e as { message?: string }).message ?? "That agent couldn't start.");
    return null;
  }
}

export async function stopAgentRun(runId: string): Promise<void> {
  await call(`/v1/agents/runs/${runId}/stop`, { json: {} }).catch(() => undefined);
  useStore.getState().finishAgentRun(runId, { state: 'refused', answer: 'You stopped it.' });
}

/** Your answer to an agent's approval card. On yes the held-back step runs and the agent carries on. */
export async function answerAgent(runId: string, yes: boolean): Promise<void> {
  const s = useStore.getState();
  try {
    const out = await call<AgentRun>(`/v1/agents/runs/${runId}/approve?yes=${yes}`, { json: {} });
    s.finishAgentRun(runId, out);
  } catch (e) {
    s.setError((e as { message?: string }).message ?? 'That approval could not be answered.');
    await loadRuns().then((rs) => rs.find((r) => r.id === runId) && s.finishAgentRun(runId, rs.find((r) => r.id === runId)!));
  }
}

// ---------------------------------------------------------------- email

export async function loadMail(): Promise<void> {
  try {
    useStore.setState({ mail: await call<MailStatus>('/v1/mail') });
  } catch {
    /* shown as not connected until the core answers */
  }
}

/** Signs in to the mail server before saving: a wrong app password is refused here, not quietly later. */
export async function connectMail(f: { address: string; password: string; imap?: string; smtp?: string; name?: string }):
  Promise<{ unread?: number; error?: string }> {
  try {
    const d = await call<{ unread: number } & MailStatus>('/v1/mail', { json: f });
    useStore.setState({ mail: { connected: true, address: d.address, imap: d.imap, smtp: d.smtp, name: d.name } });
    await loadAgents();
    return { unread: d.unread };
  } catch (e) {
    return { error: (e as { message?: string }).message ?? 'That account could not be connected.' };
  }
}

export async function disconnectMail(): Promise<void> {
  await call('/v1/mail', { method: 'DELETE' }).catch(() => undefined);
  useStore.setState({ mail: { connected: false, address: '' } });
  await loadAgents();
}

/** What PLAG will use for this address, so the form can show it before you connect. */
export async function mailServers(address: string): Promise<{ known: boolean; imap: string; smtp: string }> {
  try {
    return await call(`/v1/mail/servers?address=${encodeURIComponent(address)}`);
  } catch {
    return { known: false, imap: '', smtp: '' };
  }
}
