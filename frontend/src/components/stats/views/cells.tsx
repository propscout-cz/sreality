import { Fragment, type ReactNode } from 'react';
import type { StatId } from '@/lib/statsRegistry.generated';
import { SMALL_LABEL, SMALL_STATS, rowsTitle, type PivotRow as Row, type SmallSample, type StatsSpec } from '@/lib/stats';

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

/* The whole-sample row of a nested table sits on the canvas tone; a row showing a
 * small sample sits on ochre-soft, so its own labels read as a different set. */
export const SMALL_BG = 'bg-[var(--color-ochre-soft)]';
const rowClass = (row: Row, small: boolean): string =>
  `border-b border-[var(--color-rule-soft)] last:border-b-0${small ? ` ${SMALL_BG}` : row.depth === 0 && row.rollup ? ' bg-[var(--color-paper)]' : ''}`;

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
export function PivotRow({ row, small = false, children }: { row: Row; small?: boolean; children: ReactNode }) {
  return (
    <tr className={rowClass(row, small)} data-small={small || undefined}>
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

/* A cell under the minimum, shown on request: min, průměr, medián, max, each with
 * its own label — the row's column headers name percentiles it never shows. */
export function SmallStats({ small, fmt }: { small: SmallSample; fmt: (v: number) => string }) {
  return (
    <span className="inline-flex flex-wrap items-baseline justify-end gap-x-3 gap-y-0.5">
      <span className="text-[0.62rem] uppercase tracking-[0.14em] text-[var(--color-ink-3)]">malý vzorek</span>
      {/* The spaces between items are text for assistive tech; flex collapses them on screen. */}
      {SMALL_STATS.map((s) => (
        <Fragment key={s}>
          {' '}
          <span className="whitespace-nowrap">
            <span className="text-[0.65rem] text-[var(--color-ink-3)]">{SMALL_LABEL[s]}</span>{' '}
            <span className="font-mono tabular-nums text-[var(--color-ink)]">{fmt(small[s])}</span>
          </span>
        </Fragment>
      ))}
    </span>
  );
}
