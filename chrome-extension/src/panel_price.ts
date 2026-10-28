/* "Vývoj ceny": one step line per ad of the property, the canonical ad first in
 * the accent, the others in ink, an ad taken down dashed to a hollow end; each
 * line labelled where it ends. The series is the app's (lib/priceHistory), the
 * axes are the app's (lib/chartAxis); this file only draws them. */

import { timeAxisSpec, valueAxisSpec } from '../../frontend/src/lib/chartAxis';
import { czPlural, fmtCzk } from '../../frontend/src/lib/format';
import { buildPriceSeries, lastMoveByTrack, priceChangeEvents, type PriceSeries } from '../../frontend/src/lib/priceHistory';
import { buildCard } from './card';
import { stateLine } from './checklist';
import { h } from './dom';
import { chartShape, fmtDate, portalNote, type ChartFrame } from './panel_logic';
import type { PortalListing, PriceHistory } from './types';

const NS = 'http://www.w3.org/2000/svg';
const WIDTH = 312;
const HEIGHT = 116;
/* The mono axis figures and the sans direct labels, per character (11 px). */
const MONO_CH = 6.7;
const SANS_CH = 5.8;
const RIGHT = 84;

export type HistoryView = { status: 'loading' | 'failed' } | { status: 'ok'; data: PriceHistory };

/* The property's ads as tracks, the canonical ad first (it owns the accent). */
export function priceSeries(l: PortalListing, data: PriceHistory, nowMs: number): PriceSeries[] {
  const canonical = l.canonical_listing_id;
  const ads = [
    ...data.sources.filter((s) => s.id === canonical),
    ...data.sources.filter((s) => s.id !== canonical),
  ];
  return buildPriceSeries(ads, data.snapshots, nowMs);
}

/* This ad's own last move, never another ad's: the header's pill. */
export function adMove(series: PriceSeries[] | null, listingId: number | null): number | null {
  if (series == null || listingId == null) return null;
  return lastMoveByTrack(series).get(listingId) ?? null;
}

function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>): SVGElementTagNameMap[K] {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function text(x: number, y: number, cls: string, value: string): SVGTextElement {
  const t = svg('text', { x, y, class: cls });
  t.textContent = value;
  return t;
}

function chart(series: PriceSeries[], live: ReadonlySet<number>): SVGSVGElement {
  const prices = series.flatMap((s) => s.points.map((p) => p.price));
  const yAxis = valueAxisSpec([Math.min(...prices), Math.max(...prices)], { targetTicks: 3 });
  const t0 = Math.min(...series.map((s) => s.points[0].t));
  const t1 = Math.max(...series.map((s) => s.endT));
  const xAxis = timeAxisSpec([t0, t1], { targetTicks: 4 });
  const yLabels = yAxis.ticks.map((v) => yAxis.format(v));
  const frame: ChartFrame = {
    width: WIDTH, height: HEIGHT, top: 10, bottom: 20, right: RIGHT,
    left: Math.ceil(8 + Math.max(...yLabels.map((l) => l.length)) * MONO_CH),
  };
  const shape = chartShape(series, frame, [t0, t1], yAxis.domain);
  const base = HEIGHT - frame.bottom;

  const root = svg('svg', { viewBox: `0 0 ${WIDTH} ${HEIGHT}`, role: 'img', class: 'chart' });
  root.setAttribute('aria-label', 'Cena podle inzerátu: ' + series.map((s) =>
    `${s.label} ${fmtCzk(s.points[s.points.length - 1].price)}${live.has(s.id) ? '' : ' (stažený)'}`).join(', '));
  const canonical = shape.tracks[0];
  if (canonical != null) root.appendChild(svg('path', { d: canonical.area, class: 'area' }));
  yAxis.ticks.forEach((v, i) => {
    const y = shape.y(v);
    if (y < base - 0.5) root.appendChild(svg('line', { x1: frame.left, x2: WIDTH - RIGHT, y1: y, y2: y, class: 'gl' }));
    root.appendChild(text(frame.left - 6, y + 4, 'ax axe', yLabels[i]));
  });
  root.appendChild(svg('line', { x1: frame.left, x2: WIDTH - RIGHT, y1: base, y2: base, class: 'base' }));
  for (const t of xAxis.ticks) root.appendChild(text(shape.x(t), HEIGHT - 5, 'ax axm', xAxis.formatTick(t)));

  // The canonical track last, on top of the rest.
  const order = [...shape.tracks.slice(1), ...shape.tracks.slice(0, 1)];
  for (const track of order) {
    const first = track === canonical;
    const gone = !live.has(track.id);
    const tone = first ? 1 : gone ? 3 : 2;
    root.appendChild(svg('path', { d: track.d, class: `s${tone}${first && gone ? ' gone' : ''}` }));
    if (first) for (const p of track.steps) root.appendChild(svg('circle', { cx: p.x, cy: p.y, r: 3, class: 'd1s' }));
    root.appendChild(svg('circle', {
      cx: track.end.x, cy: track.end.y, r: first && !gone ? 4.5 : 2.5, class: gone ? 'dend' : `d${tone}`,
    }));
    const at = shape.labels.find((l) => l.id === track.id);
    const label = series.find((s) => s.id === track.id)?.label ?? '';
    if (at != null) {
      const room = Math.floor((at.x >= WIDTH - RIGHT ? RIGHT - 8 : WIDTH - at.x) / SANS_CH);
      const shown = label.length > room ? `${label.slice(0, Math.max(1, room - 1))}…` : label;
      const node = text(at.x, at.y, `lab lab${tone}`, shown);
      if (shown !== label) node.appendChild(svg('title', {})).textContent = label;
      root.appendChild(node);
    }
  }
  return root;
}

export function priceCard(history: HistoryView, series: PriceSeries[] | null, retry: () => void): HTMLElement {
  const note = h('span', 'card-note');
  const card = buildCard({ title: 'Vývoj ceny', actions: [note] });
  if (history.status === 'loading') {
    const sk = h('span', 'sk');
    sk.style.height = `${HEIGHT}px`;
    card.body.appendChild(sk);
    return card.section;
  }
  if (history.status !== 'ok' || series == null) {
    const line = stateLine('Vývoj ceny se nepodařilo načíst.', retry);
    line.className = 'err';
    card.body.appendChild(line);
    return card.section;
  }
  const { portals, withdrawn } = portalNote(history.data.sources);
  note.textContent = `${portals} ${czPlural(portals, 'portál', 'portály', 'portálů')}`
    + (withdrawn > 0 ? ` · ${withdrawn} ${czPlural(withdrawn, 'stažený', 'stažené', 'stažených')}` : '');
  if (series.length === 0) {
    card.body.appendChild(h('p', 'meta', 'Cena na vyžádání'));
    return card.section;
  }
  const live = new Set(history.data.sources.filter((s) => s.is_active).map((s) => s.id));
  card.body.appendChild(chart(series, live));
  if (priceChangeEvents(series).length === 0) {
    const since = Math.min(...series.map((s) => s.points[0].t));
    card.body.appendChild(h('p', 'meta', `Cena se od ${fmtDate(since)} nezměnila.`));
  }
  return card.section;
}
