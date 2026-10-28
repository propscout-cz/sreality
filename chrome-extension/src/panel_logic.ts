/* The listing panel's rules, apart from its DOM: when the sheet starts folded, which
 * facts a property type shows, where a chip row cuts to "+N", when the stage step bar
 * gives way to the dropdown, the chart's geometry and the price card's note counts.
 * No runtime import and no `document`, so panel_logic.test.ts runs it under node;
 * formatters are handed in. */

/* Below this viewport width the sheet would cover most of the page: it starts folded. */
export const FOLD_BELOW_PX = 1280;
/* A pipeline of at most this many open stages draws them as a step bar. */
export const STEP_BAR_MAX = 4;
/* Chips shown before the rest fold into "+N". */
export const CHIPS_MAX = 3;

/* `stored`: the folded state the operator last chose (null: never chose). A narrow
 * viewport starts folded whatever was stored; the sheet then remembers what they do. */
export function startsFolded(stored: boolean | null, viewportWidth: number): boolean {
  return viewportWidth < FOLD_BELOW_PX || stored === true;
}

export type FactKey = 'floor' | 'condition' | 'ownership' | 'plot' | 'subtype';

/* The header's fact chips by property type (operator ruling 2026-10-28). */
const FACTS: Record<string, readonly FactKey[]> = {
  byt: ['floor', 'condition', 'ownership'],
  dum: ['plot', 'condition', 'subtype'],
  komercni: ['subtype', 'floor', 'condition'],
  pozemek: ['ownership'],
};

export interface FactRow {
  category_main: string | null;
  floor: number | null;
  total_floors: number | null;
  condition: string | null;
  ownership: string | null;
  property_subtype: string | null;
  plot_area_m2: number | null;
}

export interface FactFormat {
  floor: (floor: number, totalFloors: number | null) => string | null;
  /* An enum column's Czech label. */
  label: (column: 'condition' | 'ownership' | 'subtype', slug: string) => string | null;
  plot: (m2: number) => string;
}

/* Values only, in the type's order; a fact the property lacks is left out, never "—". */
export function factChips(row: FactRow, fmt: FactFormat): string[] {
  const value = (key: FactKey): string | null => {
    switch (key) {
      case 'floor': return row.floor == null ? null : fmt.floor(row.floor, row.total_floors);
      case 'condition': return row.condition ? fmt.label('condition', row.condition) : null;
      case 'ownership': return row.ownership ? fmt.label('ownership', row.ownership) : null;
      case 'subtype': return row.property_subtype ? fmt.label('subtype', row.property_subtype) : null;
      case 'plot': return row.plot_area_m2 == null ? null : fmt.plot(row.plot_area_m2);
    }
  };
  return (FACTS[row.category_main ?? ''] ?? [])
    .map(value)
    .filter((v): v is string => v != null && v !== '');
}

/* A day as the panel prints it: "24. 10. 2026" (a note, "ve fázi od", the chart's since). */
export function fmtDate(at: string | number | null | undefined): string {
  if (at == null) return '';
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('cs-CZ', { day: 'numeric', month: 'numeric', year: 'numeric' });
}

export function cutChips<T>(items: readonly T[], max = CHIPS_MAX): { shown: T[]; more: number } {
  return { shown: items.slice(0, max), more: Math.max(0, items.length - max) };
}

export function stageControl(stages: readonly { is_terminal: boolean }[]): 'bar' | 'dropdown' {
  return stages.filter((s) => !s.is_terminal).length <= STEP_BAR_MAX ? 'bar' : 'dropdown';
}

/* "N portály · M stažený": portals the property was advertised on, and its ads taken down. */
export function portalNote(sources: readonly { source: string; is_active: boolean }[]): {
  portals: number;
  withdrawn: number;
} {
  return {
    portals: new Set(sources.map((s) => s.source)).size,
    withdrawn: sources.filter((s) => !s.is_active).length,
  };
}

/* ---- the price chart ---------------------------------------------------------- */

export interface Point { x: number; y: number }

/* A price track as lib/priceHistory buildPriceSeries returns it. */
export interface SeriesLike {
  id: number;
  points: readonly { t: number; price: number }[];
  endT: number;
}

/* The SVG box and the gutters around the plot (axis labels left and below,
 * the direct labels right). */
export interface ChartFrame {
  width: number;
  height: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface TrackShape {
  id: number;
  /* The step line: held flat between observations, up to the track's end. */
  d: string;
  /* The same line closed down to the baseline (the canonical track's fill). */
  area: string;
  /* Where the price moved. */
  steps: Point[];
  start: Point;
  end: Point;
}

export interface ChartShape {
  x: (t: number) => number;
  y: (v: number) => number;
  tracks: TrackShape[];
  /* One direct label per track: right of the plot for a track running to the end,
   * else just above its start; right-hand labels never overlap. */
  labels: { id: number; x: number; y: number }[];
}

const LABEL_GAP = 12;
const r1 = (v: number): number => Math.round(v * 10) / 10;

export function chartShape(
  series: readonly SeriesLike[], frame: ChartFrame,
  xDomain: readonly [number, number], yDomain: readonly [number, number],
): ChartShape {
  const x0 = frame.left;
  const x1 = frame.width - frame.right;
  const yTop = frame.top;
  const yBase = frame.height - frame.bottom;
  const tSpan = xDomain[1] - xDomain[0] || 1;
  const vSpan = yDomain[1] - yDomain[0] || 1;
  const x = (t: number): number => r1(x0 + ((t - xDomain[0]) / tSpan) * (x1 - x0));
  const y = (v: number): number => r1(yBase - ((v - yDomain[0]) / vSpan) * (yBase - yTop));

  const tracks = series.filter((s) => s.points.length > 0).map((s): TrackShape => {
    const first = s.points[0];
    const start = { x: x(first.t), y: y(first.price) };
    let d = `M${start.x} ${start.y}`;
    let prevY = start.y;
    const steps: Point[] = [];
    for (let i = 1; i < s.points.length; i++) {
      const px = x(s.points[i].t);
      const py = y(s.points[i].price);
      d += `H${px}`;
      if (py !== prevY) {
        d += `V${py}`;
        steps.push({ x: px, y: py });
      }
      prevY = py;
    }
    const end = { x: x(s.endT), y: prevY };
    d += `H${end.x}`;
    return { id: s.id, d, area: `${d}V${yBase}H${start.x}Z`, steps, start, end };
  });

  const right = tracks.filter((t) => t.end.x >= x1 - 0.5)
    .map((t) => ({ id: t.id, x: r1(x1 + 7), y: r1(t.end.y + 4) }))
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < right.length; i++) right[i].y = Math.max(right[i].y, right[i - 1].y + LABEL_GAP);
  const overflow = right.length > 0 ? right[right.length - 1].y - (yBase + 4) : 0;
  if (overflow > 0) for (const l of right) l.y = r1(Math.max(yTop + 8, l.y - overflow));
  const above = tracks.filter((t) => t.end.x < x1 - 0.5).map((t) => ({
    id: t.id,
    x: r1(t.start.x + 4),
    y: r1(t.start.y - 5 < yTop + 8 ? t.start.y + 13 : t.start.y - 5),
  }));
  return { x, y, tracks, labels: [...right, ...above] };
}
