/* The operator's state drawn OVER a search-page card's photo, so it reads from
 * across the grid:
 *   pipeline   — the photo toned in the stage's colour (the operator's, copper
 *                when none — glyphs.stageAccent), the stage's badge and name
 *                set over it. Hovering the card lifts the tone to show the photo.
 *   collection — toned the same way in copper (the extension's "saved"
 *                colour), a bookmark and the collection's name (+N for more)
 *                where a stage sets its badge: a property already sorted must
 *                not shine on the page like a new one. In the pipeline too, the
 *                stage tones the photo and a copper label in the top-right
 *                corner names the collection.
 *   tags       — the app's tag chips (the name in the tag's colour), in the
 *                same corner, the bottom being where portals put their own
 *                broker logos and video marks.
 * A property can carry all three. The card's cluster stays the control; this
 * is only the mark (aria-hidden — the cluster's labels say it; tags are edited
 * in the app).
 *
 * The photo is found by geometry, not markup, like the card itself: the
 * largest image the card shows, cut to the boxes that clip it, each corner
 * rounded like the clipping box whose corner it shares. Re-measured when the
 * card or the photo resizes and when an image in the card loads. No photo
 * found → no mark; the cluster still says it all.
 *
 * Its host is a second element in the card, beside the cluster's. The host
 * makes no stacking context: the tone layers blend with the photo, and a blend
 * stops at the nearest stacking context. So the layers carry their own
 * z-index, one under the cluster's 900, and paint under it whatever the two
 * hosts' order in the card. */

import tokens from './tokens.css?inline';
import { bookmarkIconSvg, stageAccent } from './glyphs';

export const PHOTO_MARK_HOST_CLASS = '__mf_photo_mark_host';
/* Anything smaller is a broker logo or an icon, not the listing's photo. */
const MIN_PHOTO_SIDE = 80;
const FRAME_INSET = 10;
/* More chips than this fold into one "+N" — the corner must not bury the photo. */
const MAX_CHIPS = 4;

export interface PhotoMarkView {
  /* The deal's stage; null out of the pipeline. */
  stage: { color: string | null; badge: string | null; label: string | null } | null;
  /* The names of MY collections the property sits in, checklist order; null
   * when it is in none of mine. */
  collections: string[] | null;
  /* The property's tags, by name; empty when it has none. */
  tags: { name: string; color: string }[];
}

export interface PhotoMark {
  /* null takes the mark down (nothing to say, or the card is veiled). */
  show: (view: PhotoMarkView | null) => void;
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
  .box { position: absolute; font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif; }
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
    text-shadow: 0 1px 12px rgba(0, 0, 0, 0.3);
  }
  .frame {
    position: absolute; inset: ${FRAME_INSET}px;
    border: 1px solid rgba(255, 255, 255, 0.45);
  }
  .badge {
    font-size: clamp(22px, min(20cqh, 16cqw), 64px); font-weight: 300;
    line-height: 0.85; letter-spacing: -0.02em; opacity: 0.9;
    font-variant-numeric: lining-nums tabular-nums; white-space: nowrap;
  }
  .rule {
    width: 20px; height: 1px; flex-shrink: 0; background: rgba(255, 255, 255, 0.6);
    margin: clamp(6px, 3cqh, 10px) 0 clamp(5px, 2cqh, 8px);
  }
  .label {
    max-width: calc(100% - 48px); overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; font-size: clamp(9px, 3.4cqh, 11px); font-weight: 600;
    line-height: 1.2; letter-spacing: 0.2em; text-transform: uppercase; opacity: 0.9;
  }
  .badge svg { display: block; width: clamp(18px, min(15cqh, 12cqw), 44px); height: auto; }
  .corner {
    position: absolute; top: 6px; right: 6px; z-index: 899;
    display: flex; flex-direction: column; align-items: flex-end; gap: 4px;
    max-width: 55%;
  }
  .saved {
    box-sizing: border-box; display: inline-flex; align-items: center; gap: 4px;
    max-width: 100%; height: 24px; padding: 0 8px 0 6px;
    font-size: 11px; font-weight: 600; line-height: 1; letter-spacing: 0.02em;
    color: #fff; background: var(--copper); white-space: nowrap;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
  }
  .saved svg { width: 13px; height: 13px; flex-shrink: 0; }
  .saved-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .chips { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 3px; max-width: 100%; }
  /* The app's TagChip on a photo: its pale wash would vanish there, so paper. */
  .chip {
    box-sizing: border-box; max-width: 100%; height: 20px; padding: 0 6px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: 11px; font-weight: 600; line-height: 18px; color: var(--chip);
    background: rgba(255, 253, 248, 0.94); border: 1px solid var(--chip); border-radius: 2px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.14);
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

/* The label's words: the first collection's name, "+N" for the rest. */
function savedText(names: string[]): string {
  return names.length > 1 ? `${names[0]} +${names.length - 1}` : names[0] ?? '';
}

export function createPhotoMark(card: HTMLElement, cardAttr: string): PhotoMark {
  let host: HTMLElement | null = null;
  let parts: {
    box: HTMLElement; stage: HTMLElement[]; frame: HTMLElement; badge: HTMLElement;
    rule: HTMLElement; label: HTMLElement; saved: HTMLElement;
    savedName: HTMLElement; chips: HTMLElement;
  } | null = null;
  let view: PhotoMarkView | null = null;
  let shown = '';
  let watched: Element | null = null;
  let frame: number | null = null;
  const resizes = new ResizeObserver(() => schedule());
  const onLoad = (): void => schedule();

  function build(): NonNullable<typeof parts> {
    host = document.createElement('div');
    host.className = PHOTO_MARK_HOST_CLASS;
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
    const corner = div('corner');
    const saved = div('saved');
    const built = {
      box, stage: [div('tone'), div('shade'), face], frame: div('frame'), badge: div('badge'),
      rule: div('rule'), label: div('label'), saved,
      savedName: div('saved-name'), chips: div('chips'),
    };
    face.append(built.frame, built.badge, built.rule, built.label);
    saved.innerHTML = bookmarkIconSvg(true);
    saved.append(built.savedName);
    corner.append(saved, built.chips);
    box.append(...built.stage, corner);
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
    const { box, frame: hairline } = parts;
    box.hidden = at == null;
    if (at == null) return;
    box.style.top = `${at.top}px`;
    box.style.left = `${at.left}px`;
    box.style.width = `${at.width}px`;
    box.style.height = `${at.height}px`;
    box.style.borderRadius = px(at.radii);
    hairline.style.borderRadius = px(at.radii.map((r) => Math.max(0, r - FRAME_INSET)));
  }

  function show(next: PhotoMarkView | null): void {
    if (JSON.stringify(next) === JSON.stringify(view)) return;
    view = next;
    if (next == null) {
      if (parts != null) parts.box.hidden = true;
      shown = '';
      return;
    }
    parts ??= build();
    const { box, stage, badge, rule, label, saved, savedName, chips } = parts;
    const s = next.stage;
    const names = next.collections;
    // The face says the stage; without one, the collection.
    const face = s != null
      ? { color: stageAccent(s.color).fg, glyph: false, badge: s.badge, label: s.label }
      : names != null
        ? { color: 'var(--copper)', glyph: true, badge: '', label: savedText(names) }
        : null;
    for (const layer of stage) layer.hidden = face == null;
    if (face != null) {
      box.style.setProperty('--stage', face.color);
      if (face.glyph) badge.innerHTML = bookmarkIconSvg(true);
      else badge.textContent = face.badge ?? '';
      badge.hidden = face.badge == null;
      label.textContent = face.label ?? '';
      label.hidden = face.label == null;
      rule.hidden = face.badge == null || face.label == null;
    }
    // The corner label only when the face is the stage's — else it repeats the face.
    saved.hidden = names == null || s == null;
    const text = savedText(names ?? []);
    savedName.textContent = text;
    savedName.hidden = text === '';
    const shownTags = next.tags.length > MAX_CHIPS ? next.tags.slice(0, MAX_CHIPS - 1) : next.tags;
    chips.replaceChildren(...shownTags.map((t) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      // The tag palette IS the stage palette (TagColor), so its accent resolves alike.
      chip.style.setProperty('--chip', stageAccent(t.color).fg);
      chip.textContent = t.name;
      return chip;
    }));
    if (shownTags.length < next.tags.length) {
      const more = document.createElement('span');
      more.className = 'chip';
      more.style.setProperty('--chip', 'var(--ink-3)');
      more.textContent = `+${next.tags.length - shownTags.length}`;
      chips.append(more);
    }
    chips.hidden = next.tags.length === 0;
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
