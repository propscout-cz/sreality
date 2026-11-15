/* The feed card's photos as a strip (the operator's ask, 2026-10-13): the cover
 * first, then every other photo the card's read holds, one 4:3 slide per swipe —
 * the 1080 px copies — in the property gallery's idiom (scroll-snap, native lazy
 * images), the stage wash on every photo. The strip goes round (operator's ask,
 * 2026-11-15; ./useLoopStrip): past the last photo comes the cover, before the
 * cover the last. The position sits on the photo in the margin's own translucent
 * paper — dots up to STRIP_MAX_DOTS photos, a counter past that — so it reads as
 * the margin's material rather than a control laid on the picture.
 *
 * PHOTOS LOAD ONE STEP AHEAD OF THE SWIPE: the photo on screen and the next get
 * an <img> (native lazy, so a card far down the roll fetches nothing); the rest
 * stay bare frames until the swipe nears them. The photo BEFORE the one on screen
 * (the last, on a fresh card) loads only once a finger has come down on the strip,
 * so a card merely scrolled past never fetches it. Native lazy loading alone
 * fetched the first three photos of every card near the viewport in Chromium, and
 * WebKit's rule inside a scroller is unverified — while the roll is newest first,
 * so each of those is a cold 1080 px copy the API makes on its first request.
 *
 * TAP VS SWIPE. The card's text keeps its stretched link, but the strip is NOT
 * under it: an overlay above a scroller takes the touch and the strip never
 * scrolls. Each slide is its own link to the property instead, out of the tab
 * order (the price link stays the card's one keyboard stop). The browser decides
 * the axis — no script touches the scroll under a finger — and a touch that
 * scrolls ends in pointercancel, never a click. The guard covers the rest: a click
 * whose gesture travelled past TAP_SLOP or moved the strip (a mouse drag, a flick
 * an engine still ends in a click) is swallowed before the link sees it. */

import { useRef, useState, type MouseEvent, type PointerEvent } from 'react';
import { Link } from 'react-router-dom';

import { imageSrc } from '@/lib/imageUrl';
import type { ImagePublic } from '@/lib/types';

import StageWash, { type StageWashProps } from './StageWash';
import { loopSlots, useLoopStrip } from './useLoopStrip';

/* A dark room for the photo after dusk, as the prototype dims it (26 %); the
 * pipeline's thumbnails and the property page's gallery take the same. */
export const PHOTO_DARK = '[:root[data-theme=dark]_&]:brightness-[0.74]';

/* More dots than this stop reading as positions; a counter takes over. */
const STRIP_MAX_DOTS = 8;

/* How far a press may travel and still be a tap (px). */
const TAP_SLOP = 10;

const SLIDE = 'relative isolate block aspect-[4/3] w-full shrink-0 snap-start snap-always overflow-hidden bg-[var(--color-inset)]';

export interface CardPhotoStripProps {
  /* The property page the strip opens. */
  to: string;
  photos: readonly ImagePublic[];
  /* The first page of photos is still in flight. */
  pending: boolean;
  /* The stage wash of a filed property, laid on every photo. */
  wash: Omit<StageWashProps, 'insetRight' | 'size'> | null;
  /* The column laid over the right edge (the margin marks), px. */
  insetRight: number;
}

export function useTapGuard() {
  const start = useRef<{ x: number; y: number; left: number } | null>(null);
  return {
    onPointerDown: (e: PointerEvent<HTMLElement>) => {
      start.current = { x: e.clientX, y: e.clientY, left: e.currentTarget.scrollLeft };
    },
    onClickCapture: (e: MouseEvent<HTMLElement>) => {
      const s = start.current;
      start.current = null;
      if (s == null) return;
      const moved =
        Math.abs(e.clientX - s.x) > TAP_SLOP ||
        Math.abs(e.clientY - s.y) > TAP_SLOP ||
        Math.abs(e.currentTarget.scrollLeft - s.left) > 1;
      if (moved) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
  };
}

export default function CardPhotoStrip({ to, photos, pending, wash, insetRight }: CardPhotoStripProps) {
  const count = photos.length;
  const { ref, at, handlers } = useLoopStrip(count);
  /* The photos that have an <img>: grown as the swipe nears them, never shrunk. */
  const [near, setNear] = useState<ReadonlySet<number>>(() => new Set([0, 1]));
  const [touched, setTouched] = useState(false);
  const [broken, setBroken] = useState<ReadonlySet<number>>(() => new Set());
  const guard = useTapGuard();

  const washLayer = wash && <StageWash {...wash} insetRight={insetRight} />;

  if (count === 0) {
    return (
      <div className={SLIDE}>
        {!pending && (
          <span
            className="absolute inset-0 flex items-center justify-center text-[12px] text-[var(--color-ink-4)]"
            style={{ paddingRight: insetRight }}
          >
            Bez fotky
          </span>
        )}
        {washLayer}
      </div>
    );
  }

  const want = [at, (at + 1) % count, ...(touched ? [(at - 1 + count) % count] : [])];
  if (want.some((i) => !near.has(i))) setNear(new Set([...near, ...want]));
  /* A copy stands beside the first or the last photo and is only ever seen from there. */
  const atAnEnd = at === 0 || at === count - 1;

  return (
    <div className="relative">
      <div
        ref={ref}
        role="group"
        aria-label={`Fotky, ${count}`}
        {...handlers}
        onPointerDown={(e) => {
          guard.onPointerDown(e);
          setTouched(true);
        }}
        onClickCapture={guard.onClickCapture}
        className="flex snap-x snap-mandatory overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {loopSlots(count).map(({ index, copy }) => {
          const img = photos[index];
          return (
            <Link
              key={copy ? `copy-${img.id}` : img.id}
              to={to}
              tabIndex={-1}
              draggable={false}
              aria-hidden={copy || undefined}
              aria-label={copy ? undefined : `Fotka ${index + 1} z ${count}`}
              className={SLIDE}
            >
              {!near.has(index) || (copy && !atAnEnd) ? null : broken.has(img.id) ? (
                <span
                  className="absolute inset-0 flex items-center justify-center text-[12px] text-[var(--color-ink-4)]"
                  style={{ paddingRight: insetRight }}
                >
                  Fotka nedostupná
                </span>
              ) : (
                <img
                  src={imageSrc(img, { w: 1080 })}
                  alt=""
                  loading="lazy"
                  decoding="async"
                  draggable={false}
                  onError={() => setBroken((prev) => new Set(prev).add(img.id))}
                  className={`block h-full w-full object-cover ${PHOTO_DARK}`}
                />
              )}
              {washLayer}
            </Link>
          );
        })}
      </div>
      {count > 1 && (
        <div
          aria-hidden
          className="pointer-events-none absolute bottom-4 left-0 flex justify-center"
          style={{ right: insetRight }}
        >
          <span className="flex h-3.5 items-center gap-1 rounded-full bg-[var(--color-paper-2)]/88 px-1.5">
            {count <= STRIP_MAX_DOTS ? (
              photos.map((img, i) => (
                <i
                  key={img.id}
                  data-dot={i === at ? 'on' : undefined}
                  className={`h-[5px] w-[5px] rounded-full transition-colors duration-150 motion-reduce:transition-none ${
                    i === at ? 'bg-[var(--color-ink)]' : 'bg-[var(--color-ink-4)]'
                  }`}
                />
              ))
            ) : (
              <span data-counter className="font-mono text-[10px] leading-none tabular-nums text-[var(--color-ink)]">
                {at + 1} / {count}
              </span>
            )}
          </span>
        </div>
      )}
    </div>
  );
}
