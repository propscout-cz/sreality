/* Who may start an agent run, and what they may start: GET /runs/options, which the Shell's nav reads on every visit.
 * Apart from `lib/agentRuns`, so that nav read keeps the pages' helpers out of the first-visit bundle. */

import { useQuery } from '@tanstack/react-query';
import { fetchAgentRunOptions } from './api';

/* Whether this account's queued run can move now: `dark` the brake is off, `parked` the daily ceiling or a missing setting
 * holds it, `limited` only the account's own daily limit does. */
export type QueueState = 'dark' | 'parked' | 'limited' | 'open';

/* A null figure is one the API could not name; the page shows a gap, not a number. */
export interface AccountLimit {
  limit_usd: number | null;
  spent_today_usd: number | null;
}

export interface AgentOption {
  name: string;
  description: string;
  input_schema: JsonSchema;
}

/* The one answer to "may this caller start a run": the allowlist and the plan's `agents` agenda; the server decides. */
export interface AgentRunOptions {
  start: { allowed: boolean; reason: string | null };
  /* null for a refused account, which reads no platform state. */
  queue: QueueState | null;
  agents: AgentOption[];
  /* null for a refused account, which has no agent controls. */
  limit: AccountLimit | null;
}

export interface JsonSchema {
  type?: string;
  description?: string;
  enum?: string[];
  required?: string[];
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
}

export const agentRunKeys = {
  all: ['agent-runs'] as const,
  options: ['agent-runs', 'options'] as const,
  list: ['agent-runs', 'list'] as const,
  run: (id: number) => ['agent-runs', 'run', id] as const,
  steps: (id: number) => ['agent-runs', 'steps', id] as const,
  proposals: (id: number) => ['agent-runs', 'proposals', id] as const,
};

/* A run the queue cannot move can wait for days: nothing will change until the brake, the ceiling or the limit does. */
export const WAITING_POLL_MS = 30_000;

/* The Shell's nav and the pages share this one read. `enabled` is false before a session exists. */
export const useAgentRunOptions = ({ enabled = true, live = false }: { enabled?: boolean; live?: boolean } = {}) =>
  useQuery({
    queryKey: agentRunKeys.options,
    queryFn: fetchAgentRunOptions,
    enabled,
    staleTime: 5 * 60_000,
    refetchInterval: live ? WAITING_POLL_MS : false,
  });
