import { memo, useMemo } from 'react';
import { STATS_REGISTRY, type ChartStat } from '@/lib/statsRegistry.generated';
import { fmtCount } from '@/lib/format';
import {
  STAT_LABEL,
  chartTitle,
  colValuesOf,
  dimTitle,
  keyLabel,
  measureUnit,
  pivotRows,
  quantityLabel,
  statFormat,
  statUsed,
  type StatsCell,
  type StatsPivot,
} from '@/lib/stats';
import { NUM_TD, NUM_TH, PivotRow, ROW_DIM_TH, SMALL_BG, SmallStats, TooFew, rowDimsLabel, statTd } from './cells';

const STATS = STATS_REGISTRY.chart_stats;

const COLGROUP_TH = 'px-3 pt-1.5 text-right text-[0.7rem] font-medium text-[var(--color-ink-2)]';

/* The pivot as a table. Nested rows are indented under their rollup row, which
 * sits above them. Without a column dimension a row carries the seven
 * statistics; with one, each column value carries the chart's picked statistics
 * side by side, then its n. The unit is named once, in the header, from the
 * payload's basis. */
function PivotTable({
  pivot,
  stats,
  showSmall = false,
}: {
  pivot: StatsPivot;
  stats: readonly ChartStat[];
  showSmall?: boolean;
}) {
  const { spec, basis, places, min_n: minN } = pivot;
  const rows = useMemo(() => pivotRows(pivot), [pivot]);
  const cols = useMemo(() => colValuesOf(pivot), [pivot]);
  const colDim = spec.cols[0];
  const perColumn = statUsed({ view: 'table', cols: spec.cols });
  const fmt = statFormat(spec.measure);
  const unit = measureUnit(spec.measure, basis);
  const quantity = quantityLabel(spec.measure, basis);
  /* A column value spans its statistics and its n. */
  const span = stats.length + 1;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[0.8rem]">
        <caption className="sr-only">
          {chartTitle(spec, basis)}
          {unit ? ` (${unit})` : ''}
        </caption>
        <thead>
          <tr className="border-b border-[var(--color-rule-soft)]">
            <th rowSpan={perColumn ? 3 : 2} scope="col" className={ROW_DIM_TH}>
              {rowDimsLabel(spec, basis)}
            </th>
            {!perColumn && (
              <th rowSpan={2} scope="col" className={NUM_TH}>
                n
              </th>
            )}
            <th colSpan={perColumn ? Math.max(1, cols.length * span) : STATS.length} scope="colgroup" className={COLGROUP_TH}>
              {perColumn ? `${dimTitle(colDim, basis)} — ${quantity}` : quantity}
            </th>
          </tr>
          {perColumn && (
            <tr className="border-b border-[var(--color-rule-soft)]">
              {cols.map((c) => (
                <th key={c} colSpan={span} scope="colgroup" className={COLGROUP_TH}>
                  {keyLabel(colDim, c, basis, places)}
                </th>
              ))}
            </tr>
          )}
          <tr className="border-b border-[var(--color-rule)]">
            {perColumn
              ? cols.flatMap((c) => [
                  ...stats.map((s) => (
                    <th key={`${c}|${s}`} scope="col" className={NUM_TH}>
                      {STAT_LABEL[s]}
                    </th>
                  )),
                  <th key={`${c}|n`} scope="col" className={NUM_TH}>
                    n
                  </th>,
                ])
              : STATS.map((s) => (
                  <th key={s} scope="col" className={NUM_TH}>
                    {STAT_LABEL[s]}
                  </th>
                ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <PivotRow key={row.key} row={row} small={!perColumn && smallOf(row.cells.get(''), showSmall) != null}>
              {perColumn ? (
                cols.map((c) => (
                  <ColumnCells key={c} cell={row.cells.get(c)} stats={stats} fmt={fmt} minN={minN} showSmall={showSmall} />
                ))
              ) : (
                <SevenStats cell={row.cells.get('')} fmt={fmt} minN={minN} showSmall={showSmall} />
              )}
            </PivotRow>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(PivotTable);

/* The small-sample summary a cell under the minimum shows, when asked for. */
const smallOf = (cell: StatsCell | undefined, showSmall: boolean) =>
  showSmall && cell != null && cell.median == null ? cell.small ?? null : null;

function SevenStats({
  cell,
  fmt,
  minN,
  showSmall,
}: {
  cell: StatsCell | undefined;
  fmt: (v: number) => string;
  minN: number;
  showSmall: boolean;
}) {
  if (!cell) {
    return (
      <>
        <td className={`${NUM_TD} text-[var(--color-ink-3)]`}>0</td>
        <td colSpan={STATS.length} />
      </>
    );
  }
  /* The server nulls the six statistics together below the minimum. */
  if (cell.median == null) {
    const small = smallOf(cell, showSmall);
    return (
      <>
        <td className={`${NUM_TD} text-[var(--color-ink-2)]`}>{fmtCount(cell.n)}</td>
        <td colSpan={STATS.length} className="px-3 py-1.5 text-right">
          {small ? <SmallStats small={small} fmt={fmt} /> : <TooFew minN={minN} />}
        </td>
      </>
    );
  }
  return (
    <>
      <td className={`${NUM_TD} text-[var(--color-ink-3)]`}>{fmtCount(cell.n)}</td>
      {STATS.map((s) => (
        <td key={s} className={statTd(s)}>
          {fmt(cell[s] as number)}
        </td>
      ))}
    </>
  );
}

/* One column value's cells: the picked statistics, then its n. */
function ColumnCells({
  cell,
  stats,
  fmt,
  minN,
  showSmall,
}: {
  cell: StatsCell | undefined;
  stats: readonly ChartStat[];
  fmt: (v: number) => string;
  minN: number;
  showSmall: boolean;
}) {
  if (!cell) {
    return (
      <td colSpan={stats.length + 1} className={`${NUM_TD} text-[var(--color-ink-4)]`}>
        <span aria-hidden="true">·</span>
        <span className="sr-only">žádná nemovitost</span>
      </td>
    );
  }
  const n = <td className={`${NUM_TD} text-[var(--color-ink-3)]`}>{fmtCount(cell.n)}</td>;
  /* The server nulls the statistics together below the minimum. A shown small sample
   * tints only its own cells: the row's other column values may be full. */
  if (cell.median == null) {
    const small = smallOf(cell, showSmall);
    return (
      <>
        <td colSpan={stats.length} className={`px-3 py-1.5 text-right${small ? ` ${SMALL_BG}` : ''}`}>
          {small ? <SmallStats small={small} fmt={fmt} /> : <TooFew minN={minN} />}
        </td>
        {n}
      </>
    );
  }
  return (
    <>
      {stats.map((s) => (
        <td key={s} className={statTd(s)}>
          {fmt(cell[s] as number)}
        </td>
      ))}
      {n}
    </>
  );
}
