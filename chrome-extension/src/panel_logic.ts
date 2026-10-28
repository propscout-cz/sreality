/* The listing panel's rules, apart from its DOM: when the sheet starts folded, which
 * facts a property type shows, what the folded tab marks, where a chip row cuts to
 * "+N", when the stage step bar gives way to the dropdown, the chart's geometry and
 * the price card's note counts. No runtime import and no `document`, so
 * panel_logic.test.ts runs it under node; formatters are handed in. */

import type { PriceSeries } from '../../frontend/src/lib/priceHistory';
import type { PortalListing } from './types';

/* Below this viewport width the sheet would cover most of the page: it starts folded. */
export const FOLD_BELOW_PX = 1280;
/* A pipeline of at most this many open stages draws them as a step bar. */
export const STEP_BAR_MAX = 4;
/* Chips shown before the rest fold into "+N". */
export const CHIPS_MAX = 3;

/* Where the fold is kept (chrome.storage.local), and the old floating panel's
 * "minimized" choice, read once as the first fold state and then removed. */
export const FOLDED_KEY = 'panelFolded';
export const LEGACY_FOLD_KEY = 'panelMinimized';

/* The fold the operator last chose, from what storage holds; null: never chose. */
export function storedFold(r: Record<string, unknown>): boolean | null {
  const folded = r[FOLDED_KEY];
  if (typeof folded === 'boolean') return folded;
  return r[LEGACY_FOLD_KEY] === true ? true : null;
}

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

export type FactRow = Pick<
  PortalListing, 'category_main' | 'floor' | 'total_floors' | 'condition' | 'ownership' | 'property_subtype' | 'plot_area_m2'
>;

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

/* The folded tab's marks after the PS mark, each only when there: the deal's stage,
 * this ad's price DROP (a rise is no news on a tab), MF. */
export function tabMarkList<N>(stage: N | null, move: number | null, mf: N | null, drop: (pct: number) => N): N[] {
  return [stage, move != null && move < 0 ? drop(move) : null, mf].filter((n): n is N => n != null);
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

/* The SVG box and the gutters around the plot (axis labels left and below,
 * the direct labels right), and the direct labels' width per character. */
export interface ChartFrame {
  width: number;
  height: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
  labelCh: number;
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
   * else just above its start, or below it, where that crosses no line or label,
   * else right of the plot too; right-hand labels never overlap. */
  labels: { id: number; x: number; y: number }[];
}

const LABEL_GAP = 12;
const r1 = (v: number): number => Math.round(v * 10) / 10;

interface Box { x0: number; x1: number; y0: number; y1: number }

export function chartShape(
  series: readonly PriceSeries[], frame: ChartFrame,
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

  /* Everything drawn in the plot, as boxes a label must not cross: the baseline,
   * every track's runs and rises, then each label placed inside it. */
  const ink: Box[] = [{ x0, x1, y0: yBase, y1: yBase }];
  const line = (ax: number, ay: number, bx: number, by: number): void => {
    ink.push({ x0: Math.min(ax, bx), x1: Math.max(ax, bx), y0: Math.min(ay, by), y1: Math.max(ay, by) });
  };
  const tracks = series.filter((s) => s.points.length > 0).map((s): TrackShape => {
    const first = s.points[0];
    const start = { x: x(first.t), y: y(first.price) };
    let d = `M${start.x} ${start.y}`;
    let prevX = start.x;
    let prevY = start.y;
    const steps: Point[] = [];
    for (let i = 1; i < s.points.length; i++) {
      const px = x(s.points[i].t);
      const py = y(s.points[i].price);
      d += `H${px}`;
      line(prevX, prevY, px, prevY);
      if (py !== prevY) {
        d += `V${py}`;
        line(px, prevY, px, py);
        steps.push({ x: px, y: py });
      }
      prevX = px;
      prevY = py;
    }
    const end = { x: x(s.endT), y: prevY };
    d += `H${end.x}`;
    line(prevX, prevY, end.x, prevY);
    return { id: s.id, d, area: `${d}V${yBase}H${start.x}Z`, steps, start, end };
  });

  /* A label's box (11 px text on its baseline, 1 px clear), clipped at the SVG's edge. */
  const labelBox = (id: number, lx: number, ly: number): Box => {
    const chars = series.find((s) => s.id === id)?.label.length ?? 0;
    return { x0: lx, x1: Math.min(lx + chars * frame.labelCh, frame.width), y0: ly - 11, y1: ly + 4 };
  };
  const clear = (b: Box): boolean => !ink.some((o) => o.x1 >= b.x0 && o.x0 <= b.x1 && o.y1 >= b.y0 && o.y0 <= b.y1);

  const right: { id: number; x: number; y: number }[] = [];
  const inPlot: { id: number; x: number; y: number }[] = [];
  for (const t of tracks) {
    const lx = r1(t.start.x + 4);
    const spot = t.end.x >= x1 - 0.5 ? undefined : [r1(t.start.y - 5), r1(t.start.y + 13)]
      .filter((ly) => ly >= yTop + 8)
      .map((ly) => ({ ly, box: labelBox(t.id, lx, ly) }))
      .find((c) => clear(c.box));
    if (spot == null) {
      right.push({ id: t.id, x: r1(x1 + 7), y: r1(t.end.y + 4) });
    } else {
      ink.push(spot.box);
      inPlot.push({ id: t.id, x: lx, y: spot.ly });
    }
  }
  right.sort((a, b) => a.y - b.y);
  for (let i = 1; i < right.length; i++) right[i].y = r1(Math.max(right[i].y, right[i - 1].y + LABEL_GAP));
  const overflow = right.length > 0 ? right[right.length - 1].y - (yBase + 4) : 0;
  if (overflow > 0) for (const l of right) l.y = r1(Math.max(yTop + 8, l.y - overflow));
  return { x, y, tracks, labels: [...right, ...inPlot] };
}
