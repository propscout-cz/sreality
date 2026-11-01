/* Agent runs in the SPA (#2071): the shapes GET /runs* answer, the live refresh, and the two translations the pages need:
 * a run's diary into the estimation Timeline's trace, and an agent's input schema into a form.
 *
 * EVERYTHING A RUN HOLDS IS UNTRUSTED except the numbers it is keyed by: the model wrote the result, the proposals and
 * the step words, and an ad's text can steer the model. So a page renders it as React text only (never markdown, never
 * dangerouslySetInnerHTML, nothing that autolinks), and the one link a run gets is built here from an id. */

import { useQuery } from '@tanstack/react-query';
import { fetchAgentRun, fetchAgentRunOptions, fetchAgentRuns, fetchAgentRunSteps } from './api';
import { fmtUsd, fmtUsdPerCall } from './format';
import type { Trace, TraceStep } from './types';

export type AgentRunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
/* Whether a queued run can move now: `dark` the brake is off, `parked` the daily ceiling or a missing setting holds it. */
export type QueueState = 'dark' | 'parked' | 'open';

export interface AgentOption {
  name: string;
  description: string;
  input_schema: JsonSchema;
}

/* The one answer to "may this caller start a run": allowlist today, a plan's agenda with #2319; the server decides. */
export interface AgentRunOptions {
  start: { allowed: boolean; reason: string | null };
  /* null for a refused account, which reads no platform state. */
  queue: QueueState | null;
  agents: AgentOption[];
}

export interface AgentRunRow {
  id: number;
  agent: string;
  status: AgentRunStatus;
  attempts: number;
  provider: string;
  model: string;
  cost_usd: number;
  stop_reason: string | null;
  error: string | null;
  input: unknown;
  cancel_requested_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentRun extends AgentRunRow {
  result_markdown: string | null;
  cited_property_ids: number[];
  files: string[];
  proposals: unknown[];
  output: unknown;
  definition_sha: string;
  tool_set_sha: string;
  commit_sha: string;
}

export interface AgentRunStep {
  attempt: number;
  n: number;
  kind: 'model' | 'tool' | 'file';
  name: string;
  summary: string;
  input: unknown;
  text: string[] | null;
  cost_usd: number;
  duration_ms: number;
  created_at: string;
}

export interface StartAgentRun {
  agent: string;
  input: Record<string, unknown>;
  idempotency_key: string;
}

const LIVE_POLL_MS = 3000;

export const isLive = (status: string | undefined): boolean => status === 'queued' || status === 'running';

export const agentRunKeys = {
  all: ['agent-runs'] as const,
  options: ['agent-runs', 'options'] as const,
  list: ['agent-runs', 'list'] as const,
  run: (id: number) => ['agent-runs', 'run', id] as const,
  steps: (id: number) => ['agent-runs', 'steps', id] as const,
};

/* The Shell's nav and the pages share this one read. `enabled` is false before a session exists. */
export const useAgentRunOptions = ({ enabled = true, live = false }: { enabled?: boolean; live?: boolean } = {}) =>
  useQuery({
    queryKey: agentRunKeys.options,
    queryFn: fetchAgentRunOptions,
    enabled,
    staleTime: 5 * 60_000,
    refetchInterval: live ? 15_000 : false,
  });

export const useAgentRuns = () =>
  useQuery({
    queryKey: agentRunKeys.list,
    queryFn: fetchAgentRuns,
    refetchInterval: (q) => ((q.state.data?.data ?? []).some((r) => isLive(r.status)) ? LIVE_POLL_MS : false),
  });

export const useAgentRun = (id: number | null) =>
  useQuery({
    queryKey: agentRunKeys.run(id ?? 0),
    queryFn: () => fetchAgentRun(id as number),
    enabled: id != null,
    refetchInterval: (q) => (isLive(q.state.data?.status) ? LIVE_POLL_MS : false),
  });

export const useAgentRunSteps = (id: number | null, live: boolean) =>
  useQuery({
    queryKey: agentRunKeys.steps(id ?? 0),
    queryFn: () => fetchAgentRunSteps(id as number),
    enabled: id != null,
    refetchInterval: live ? LIVE_POLL_MS : false,
  });

/* Why a live run is not simply working, or null when it is. */
export function waitingNote(
  run: Pick<AgentRunRow, 'status' | 'attempts' | 'cancel_requested_at'>,
  queue: QueueState | null | undefined,
): string | null {
  if (run.status === 'running') {
    if (run.cancel_requested_at) return 'Stopping at its next step.';
    return run.attempts > 1 ? 'Its first attempt was interrupted, so it started again from the beginning.' : null;
  }
  if (run.status !== 'queued') return null;
  if (queue === 'dark') return 'Queued. The executor is switched off, so this run waits until it is switched on.';
  if (queue === 'parked') return 'Queued. Agent runs are paused for now (the daily spending ceiling or a setting); this run starts when that lifts.';
  if (queue === 'open') return 'Queued. It starts at the next free slot.';
  return 'Queued.';
}

/* --- the diary as the Timeline's trace ------------------------------------------------------------------------------ */

export function stepsByAttempt(steps: readonly AgentRunStep[]): Array<[number, AgentRunStep[]]> {
  const groups = new Map<number, AgentRunStep[]>();
  for (const step of steps) groups.set(step.attempt, [...(groups.get(step.attempt) ?? []), step]);
  return [...groups.entries()];
}

/* Small costs keep their fourth decimal (a model call); nothing spent reads $0.00. */
export const fmtRunCost = (usd: number): string => (usd > 0 ? fmtUsdPerCall(usd) : fmtUsd(0));

const costed = (step: AgentRunStep): Record<string, string> =>
  step.cost_usd > 0 ? { cost: fmtRunCost(step.cost_usd) } : {};

function toTraceStep(step: AgentRunStep): TraceStep {
  const base = { n: step.n, started_at: step.created_at, duration_ms: step.duration_ms };
  if (step.kind === 'model') {
    return {
      ...base,
      kind: 'reasoning',
      output_summary: { text: (step.text ?? []).join('\n\n'), model: step.name, ...costed(step) },
    };
  }
  const output_summary = { result: step.summary, ...costed(step) };
  if (step.kind === 'tool') {
    // Under one key, so a tool's `price_*` argument is shown as written and never meets the Timeline's Kč formatter.
    return { ...base, kind: 'tool_call', tool: step.name, input: { arguments: step.input }, output_summary };
  }
  return { ...base, kind: 'computation', label: `file ${step.name}`, output_summary };
}

/* One attempt's steps. No runId reaches the Timeline: its payload drill-down reads the estimation table. */
export const toTrace = (steps: readonly AgentRunStep[]): Trace => ({
  version: 1,
  summary: '',
  steps: steps.map(toTraceStep),
});

/* The ids a property link may be built from: the server checked them against the run's own tool results, and this keeps
 * anything else out of a URL even so. */
export const citedIds = (ids: readonly unknown[] | null | undefined): number[] =>
  (ids ?? []).filter((id): id is number => typeof id === 'number' && Number.isSafeInteger(id) && id > 0);

/* --- an input schema as a form -------------------------------------------------------------------------------------- */

export interface JsonSchema {
  type?: string;
  description?: string;
  enum?: string[];
  required?: string[];
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
}

export type FieldKind = 'text' | 'integer' | 'number' | 'choice' | 'boolean' | 'list' | 'json';

export interface FormField {
  name: string;
  kind: FieldKind;
  required: boolean;
  description?: string;
  options?: string[];
  listOfNumbers?: boolean;
}

function kindOf(schema: JsonSchema): FieldKind {
  if (schema.enum) return 'choice';
  if (schema.type === 'string') return 'text';
  if (schema.type === 'integer' || schema.type === 'number' || schema.type === 'boolean') return schema.type;
  if (schema.type === 'array' && ['string', 'integer', 'number'].includes(schema.items?.type ?? '')) return 'list';
  return 'json';
}

/* One field per top-level property; anything nested is typed as JSON. The server's schema check is the real one. */
export function fieldsOf(schema: JsonSchema): FormField[] {
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([name, property]) => ({
    name,
    kind: kindOf(property),
    required: required.has(name),
    description: property.description,
    options: property.enum,
    listOfNumbers: property.type === 'array' && property.items?.type !== 'string',
  }));
}

export type FormValues = Record<string, string | boolean | undefined>;

/* The field's typed value, undefined for an empty one, or throws its complaint. */
function parse(field: FormField, raw: string): unknown {
  if (field.kind === 'integer' || field.kind === 'number') {
    const n = Number(raw);
    if (field.kind === 'integer' && !Number.isSafeInteger(n)) throw new Error('A whole number.');
    if (!Number.isFinite(n)) throw new Error('A number.');
    return n;
  }
  if (field.kind === 'list') {
    const items = raw.split(',').map((s) => s.trim()).filter(Boolean);
    if (!field.listOfNumbers) return items;
    const numbers = items.map(Number);
    if (!numbers.every(Number.isFinite)) throw new Error('Whole numbers, separated by commas.');
    return numbers;
  }
  if (field.kind === 'json') {
    try {
      return JSON.parse(raw);
    } catch {
      throw new Error('Not valid JSON.');
    }
  }
  return raw;
}

export function buildInput(
  fields: readonly FormField[],
  values: FormValues,
): { input: Record<string, unknown>; errors: Record<string, string> } {
  const input: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const raw = values[field.name];
    if (field.kind === 'boolean') {
      if (raw === true || field.required) input[field.name] = raw === true;
      continue;
    }
    const text = typeof raw === 'string' ? raw.trim() : '';
    if (!text) {
      if (field.required) errors[field.name] = 'Required.';
      continue;
    }
    try {
      input[field.name] = parse(field, text);
    } catch (err) {
      errors[field.name] = (err as Error).message;
    }
  }
  return { input, errors };
}
