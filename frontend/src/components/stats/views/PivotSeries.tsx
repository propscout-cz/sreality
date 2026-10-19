import { memo, useId, useMemo } from 'react';
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import type { ChartStat } from '@/lib/statsRegistry.generated';
import { numericDomain, valueAxisSpec } from '@/lib/chartAxis';
import { useTokenColors } from '@/lib/useTokenColors';
import {
  SERIES_TOKENS,
  STAT_LABEL,
  dimTitle,
  measureUnit,
  statFormat,
  withUnit,
  type SeriesModel,
  type StatsPivot,
} from '@/lib/stats';

const TOKEN_KEYS = ['--color-ink-3', '--color-rule', ...SERIES_TOKENS];
const MARGIN = { top: 8, right: 14, bottom: 0, left: 0 };
const HEIGHT = 240;

/* Copper first, then the ink tints; past the palette the colours repeat dashed
 * (a line) or lighter (a bar). One object per theme, so the plot's props keep
 * their identity between renders. */
function useSeriesColors() {
  const colors = useTokenColors(TOKEN_KEYS);
  return useMemo(() => {
    const axis = colors['--color-ink-3'] || '#7a7d86';
    const repeat = (i: number) => i >= SERIES_TOKENS.length;
    return {
      strokeOf: (i: number) => colors[SERIES_TOKENS[i % SERIES_TOKENS.length]] || '#3c6e63',
      dashOf: (i: number) => (repeat(i) ? '5 3' : undefined),
      fillOpacityOf: (i: number) => (repeat(i) ? 0.55 : 1),
      axis,
      grid: colors['--color-rule'] || 'rgba(26,28,34,0.08)',
      tick: { fill: axis, fontSize: 11 },
    };
  }, [colors]);
}

/* The line and bar views: one row dimension on x, the column dimension as
 * series, axes through valueAxisSpec — a bar's from zero, because its length is
 * its value. Around the plot: the value caption (which statistic, in which unit
 * — from the payload's basis), the series key, the honest note when a point is
 * missing because its cell is under the minimum, and the plot's text
 * alternative: the same numbers as a screen-reader table (the SVG itself is
 * hidden from assistive tech — it is geometry, not text). */
function PivotSeries({
  pivot,
  model,
  stat,
  kind,
}: {
  pivot: StatsPivot;
  model: SeriesModel;
  stat: ChartStat;
  kind: 'line' | 'bar';
}) {
  const c = useSeriesColors();
  const captionId = useId();
  const y = useMemo(
    () => valueAxisSpec(numericDomain(model.data, model.series.map((s) => s.dataKey)), { targetTicks: 5, zeroBased: kind === 'bar' }),
    [model, kind],
  );
  const dots = useMemo(
    () => model.series.map((_, i) => ({ r: 2.5, strokeWidth: 0, fill: c.strokeOf(i) })),
    [model, c],
  );
  const axes = [
    <CartesianGrid key="grid" stroke={c.grid} vertical={false} />,
    <XAxis key="x" dataKey="label" type="category" tick={c.tick} stroke={c.axis} minTickGap={8} />,
    <YAxis key="y" domain={y.domain} ticks={y.ticks} tickFormatter={y.format} tick={c.tick} stroke={c.axis} width={62} />,
  ];

  return (
    <figure className="m-0">
      <figcaption id={captionId} className="mb-1 text-[0.7rem] text-[var(--color-ink-3)]">
        {withUnit(STAT_LABEL[stat], measureUnit(pivot.spec.measure, pivot.basis))}
      </figcaption>
      <div aria-hidden="true">
        <ResponsiveContainer width="100%" height={HEIGHT}>
          {kind === 'line' ? (
            <LineChart data={model.data} margin={MARGIN}>
              {axes}
              {model.series.map((s, i) => (
                <Line
                  key={s.dataKey}
                  type="linear"
                  dataKey={s.dataKey}
                  name={s.label}
                  stroke={c.strokeOf(i)}
                  strokeWidth={i === 0 ? 2 : 1.6}
                  strokeDasharray={c.dashOf(i)}
                  dot={dots[i]}
                  activeDot={false}
                  // NOT connectNulls: a missing point is a cell under the minimum —
                  // bridging it would draw a number the server withheld.
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          ) : (
            <BarChart data={model.data} margin={MARGIN} barCategoryGap="18%">
              {axes}
              {model.series.map((s, i) => (
                <Bar
                  key={s.dataKey}
                  dataKey={s.dataKey}
                  name={s.label}
                  fill={c.strokeOf(i)}
                  fillOpacity={c.fillOpacityOf(i)}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      <SeriesTable pivot={pivot} model={model} labelledBy={captionId} />
      {model.series.length > 1 && (
        <ul aria-label="Řady grafu" className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[0.72rem] text-[var(--color-ink-2)]">
          {model.series.map((s, i) => (
            <li key={s.dataKey} className="flex items-center gap-1.5">
              <svg width="16" height="8" aria-hidden="true">
                {kind === 'line' ? (
                  <line x1="0" y1="4" x2="16" y2="4" stroke={c.strokeOf(i)} strokeWidth="2" strokeDasharray={c.dashOf(i)} />
                ) : (
                  <rect x="3" y="0" width="10" height="8" fill={c.strokeOf(i)} opacity={c.fillOpacityOf(i)} />
                )}
              </svg>
              {s.label}
            </li>
          ))}
        </ul>
      )}
      {model.gaps && (
        <p className="mt-1.5 text-[0.7rem] text-[var(--color-ink-3)]">
          Chybějící bod = buňka pod {pivot.min_n} nemovitostí nebo bez nemovitostí; čísla ukáže Tabulka.
        </p>
      )}
    </figure>
  );
}

export default memo(PivotSeries);

function SeriesTable({ pivot, model, labelledBy }: { pivot: StatsPivot; model: SeriesModel; labelledBy: string }) {
  const x = pivot.spec.rows[0];
  const fmt = statFormat(pivot.spec.measure);
  return (
    <table className="sr-only" aria-labelledby={labelledBy}>
      <thead>
        <tr>
          <th scope="col">{x ? dimTitle(x, pivot.basis) : ''}</th>
          {model.series.map((s) => (
            <th key={s.dataKey} scope="col">
              {s.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {model.data.map((row) => (
          <tr key={String(row.x)}>
            <th scope="row">{row.label}</th>
            {model.series.map((s) => {
              const v = row[s.dataKey];
              return <td key={s.dataKey}>{typeof v === 'number' ? fmt(v) : '—'}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
