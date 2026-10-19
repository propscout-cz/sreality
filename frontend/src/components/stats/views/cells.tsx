import type { ReactNode } from 'react';
import { dimTitle, type StatsSpec } from '@/lib/stats';

/* The cells both pivot views (PivotTable, BoxTable) draw, defined once: the
 * numeric header and cell, the row-dimension header, a row's tone, and the
 * indented row header. */

export const NUM_TH =
  'px-3 py-1.5 text-right align-bottom text-[0.65rem] tracking-wide uppercase font-medium text-[var(--color-ink-3)] whitespace-nowrap';
export const NUM_TD = 'px-3 py-1.5 text-right font-mono tabular-nums whitespace-nowrap';
export const ROW_DIM_TH =
  'px-3 py-1.5 text-left align-bottom text-[0.65rem] tracking-wide uppercase font-medium text-[var(--color-ink-3)]';

/* "Dispozice › Stav" over the row headers; "Vzorek" for the whole sample. */
export const rowDimsLabel = (spec: StatsSpec, basis: string | null): string =>
  spec.rows.length ? spec.rows.map((d) => dimTitle(d, basis)).join(' › ') : 'Vzorek';

/* The whole-sample row of a nested table sits on the canvas tone. */
export const rowClass = (depth: number, levels: number): string =>
  `border-b border-[var(--color-rule-soft)] last:border-b-0${depth === 0 && levels > 0 ? ' bg-[var(--color-paper)]' : ''}`;

/* A row's own header, indented by depth. A rollup (a row with deeper rows under
 * it) reads in ink at medium weight, a leaf in ink-2 at normal weight: ONE
 * weight class per branch, because Tailwind emits .font-normal after
 * .font-medium and both on one element always rendered normal. */
export function RowHeader({ depth, rollup, children }: { depth: number; rollup: boolean; children: ReactNode }) {
  return (
    <th
      scope="row"
      className={`py-1.5 pr-3 text-left whitespace-nowrap ${
        rollup ? 'font-medium text-[var(--color-ink)]' : 'font-normal text-[var(--color-ink-2)]'
      }`}
      style={{ paddingLeft: `${0.75 + depth * 0.9}rem` }}
    >
      {children}
    </th>
  );
}
