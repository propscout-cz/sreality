/* The deal-pipeline stage drawn OVER a search-page card's photo, so a property
 * in the pipeline reads from across the grid: the photo toned in the stage's
 * colour (the operator's, copper when none — glyphs.stageAccent) with the
 * stage's badge set large on it and its name beneath. The funnel in the card's
 * cluster stays the control; this is only the mark (aria-hidden — the funnel's
 * label already says the stage). Hovering the card lifts the tone to show the
 * photo.
 *
 * The photo is found by geometry, not markup, like the card itself: the
 * largest image the card shows, cut to the boxes that clip it, each corner
 * rounded like the clipping box whose corner it shares. Re-measured when the
 * card or the photo resizes and when an image in the card loads. No photo
 * found → no mark; the funnel's badge still says the stage.
 *
 * Its host is a second element in the card, beside the cluster's. The host
 * makes no stacking context: the tone layers blend with the photo, and a blend
 * stops at the nearest stacking context. So the layers carry their own
 * z-index, one under the cluster's 900, and paint under it whatever the two
 * hosts' order in the card. */

import tokens from './tokens.css?inline';
import { stageAccent } from './glyphs';

export const STAGE_HOST_CLASS = '__mf_stage_host';
/* Anything smaller is a broker logo or an icon, not the listing's photo. */
const MIN_PHOTO_SIDE = 80;
const FRAME_INSET = 10;

export interface StageMarkView {
  color: string | null;
  badge: string | null;
  label: string | null;
}

export interface StageMark {
  /* null takes the mark down (out of the pipeline, or the card is veiled). */
  show: (view: StageMarkView | null) => void;
  destroy: () => void;
}

interface Placement {
  top: number;
  left: number;
  width: number;
  height: number;
  /* top-left, top-right, bottom-right, bottom-left */
  radii: [number, number, number, number];
  photo: Element;
}

const css = (cardAttr: string): string => `
  ${tokens}
  :host {
    all: initial !important;
    position: absolute !important; inset: 0 !important;
    display: block !important; pointer-events: none !important;
  }
  [hidden] { display: none !important; }
  .box { position: absolute; }
  .tone, .shade, .face {
    position: absolute; inset: 0; z-index: 899; border-radius: inherit;
    transition: opacity 220ms ease;
  }
  .tone { background: var(--stage); mix-blend-mode: color; opacity: 0.9; }
  /* Deepens the photo toward the stage colour so white type reads on a sky. */
  .shade { background: var(--stage); mix-blend-mode: multiply; opacity: 0.5; }
  .face {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    overflow: hidden; container-type: size; color: #fff; text-align: center;
    font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
    text-shadow: 0 1px 18px rgba(0, 0, 0, 0.28);
  }
  .frame {
    position: absolute; inset: ${FRAME_INSET}px;
    border: 1px solid rgba(255, 255, 255, 0.5);
  }
  .badge {
    font-size: clamp(40px, min(50cqh, 40cqw), 168px); font-weight: 300;
    line-height: 0.8; letter-spacing: -0.04em;
    font-variant-numeric: lining-nums tabular-nums; white-space: nowrap;
  }
  .rule {
    width: 28px; height: 1px; flex-shrink: 0; background: rgba(255, 255, 255, 0.75);
    margin: clamp(10px, 5cqh, 20px) 0 clamp(7px, 3cqh, 12px);
  }
  .label {
    max-width: calc(100% - 48px); overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; font-size: clamp(10px, 4cqh, 13px); font-weight: 600;
    line-height: 1.2; letter-spacing: 0.22em; text-transform: uppercase;
  }
  :host-context([${cardAttr}]:hover) .tone { opacity: 0.25; }
  :host-context([${cardAttr}]:hover) .shade { opacity: 0.15; }
  @media (prefers-reduced-motion: reduce) { .tone, .shade, .face { transition: none; } }
`;

type Rect = { top: number; left: number; right: number; bottom: number };

function intersect(a: Rect, b: Rect): Rect {
  return {
    top: Math.max(a.top, b.top), left: Math.max(a.left, b.left),
    right: Math.min(a.right, b.right), bottom: Math.min(a.bottom, b.bottom),
  };
}

const area = (r: Rect): number =>
  Math.max(0, r.right - r.left) * Math.max(0, r.bottom - r.top);

function radius(value: string, w: number, h: number): number {
  const n = parseFloat(value) || 0;
  return value.trim().split(/\s+/)[0].endsWith('%') ? (n / 100) * Math.min(w, h) : n;
}

const near = (a: number, b: number): boolean => Math.abs(a - b) <= 1.5;

/* The largest image the card shows, by its area inside the card — a carousel's
 * off-screen slides sit outside the card and lose. */
function findPhoto(card: HTMLElement, cardRect: Rect): Element | null {
  let best: Element | null = null;
  let bestArea = 0;
  const nodes = card.querySelectorAll('img, video, [style*="background-image"]');
  for (const node of Array.from(nodes)) {
    const r = node.getBoundingClientRect();
    if (r.width < MIN_PHOTO_SIDE || r.height < MIN_PHOTO_SIDE) continue;
    const a = area(intersect(r, cardRect));
    if (a > bestArea) {
      best = node;
      bestArea = a;
    }
  }
  return best;
}

function place(card: HTMLElement, host: HTMLElement): Placement | null {
  const cardRect = card.getBoundingClientRect();
  const photo = findPhoto(card, cardRect);
  if (photo == null) return null;

  // What it shows: the photo cut to every box between it and the card that clips.
  let shown: Rect = photo.getBoundingClientRect();
  const clips: Element[] = [photo];
  for (let e = photo.parentElement; e != null; e = e.parentElement) {
    const s = getComputedStyle(e);
    if (s.overflowX !== 'visible' || s.overflowY !== 'visible') {
      shown = intersect(shown, e.getBoundingClientRect());
      clips.push(e);
    }
    if (e === card) break;
  }
  const width = shown.right - shown.left;
  const height = shown.bottom - shown.top;
  if (width < MIN_PHOTO_SIDE || height < MIN_PHOTO_SIDE) return null;

  /* A corner is rounded by whichever clipping box shares it: the image's own
   * radius, its frame's, or the card's (whose bottom corners sit below the
   * photo and so round nothing here). */
  const radii: Placement['radii'] = [0, 0, 0, 0];
  for (const e of clips) {
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    const corners: [boolean, string][] = [
      [near(r.top, shown.top) && near(r.left, shown.left), s.borderTopLeftRadius],
      [near(r.top, shown.top) && near(r.right, shown.right), s.borderTopRightRadius],
      [near(r.bottom, shown.bottom) && near(r.right, shown.right), s.borderBottomRightRadius],
      [near(r.bottom, shown.bottom) && near(r.left, shown.left), s.borderBottomLeftRadius],
    ];
    corners.forEach(([shared, value], i) => {
      if (shared) radii[i] = Math.max(radii[i], radius(value, r.width, r.height));
    });
  }

  const origin = host.getBoundingClientRect();
  return {
    top: shown.top - origin.top, left: shown.left - origin.left,
    width, height, radii, photo,
  };
}

const px = (radii: number[]): string => radii.map((r) => `${Math.round(r * 10) / 10}px`).join(' ');

export function createStageMark(card: HTMLElement, cardAttr: string): StageMark {
  let host: HTMLElement | null = null;
  let parts: {
    box: HTMLElement; frame: HTMLElement; badge: HTMLElement; rule: HTMLElement; label: HTMLElement;
  } | null = null;
  let view: StageMarkView | null = null;
  let shown = '';
  let watched: Element | null = null;
  let frame: number | null = null;
  const resizes = new ResizeObserver(() => schedule());
  const onLoad = (): void => schedule();

  function build(): NonNullable<typeof parts> {
    host = document.createElement('div');
    host.className = STAGE_HOST_CLASS;
    const shadow = host.attachShadow({ mode: 'closed' });
    const style = document.createElement('style');
    style.textContent = css(cardAttr);
    const div = (className: string): HTMLElement => {
      const node = document.createElement('div');
      node.className = className;
      return node;
    };
    const box = div('box');
    box.setAttribute('aria-hidden', 'true');
    box.hidden = true;
    const face = div('face');
    const built = {
      box, frame: div('frame'), badge: div('badge'), rule: div('rule'), label: div('label'),
    };
    face.append(built.frame, built.badge, built.rule, built.label);
    box.append(div('tone'), div('shade'), face);
    shadow.append(style, box);
    card.appendChild(host);
    resizes.observe(card);
    // `load` doesn't bubble; capture catches every lazy image the card fills in.
    card.addEventListener('load', onLoad, true);
    return built;
  }

  function schedule(): void {
    if (frame != null || view == null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      measure();
    });
  }

  function measure(): void {
    if (host == null || parts == null || view == null || !card.isConnected) return;
    const at = place(card, host);
    if (at?.photo !== watched) {
      if (watched != null) resizes.unobserve(watched);
      watched = at?.photo ?? null;
      if (watched != null) resizes.observe(watched);
    }
    const key = at == null
      ? ''
      : [at.top, at.left, at.width, at.height, ...at.radii].map(Math.round).join(',');
    if (key === shown) return;
    shown = key;
    const { box, frame: rim } = parts;
    box.hidden = at == null;
    if (at == null) return;
    box.style.top = `${at.top}px`;
    box.style.left = `${at.left}px`;
    box.style.width = `${at.width}px`;
    box.style.height = `${at.height}px`;
    box.style.borderRadius = px(at.radii);
    rim.style.borderRadius = px(at.radii.map((r) => Math.max(0, r - FRAME_INSET)));
  }

  function show(next: StageMarkView | null): void {
    if (JSON.stringify(next) === JSON.stringify(view)) return;
    view = next;
    if (next == null) {
      if (parts != null) parts.box.hidden = true;
      shown = '';
      return;
    }
    parts ??= build();
    const { box, badge, rule, label } = parts;
    box.style.setProperty('--stage', stageAccent(next.color).fg);
    badge.textContent = next.badge ?? '';
    badge.hidden = next.badge == null;
    label.textContent = next.label ?? '';
    label.hidden = next.label == null;
    rule.hidden = next.badge == null || next.label == null;
    shown = '';
    measure();
  }

  function destroy(): void {
    view = null;
    if (frame != null) cancelAnimationFrame(frame);
    resizes.disconnect();
    card.removeEventListener('load', onLoad, true);
    host?.remove();
    host = null;
    parts = null;
  }

  return { show, destroy };
}
