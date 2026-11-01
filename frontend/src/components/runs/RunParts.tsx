import type { AgentRunStatus } from '@/lib/agentRuns';

/* A run's state word. A state, not a health verdict (StatusDot's rule), so the word sits in a chip: only an ending has a
 * tone, and a live run reads ochre, as an estimation run's chip does. */
const TONE: Readonly<Record<AgentRunStatus, string>> = {
  queued: 'bg-[var(--color-ochre-soft)] text-[var(--color-ochre)]',
  running: 'bg-[var(--color-ochre-soft)] text-[var(--color-ochre)]',
  succeeded: 'bg-[var(--color-sage-soft)] text-[var(--color-sage)]',
  failed: 'bg-[var(--color-brick-soft)] text-[var(--color-brick)]',
  cancelled: 'border border-[var(--color-rule-strong)] text-[var(--color-ink-3)]',
};

export function RunStateChip({ status }: { status: AgentRunStatus }) {
  return (
    <span className={`inline-block rounded-[var(--radius-xs)] px-2 py-0.5 text-[0.65rem] font-medium uppercase tracking-wide ${TONE[status]}`}>
      {status}
    </span>
  );
}

export const asJson = (value: unknown): string => {
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
