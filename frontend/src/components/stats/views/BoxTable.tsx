import { valueAxisSpec } from '@/lib/chartAxis';
import {
  STAT_LABEL,
  cellTree,
  flattenTree,
  fmtN,
  fmtStat,
  measureLabel,
  measureUnit,
  nodeLabel,
  placeNames,
  rowKeyOf,
  type PivotNode,
  type StatsCell,
  type StatsPivot,
} from '@/lib/stats';
import { TooFew } from '../SampleLedger';
import { NUM_TD, NUM_TH, ROW_DIM_TH, RowHeader, rowClass, rowDimsLabel } from './cells';

type Drawn = StatsCell & { p10: number; p25: number; median: number; p75: number; p90: number };

const isDrawn = (c: StatsCell | undefined): c is Drawn =>
  c != null && c.p10 != null && c.p25 != null && c.median != null && c.p75 != null && c.p90 != null;

const QUANTILES = ['p10', 'p25', 'median', 'p75', 'p90'] as const;

/* The axis every box in the table shares: the drawn rows' p10..p90, through
 * valueAxisSpec. Built from the whiskers, never from a raw min/max, so one
 * outlier cannot squash every other box into a sliver. Exported for the test. */
export function boxAxis(nodes: readonly PivotNode[]) {
  const drawn = nodes.map((n) => n.cells.get('')).filter(isDrawn);
  if (drawn.length === 0) return null;
  const lo = Math.min(...drawn.map((c) => c.p10));
  const hi = Math.max(...drawn.map((c) => c.p90));
  return valueAxisSpec([lo, hi], { targetTicks: 4 });
}

/* THE box primitive. One row per pivot row, nested rows indented under their
 * rollup, n per row, the box on one shared axis, and the five numbers it draws
 * as columns of the same table — no tooltip, the numbers are right there. A row
 * under the minimum shows its n and the dashed mark, never a box. */
export default function BoxTable({ pivot }: { pivot: StatsPivot }) {
  const { spec } = pivot;
  const places = placeNames(pivot);
  const rows = flattenTree(cellTree(pivot.cells, spec, places).root);
  const axis = boxAxis(rows);
  const unit = measureUnit(spec.measure, pivot.basis);
  const [d0, d1] = axis?.domain ?? [0, 1];
  const pos = (v: number): number => (d1 === d0 ? 50 : ((v - d0) / (d1 - d0)) * 100);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[0.8rem]">
        <caption className="caption-top pb-2 text-left text-[0.7rem] text-[var(--color-ink-3)]">
          <span className="font-medium text-[var(--color-ink-2)]">
            {measureLabel(spec.measure)}
            {unit ? ` · ${unit}` : ''}
          </span>{' '}
          — vousy p10–p90, krabice p25–p75, čára medián
        </caption>
        <thead>
          <tr className="border-b border-[var(--color-rule)]">
            <th scope="col" className={ROW_DIM_TH}>
              {rowDimsLabel(spec, pivot.basis)}
            </th>
            <th scope="col" className={NUM_TH}>n</th>
            <th scope="col" className="min-w-[12rem] w-full px-3 py-1.5 align-bottom">
              <span className="sr-only">Rozložení</span>
              <span aria-hidden="true" className="relative block h-4">
                {axis?.ticks.map((t) => (
                  <span
                    key={t}
                    data-tick={t}
                    className="absolute bottom-0 font-mono tabular-nums text-[0.62rem] font-normal text-[var(--color-ink-3)] whitespace-nowrap"
                    style={{ left: `${pos(t)}%`, transform: `translateX(${tickShift(pos(t))})` }}
                  >
                    {axis.format(t)}
                  </span>
                ))}
              </span>
            </th>
            {QUANTILES.map((q) => (
              <th key={q} scope="col" className={NUM_TH}>
                {STAT_LABEL[q]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((node) => {
            const cell = node.cells.get('');
            const depth = node.keys.length;
            return (
              <tr key={rowKeyOf(node.keys)} className={rowClass(depth, spec.rows.length)}>
                <RowHeader depth={depth} rollup={depth < spec.rows.length}>
                  {nodeLabel(node, spec, pivot.basis, places)}
                </RowHeader>
                <td className={`${NUM_TD} text-[var(--color-ink-3)]`}>{fmtN(cell?.n ?? 0)}</td>
                {isDrawn(cell) && axis ? (
                  <>
                    <td className="px-3 py-1.5">
                      <Box cell={cell} ticks={axis.ticks} pos={pos} />
                    </td>
                    {QUANTILES.map((q) => (
                      <td
                        key={q}
                        className={`${NUM_TD} ${q === 'median' ? 'text-[var(--color-ink)] font-medium' : 'text-[var(--color-ink-2)]'}`}
                      >
                        {fmtStat(cell[q], spec.measure)}
                      </td>
                    ))}
                  </>
                ) : (
                  <td colSpan={1 + QUANTILES.length} className="px-3 py-1.5">
                    <TooFew minN={pivot.min_n} />
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* The end ticks hug the cell's edges instead of hanging half outside it. */
const tickShift = (p: number): string => (p <= 0.5 ? '0' : p >= 99.5 ? '-100%' : '-50%');

function Box({
  cell,
  ticks,
  pos,
}: {
  cell: Drawn;
  ticks: readonly number[];
  pos: (v: number) => number;
}) {
  const x10 = pos(cell.p10);
  const x25 = pos(cell.p25);
  const x50 = pos(cell.median);
  const x75 = pos(cell.p75);
  const x90 = pos(cell.p90);
  return (
    <span aria-hidden="true" data-box className="relative block h-5">
      {ticks.map((t) => (
        <span
          key={t}
          className="absolute inset-y-0 w-px bg-[var(--color-rule-soft)]"
          style={{ left: `${pos(t)}%` }}
        />
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
