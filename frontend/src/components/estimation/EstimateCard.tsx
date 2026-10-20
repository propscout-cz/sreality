import type { ReactNode } from 'react';

/* The frame + eyebrow of every estimate figure on the property page, one copy:
 * a plain rule = an outside authority (MF, Valuo), the copper wash = ours,
 * dashed = absent (system.md). */
const TONE = {
  plain: 'border-[var(--color-rule)]',
  ours: 'border-[var(--color-copper)]/30 bg-[var(--color-copper-soft)]/40',
  absent: 'border-dashed border-[var(--color-rule)]',
} as const;

export default function EstimateCard({ eyebrow, aside, tone = 'plain', className = '', children }: {
  eyebrow: string;
  aside?: ReactNode;
  tone?: keyof typeof TONE;
  className?: string;
  children: ReactNode;
}) {
  const label = <p className="text-[0.6rem] tracking-[0.16em] uppercase text-[var(--color-ink-4)]">{eyebrow}</p>;
  return (
    <div className={`border ${TONE[tone]} rounded-[var(--radius-sm)] p-3 ${className}`.trim()}>
      {aside ? <div className="flex items-baseline justify-between gap-3">{label}{aside}</div> : label}
      {children}
    </div>
  );
}

/* A monthly figure's unit, muted after its number. */
export const PerMonth = () => <span className="ml-1 text-[0.7rem] text-[var(--color-ink-3)]">/měs</span>;

/* One label/value line of a card's breakdown; `strong` is its total, over a rule. */
export function CardRow({ label, strong = false, children }: { label: string; strong?: boolean; children: ReactNode }) {
  return (
    <div className={`flex justify-between gap-3${strong ? ' border-t border-[var(--color-rule)] pt-0.5' : ''}`}>
      <dt className={strong ? undefined : 'text-[var(--color-ink-3)]'}>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
