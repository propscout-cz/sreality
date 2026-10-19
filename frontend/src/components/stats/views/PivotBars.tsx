import { Bar, BarChart, CartesianGrid, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import type { ChartStat } from '@/lib/statsRegistry.generated';
import { seriesModel, type StatsPivot } from '@/lib/stats';
import { SeriesFrame, axisFor, useSeriesColors } from './PivotLines';

/* The line view's twin as bars: one row dimension on x, the column dimension as
 * grouped series. The axis starts at zero — a bar's length is its value. */
export default function PivotBars({ pivot, stat }: { pivot: StatsPivot; stat: ChartStat }) {
  const model = seriesModel(pivot, stat);
  const { strokeOf, dashOf, axis, grid } = useSeriesColors();
  const y = axisFor(model, true);
  return (
    <SeriesFrame pivot={pivot} stat={stat} model={model} strokeOf={strokeOf} dashOf={dashOf} swatch="block">
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={model.data} margin={{ top: 8, right: 14, bottom: 0, left: 0 }} barCategoryGap="18%">
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
            <Bar
              key={s.dataKey}
              dataKey={s.dataKey}
              name={s.label}
              fill={strokeOf(i)}
              fillOpacity={dashOf(i) ? 0.55 : 1}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </SeriesFrame>
  );
}
