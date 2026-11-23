import type { AgentRunStatus } from '@/lib/agentRuns';

/* A run's state word in a chip: a state, not a health verdict (StatusDot's rule). The word stays ink, because ochre text
 * fails contrast, and the wash carries the tone: ochre while live, sage or brick at the end, a bare rule when cancelled. */
const TONE: Readonly<Record<AgentRunStatus, string>> = {
  queued: 'bg-[var(--color-ochre-soft)]',
  running: 'bg-[var(--color-ochre-soft)]',
  succeeded: 'bg-[var(--color-sage-soft)]',
  failed: 'bg-[var(--color-brick-soft)]',
  cancelled: 'border border-[var(--color-rule-strong)]',
};

export function RunStateChip({ status }: { status: AgentRunStatus }) {
  return (
    <span className={`inline-block rounded-[var(--radius-xs)] px-2 py-0.5 text-[0.78rem] font-medium text-[var(--color-ink)] ${TONE[status]}`}>
      {status}
    </span>
  );
}

const asJson = (value: unknown): string => {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
};

/* Untrusted structured data as text: JSON in a pre, which reads and links nothing. */
export function RunJson({ value }: { value: unknown }) {
  return (
    <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-xs)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-2 py-1.5 font-mono text-[0.72rem] text-[var(--color-ink-2)]">
      {asJson(value)}
    </pre>
  );
}
