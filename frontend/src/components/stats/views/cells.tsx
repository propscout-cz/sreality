import type { ReactNode } from 'react';
import type { StatId } from '@/lib/statsRegistry.generated';
import { rowsTitle, type PivotRow as Row, type StatsSpec } from '@/lib/stats';

/* The cells both pivot views (PivotTable, BoxTable) draw, defined once: the
 * numeric header and cell, a statistic's cell, the row-dimension header, one
 * nested row with its indented header, and the dashed "too few" mark. */

export const NUM_TH =
  'px-3 py-1.5 text-right align-bottom text-[0.65rem] tracking-wide uppercase font-medium text-[var(--color-ink-3)] whitespace-nowrap';
export const NUM_TD = 'px-3 py-1.5 text-right font-mono tabular-nums whitespace-nowrap';
export const ROW_DIM_TH =
  'px-3 py-1.5 text-left align-bottom text-[0.65rem] tracking-wide uppercase font-medium text-[var(--color-ink-3)]';

/* A statistic's cell: the median in ink at medium weight, the rest in ink-2. */
export const statTd = (s: StatId): string =>
  `${NUM_TD} ${s === 'median' ? 'text-[var(--color-ink)] font-medium' : 'text-[var(--color-ink-2)]'}`;

/* "Dispozice › Stav" over the row headers; "Vzorek" for the whole sample. */
export const rowDimsLabel = (spec: StatsSpec, basis: string | null): string => rowsTitle(spec, basis) || 'Vzorek';

/* The whole-sample row of a nested table sits on the canvas tone. */
const rowClass = (row: Row): string =>
  `border-b border-[var(--color-rule-soft)] last:border-b-0${row.depth === 0 && row.rollup ? ' bg-[var(--color-paper)]' : ''}`;

/* A row's own header, indented by depth. A rollup (a row with deeper rows under
 * it) reads in ink at medium weight, a leaf in ink-2 at normal weight: ONE
 * weight class per branch, because Tailwind emits .font-normal after
 * .font-medium and both on one element always rendered normal. */
function RowHeader({ row }: { row: Row }) {
  return (
    <th
      scope="row"
      className={`py-1.5 pr-3 text-left whitespace-nowrap ${
        row.rollup ? 'font-medium text-[var(--color-ink)]' : 'font-normal text-[var(--color-ink-2)]'
      }`}
      style={{ paddingLeft: `${0.75 + row.depth * 0.9}rem` }}
    >
      {row.label}
    </th>
  );
}

/* One pivot row: its indented header, then the view's cells. */
export function PivotRow({ row, children }: { row: Row; children: ReactNode }) {
  return (
    <tr className={rowClass(row)}>
      <RowHeader row={row} />
      {children}
    </tr>
  );
}

/* The dashed "too few" mark: a cell under the minimum shows its n and this,
 * never a number. The same dashed vocabulary as an empty shelf slot — absence
 * is drawn, not left blank. */
export function TooFew({ minN }: { minN: number }) {
  return (
    <span className="inline-flex items-center rounded-[var(--radius-xs)] border border-dashed border-[var(--color-rule-strong)] px-1.5 py-px text-[0.65rem] leading-tight text-[var(--color-ink-3)] whitespace-nowrap">
      <span aria-hidden="true">pod {minN}</span>
      <span className="sr-only">méně než {minN} nemovitostí, bez statistik</span>
    </span>
  );
}
