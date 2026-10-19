import { useId, type ReactNode } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import type { ChartStat } from '@/lib/statsRegistry.generated';
import { numericDomain, valueAxisSpec, type ValueAxisSpec } from '@/lib/chartAxis';
import { useTokenColors } from '@/lib/useTokenColors';
import {
  SERIES_TOKENS,
  STAT_LABEL,
  dimTitle,
  fmtStat,
  measureUnit,
  seriesModel,
  type SeriesModel,
  type StatsPivot,
} from '@/lib/stats';

const TOKEN_KEYS = ['--color-ink-3', '--color-rule', ...SERIES_TOKENS];

/* Copper first, then the ink tints; past the palette the colours repeat dashed. */
export function useSeriesColors(): {
  strokeOf: (i: number) => string;
  dashOf: (i: number) => string | undefined;
  axis: string;
  grid: string;
} {
  const colors = useTokenColors(TOKEN_KEYS);
  return {
    strokeOf: (i) => colors[SERIES_TOKENS[i % SERIES_TOKENS.length]] || '#3c6e63',
    dashOf: (i) => (i >= SERIES_TOKENS.length ? '5 3' : undefined),
    axis: colors['--color-ink-3'] || '#7a7d86',
    grid: colors['--color-rule'] || 'rgba(26,28,34,0.08)',
  };
}

/* What the line and bar views share around the plot: the value caption (which
 * statistic, in which unit — from the payload's basis), the series key, the
 * honest note when a point is missing because its cell is under the minimum,
 * and the plot's text alternative: the same numbers as a screen-reader table
 * (the SVG itself is hidden from assistive tech — it is geometry, not text). */
export function SeriesFrame({
  pivot,
  stat,
  model,
  strokeOf,
  dashOf,
  swatch,
  children,
}: {
  pivot: StatsPivot;
  stat: ChartStat;
  model: SeriesModel;
  strokeOf: (i: number) => string;
  dashOf: (i: number) => string | undefined;
  swatch: 'line' | 'block';
  children: ReactNode;
}) {
  const unit = measureUnit(pivot.spec.measure, pivot.basis);
  const captionId = useId();
  return (
    <figure className="m-0">
      <figcaption id={captionId} className="mb-1 text-[0.7rem] text-[var(--color-ink-3)]">
        {STAT_LABEL[stat]}
        {unit ? ` · ${unit}` : ''}
      </figcaption>
      <div aria-hidden="true">{children}</div>
      <SeriesTable pivot={pivot} model={model} labelledBy={captionId} />
      {model.series.length > 1 && (
        <ul aria-label="Řady grafu" className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[0.72rem] text-[var(--color-ink-2)]">
          {model.series.map((s, i) => (
            <li key={s.dataKey} className="flex items-center gap-1.5">
              <svg width="16" height="8" aria-hidden="true">
                {swatch === 'line' ? (
                  <line x1="0" y1="4" x2="16" y2="4" stroke={strokeOf(i)} strokeWidth="2" strokeDasharray={dashOf(i)} />
                ) : (
                  <rect x="3" y="0" width="10" height="8" fill={strokeOf(i)} opacity={dashOf(i) ? 0.55 : 1} />
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

function SeriesTable({ pivot, model, labelledBy }: { pivot: StatsPivot; model: SeriesModel; labelledBy: string }) {
  const x = pivot.spec.rows[0];
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
              return <td key={s.dataKey}>{typeof v === 'number' ? fmtStat(v, pivot.spec.measure) : '—'}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function axisFor(model: SeriesModel, zeroBased: boolean): ValueAxisSpec {
  return valueAxisSpec(
    numericDomain(model.data, model.series.map((s) => s.dataKey)),
    { targetTicks: 5, zeroBased },
  );
}

/* One row dimension on x, the column dimension as series; axes through valueAxisSpec. */
export default function PivotLines({ pivot, stat }: { pivot: StatsPivot; stat: ChartStat }) {
  const model = seriesModel(pivot, stat);
  const { strokeOf, dashOf, axis, grid } = useSeriesColors();
  const y = axisFor(model, false);
  return (
    <SeriesFrame pivot={pivot} stat={stat} model={model} strokeOf={strokeOf} dashOf={dashOf} swatch="line">
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={model.data} margin={{ top: 8, right: 14, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={grid} vertical={false} />
          <XAxis dataKey="label" type="category" tick={{ fill: axis, fontSize: 11 }} stroke={axis} minTickGap={8} />
          <YAxis
            domain={y.domain}
            ticks={y.ticks}
            tickFormatter={y.format}
            tick={{ fill: axis, fontSize: 11 }}
            stroke={axis}
            width={62}
          />
          {model.series.map((s, i) => (
            <Line
              key={s.dataKey}
              type="linear"
              dataKey={s.dataKey}
              name={s.label}
              stroke={strokeOf(i)}
              strokeWidth={i === 0 ? 2 : 1.6}
              strokeDasharray={dashOf(i)}
              dot={{ r: 2.5, strokeWidth: 0, fill: strokeOf(i) }}
              activeDot={false}
              // NOT connectNulls: a missing point is a cell under the minimum —
              // bridging it would draw a number the server withheld.
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </SeriesFrame>
  );
}
