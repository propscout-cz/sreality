/* The rule that cuts the roll into days (#1762): a mono day label, a hairline to
 * the edge, and — on the first rule only — the refresh control, so "Dnes 14:32"
 * reads as "fresh as of 14:32" right where a refresh happens. */

import { RefreshIcon } from './phoneIcons';

export interface DayRuleProps {
  label: string;
  /* Present on the first rule: the refresh control. */
  onRefresh?: () => void;
  refreshing?: boolean;
}

export default function DayRule({ label, onRefresh, refreshing = false }: DayRuleProps) {
  return (
    <div className="flex min-h-8 items-center gap-2 pb-3 pl-4 pr-3 pt-4">
      <span className="whitespace-nowrap font-mono text-[11px] uppercase tracking-[0.06em] text-[var(--color-ink-3)]">
        {label}
      </span>
      <span aria-hidden className="h-px flex-1 bg-[var(--color-rule-strong)]" />
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          aria-busy={refreshing || undefined}
          className="inline-flex h-8 items-center gap-1.5 pl-2 pr-1 text-[12px] text-[var(--color-ink-2)] disabled:cursor-default disabled:text-[var(--color-ink-4)]"
        >
          <RefreshIcon
            className={`h-4 w-4 ${refreshing ? 'animate-spin motion-reduce:animate-none' : ''}`}
          />
          <span>{refreshing ? 'Načítám…' : 'Obnovit'}</span>
        </button>
      )}
    </div>
  );
}
