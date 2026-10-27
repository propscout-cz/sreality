import { memo, useId, useMemo, type ReactNode } from 'react';
import { valueAxisSpec } from '@/lib/chartAxis';
import { fmtCount } from '@/lib/format';
import {
  STAT_LABEL,
  pivotRows,
  quantityLabel,
  statFormat,
  type PivotRow as Row,
  type StatsCell,
  type StatsPivot,
} from '@/lib/stats';
import { NUM_TD, NUM_TH, PivotRow, ROW_DIM_TH, SmallStats, TooFew, rowDimsLabel, statTd } from './cells';

type Drawn = StatsCell & { p10: number; p25: number; median: number; p75: number; p90: number };

/* A whole box: the five numbers it draws are all there. */
const isDrawn = (c: StatsCell | undefined): c is Drawn =>
  c != null && c.p10 != null && c.p25 != null && c.median != null && c.p75 != null && c.p90 != null;

const QUANTILES = ['p10', 'p25', 'median', 'p75', 'p90'] as const;
/* The columns: the mean first (not drawn — the box has no mean mark), then
 * the five quantiles in the order the box draws them, left to right. */
const COLUMNS = ['mean', ...QUANTILES] as const;

/* The axis every box in the table shares: the drawn rows' p10..p90, through
 * valueAxisSpec. Built from the whiskers, never from a raw min/max, so one
 * outlier cannot squash every other box into a sliver. Exported for the test. */
export function boxAxis(rows: readonly Row[]) {
  const drawn = rows.map((r) => r.cells.get('')).filter(isDrawn);
  if (drawn.length === 0) return null;
  const lo = Math.min(...drawn.map((c) => c.p10));
  const hi = Math.max(...drawn.map((c) => c.p90));
  return valueAxisSpec([lo, hi], { targetTicks: 4 });
}

interface Tick {
  value: number;
  label: string;
  /* Its position on the axis, in percent of the cell. */
  at: number;
}

/* THE box primitive. One row per pivot row, nested rows indented under their
 * rollup, n per row, the box on one shared axis, and the mean plus the five
 * numbers it draws as columns of the same table — no tooltip, the numbers are
 * right there. A row under the minimum shows its n and the dashed mark, never
 * a box. The legend names the table; a `notice` sits between it and the column
 * heads, outside the table's name and its sideways scroll (ruling S09). */
function BoxTable({
  pivot,
  showSmall = false,
  notice,
}: {
  pivot: StatsPivot;
  showSmall?: boolean;
  notice?: ReactNode;
}) {
  const legendId = useId();
  const { spec, basis } = pivot;
  const rows = useMemo(() => pivotRows(pivot), [pivot]);
  /* The shared scale, its ticks placed once for the header and every row. */
  const scale = useMemo(() => {
    const axis = boxAxis(rows);
    if (!axis) return null;
    const [d0, d1] = axis.domain;
    const pos = (v: number): number => (d1 === d0 ? 50 : ((v - d0) / (d1 - d0)) * 100);
    const ticks: Tick[] = axis.ticks.map((t) => ({ value: t, label: axis.format(t), at: pos(t) }));
    return { pos, ticks };
  }, [rows]);
  const fmt = statFormat(spec.measure);

  return (
    <>
      <p id={legendId} className="pb-2 text-left text-[0.7rem] text-[var(--color-ink-3)]">
        <span className="font-medium text-[var(--color-ink-2)]">{quantityLabel(spec.measure, basis)}</span>{' '}
        — vousy p10–p90, krabice p25–p75, čára medián
      </p>
      {notice}
      <div className="overflow-x-auto">
        <table aria-labelledby={legendId} className="w-full text-[0.8rem]">
          <thead>
            <tr className="border-b border-[var(--color-rule)]">
              <th scope="col" className={ROW_DIM_TH}>
                {rowDimsLabel(spec, basis)}
              </th>
              <th scope="col" className={NUM_TH}>n</th>
              <th scope="col" className="min-w-[12rem] w-full px-3 py-1.5 align-bottom">
                <span className="sr-only">Rozložení</span>
                <span aria-hidden="true" className="relative block h-4">
                  {scale?.ticks.map((t) => (
                    <span
                      key={t.value}
                      data-tick={t.value}
                      className="absolute bottom-0 font-mono tabular-nums text-[0.62rem] font-normal text-[var(--color-ink-3)] whitespace-nowrap"
                      style={{ left: `${t.at}%`, transform: `translateX(${tickShift(t.at)})` }}
                    >
                      {t.label}
                    </span>
                  ))}
                </span>
              </th>
              {COLUMNS.map((q) => (
                <th key={q} scope="col" className={NUM_TH}>
                  {STAT_LABEL[q]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const cell = row.cells.get('');
              const small = showSmall && !isDrawn(cell) ? cell?.small ?? null : null;
              return (
                <PivotRow key={row.key} row={row} small={small != null}>
                  <td className={`${NUM_TD} text-[var(--color-ink-3)]`}>{fmtCount(cell?.n ?? 0)}</td>
                  {isDrawn(cell) && scale ? (
                    <>
                      <td className="px-3 py-1.5">
                        <Box cell={cell} ticks={scale.ticks} pos={scale.pos} />
                      </td>
                      {COLUMNS.map((q) => (
                        <td key={q} className={statTd(q)}>
                          {cell[q] == null ? '—' : fmt(cell[q])}
                        </td>
                      ))}
                    </>
                  ) : small ? (
                    <td colSpan={1 + COLUMNS.length} className="px-3 py-1.5 text-right">
                      <SmallStats small={small} fmt={fmt} />
                    </td>
                  ) : (
                    <td colSpan={1 + COLUMNS.length} className="px-3 py-1.5">
                      <TooFew minN={pivot.min_n} />
                    </td>
                  )}
                </PivotRow>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default memo(BoxTable);

/* The end ticks hug the cell's edges instead of hanging half outside it. */
const tickShift = (p: number): string => (p <= 0.5 ? '0' : p >= 99.5 ? '-100%' : '-50%');

function Box({ cell, ticks, pos }: { cell: Drawn; ticks: readonly Tick[]; pos: (v: number) => number }) {
  const x10 = pos(cell.p10);
  const x25 = pos(cell.p25);
  const x50 = pos(cell.median);
  const x75 = pos(cell.p75);
  const x90 = pos(cell.p90);
  return (
    <span aria-hidden="true" data-box className="relative block h-5">
      {ticks.map((t) => (
        <span key={t.value} className="absolute inset-y-0 w-px bg-[var(--color-rule-soft)]" style={{ left: `${t.at}%` }} />
      ))}
      <span
        data-whisker
        className="absolute top-1/2 h-px bg-[var(--color-ink-3)]"
        style={{ left: `${x10}%`, width: `${x90 - x10}%` }}
      />
      <span className="absolute top-[30%] h-[40%] w-px bg-[var(--color-ink-3)]" style={{ left: `${x10}%` }} />
      <span className="absolute top-[30%] h-[40%] w-px bg-[var(--color-ink-3)]" style={{ left: `${x90}%` }} />
      <span
        data-iqr
        className="absolute top-[20%] h-[60%] border border-[var(--color-ink-2)] bg-[var(--color-paper-3)]"
        style={{ left: `${x25}%`, width: `max(1px, ${x75 - x25}%)` }}
      />
      <span
        data-median
        className="absolute top-[12%] h-[76%] w-[2px] -translate-x-1/2 bg-[var(--color-copper)]"
        style={{ left: `${x50}%` }}
      />
    </span>
  );
}
