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
import { NUM_TD, NUM_TH, PivotRow, ROW_DIM_TH, TooFew, rowDimsLabel, statTd } from './cells';

const STATS = STATS_REGISTRY.chart_stats;

const COLGROUP_TH = 'px-3 pt-1.5 text-right text-[0.7rem] font-medium text-[var(--color-ink-2)]';

/* The pivot as a table. Nested rows are indented under their rollup row, which
 * sits above them. Without a column dimension a row carries the seven
 * statistics; with one, each column carries the chart's statistic with its n
 * beneath. The unit is named once, in the header, from the payload's basis. */
function PivotTable({ pivot, stat }: { pivot: StatsPivot; stat: ChartStat }) {
  const { spec, basis, places, min_n: minN } = pivot;
  const rows = useMemo(() => pivotRows(pivot), [pivot]);
  const cols = useMemo(() => colValuesOf(pivot), [pivot]);
  const colDim = spec.cols[0];
  const perColumn = statUsed({ view: 'table', cols: spec.cols });
  const fmt = statFormat(spec.measure);
  const unit = measureUnit(spec.measure, basis);
  const quantity = quantityLabel(spec.measure, basis);
  /* One header model: a column per column value, or per chart statistic. */
  const heads = perColumn
    ? cols.map((c) => ({ key: c, label: keyLabel(colDim, c, basis, places) }))
    : STATS.map((s) => ({ key: s, label: STAT_LABEL[s] }));

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[0.8rem]">
        <caption className="sr-only">
          {chartTitle(spec, basis)}
          {unit ? ` (${unit})` : ''}
        </caption>
        <thead>
          <tr className="border-b border-[var(--color-rule-soft)]">
            <th rowSpan={2} scope="col" className={ROW_DIM_TH}>
              {rowDimsLabel(spec, basis)}
            </th>
            {!perColumn && (
              <th rowSpan={2} scope="col" className={NUM_TH}>
                n
              </th>
            )}
            <th colSpan={Math.max(1, heads.length)} scope="colgroup" className={COLGROUP_TH}>
              {perColumn ? `${dimTitle(colDim, basis)} — ${STAT_LABEL[stat]}, ${quantity}` : quantity}
            </th>
          </tr>
          <tr className="border-b border-[var(--color-rule)]">
            {heads.map((h) => (
              <th key={h.key} scope="col" className={NUM_TH}>
                {h.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <PivotRow key={row.key} row={row}>
              {perColumn ? (
                cols.map((c) => <ColumnCell key={c} cell={row.cells.get(c)} stat={stat} fmt={fmt} minN={minN} />)
              ) : (
                <SevenStats cell={row.cells.get('')} fmt={fmt} minN={minN} />
              )}
            </PivotRow>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(PivotTable);

function SevenStats({
  cell,
  fmt,
  minN,
}: {
  cell: StatsCell | undefined;
  fmt: (v: number) => string;
  minN: number;
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
    return (
      <>
        <td className={`${NUM_TD} text-[var(--color-ink-2)]`}>{fmtCount(cell.n)}</td>
        <td colSpan={STATS.length} className="px-3 py-1.5 text-right">
          <TooFew minN={minN} />
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

function ColumnCell({
  cell,
  stat,
  fmt,
  minN,
}: {
  cell: StatsCell | undefined;
  stat: ChartStat;
  fmt: (v: number) => string;
  minN: number;
}) {
  if (!cell) {
    return (
      <td className={`${NUM_TD} text-[var(--color-ink-4)]`}>
        <span aria-hidden="true">·</span>
        <span className="sr-only">žádná nemovitost</span>
      </td>
    );
  }
  const v = cell[stat];
  return (
    <td className={`${NUM_TD} align-top`}>
      <span className="block leading-tight">
        {v == null ? <TooFew minN={minN} /> : <span className="text-[var(--color-ink)]">{fmt(v)}</span>}
      </span>
      <span className="block text-[0.65rem] leading-tight text-[var(--color-ink-3)]">n {fmtCount(cell.n)}</span>
    </td>
  );
}
