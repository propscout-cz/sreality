import type { ChartStat, StatId } from '@/lib/statsRegistry.generated';
import {
  STAT_LABEL,
  cellKeyOf,
  cellTree,
  chartTitle,
  colKey,
  dimTitle,
  flattenTree,
  fmtN,
  fmtStat,
  keyLabel,
  measureLabel,
  measureUnit,
  nodeLabel,
  placeNames,
  rowKeyOf,
  type StatsCell,
  type StatsPivot,
} from '@/lib/stats';
import { TooFew } from '../SampleLedger';
import { NUM_TD, NUM_TH, ROW_DIM_TH, RowHeader, rowClass, rowDimsLabel } from './cells';

const SIX: ReadonlyArray<Exclude<StatId, 'n'>> = ['mean', 'median', 'p10', 'p25', 'p75', 'p90'];

/* The pivot as a table. Nested rows are indented under their rollup row, which
 * sits above them. Without a column dimension a row carries the seven
 * statistics; with one, each column carries the chart's statistic with its n
 * beneath. The unit is named once, in the header, from the payload's basis. */
export default function PivotTable({ pivot, stat }: { pivot: StatsPivot; stat: ChartStat }) {
  const { spec } = pivot;
  const places = placeNames(pivot);
  const tree = cellTree(pivot.cells, spec, places);
  const rows = flattenTree(tree.root);
  const unit = measureUnit(spec.measure, pivot.basis);
  const quantity = `${measureLabel(spec.measure)}${unit ? ` · ${unit}` : ''}`;
  const colDim = spec.cols[0];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[0.8rem]">
        <caption className="sr-only">
          {chartTitle(spec, pivot.basis)}
          {unit ? ` (${unit})` : ''}
        </caption>
        <thead>
          <tr className="border-b border-[var(--color-rule-soft)]">
            <th rowSpan={2} scope="col" className={ROW_DIM_TH}>
              {rowDimsLabel(spec, pivot.basis)}
            </th>
            {colDim ? (
              <th
                colSpan={Math.max(1, tree.cols.length)}
                scope="colgroup"
                className="px-3 pt-1.5 text-right text-[0.7rem] font-medium text-[var(--color-ink-2)]"
              >
                {dimTitle(colDim, pivot.basis)} — {STAT_LABEL[stat]}, {quantity}
              </th>
            ) : (
              <>
                <th rowSpan={2} scope="col" className={NUM_TH}>
                  n
                </th>
                <th
                  colSpan={SIX.length}
                  scope="colgroup"
                  className="px-3 pt-1.5 text-right text-[0.7rem] font-medium text-[var(--color-ink-2)]"
                >
                  {quantity}
                </th>
              </>
            )}
          </tr>
          <tr className="border-b border-[var(--color-rule)]">
            {colDim
              ? tree.cols.map((c) => (
                  <th key={c} scope="col" className={NUM_TH}>
                    {keyLabel(colDim, c, pivot.basis, places)}
                  </th>
                ))
              : SIX.map((s) => (
                  <th key={s} scope="col" className={NUM_TH}>
                    {STAT_LABEL[s]}
                  </th>
                ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((node) => {
            const depth = node.keys.length;
            return (
              <tr key={rowKeyOf(node.keys)} className={rowClass(depth, spec.rows.length)}>
                <RowHeader depth={depth} rollup={depth < spec.rows.length}>
                  {nodeLabel(node, spec, pivot.basis, places)}
                </RowHeader>
                {colDim ? (
                  tree.cols.map((c) => (
                    <ColumnCell
                      key={cellKeyOf(node.keys, c)}
                      cell={node.cells.get(colKey(c))}
                      stat={stat}
                      measure={spec.measure}
                      minN={pivot.min_n}
                    />
                  ))
                ) : (
                  <SevenStats cell={node.cells.get('')} measure={spec.measure} minN={pivot.min_n} />
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SevenStats({
  cell,
  measure,
  minN,
}: {
  cell: StatsCell | undefined;
  measure: string;
  minN: number;
}) {
  if (!cell) {
    return (
      <>
        <td className={`${NUM_TD} text-[var(--color-ink-3)]`}>0</td>
        <td colSpan={SIX.length} />
      </>
    );
  }
  if (cell.median == null) {
    return (
      <>
        <td className={`${NUM_TD} text-[var(--color-ink-2)]`}>{fmtN(cell.n)}</td>
        <td colSpan={SIX.length} className="px-3 py-1.5 text-right">
          <TooFew minN={minN} />
        </td>
      </>
    );
  }
  return (
    <>
      <td className={`${NUM_TD} text-[var(--color-ink-3)]`}>{fmtN(cell.n)}</td>
      {SIX.map((s) => {
        const v = cell[s];
        return (
          <td
            key={s}
            className={`${NUM_TD} ${s === 'median' ? 'text-[var(--color-ink)] font-medium' : 'text-[var(--color-ink-2)]'}`}
          >
            {v == null ? '—' : fmtStat(v, measure)}
          </td>
        );
      })}
    </>
  );
}

function ColumnCell({
  cell,
  stat,
  measure,
  minN,
}: {
  cell: StatsCell | undefined;
  stat: ChartStat;
  measure: string;
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
        {v == null ? (
          <TooFew minN={minN} />
        ) : (
          <span className="text-[var(--color-ink)]">{fmtStat(v, measure)}</span>
        )}
      </span>
      <span className="block text-[0.65rem] leading-tight text-[var(--color-ink-3)]">
        n {fmtN(cell.n)}
      </span>
    </td>
  );
}
