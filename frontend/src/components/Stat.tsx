import type { ReactNode } from 'react';
import type { Status } from '@/lib/healthChecks';
import StatusDot from './StatusDot';

/* One figure in a strip of tiles: a small-caps label, the value in mono tabular figures, an
 * optional hint. The number is never coloured: a status-bearing tile carries a StatusDot beside
 * its label instead (there is no AA-safe ochre text), its word read out but not shown, since the
 * label already names the tile. The hint is ink-3: ink-4 is for placeholders, never data. */
export default function Stat({
  label,
  value,
  hint,
  status,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  status?: Status;
}) {
  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--color-rule-soft)] bg-[var(--color-paper-2)] px-3 py-2">
      <div className="flex items-center gap-1.5 text-[0.62rem] tracking-[0.1em] uppercase text-[var(--color-ink-3)]">
        {status ? <StatusDot status={status} size="sm" word="sr-only" /> : null}
        <span>{label}</span>
      </div>
      <div className="font-mono tabular-nums text-xl text-[var(--color-ink)]">{value}</div>
      {hint ? <div className="text-[0.68rem] text-[var(--color-ink-3)]">{hint}</div> : null}
    </div>
  );
}
