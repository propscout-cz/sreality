/* The feed card's photos as a strip (the operator's ask, 2026-10-13): the cover
 * first, then up to seven more of the ad's photos — the 1080 px copies — one 4:3
 * slide per swipe, in the property gallery's idiom (scroll-snap, native lazy
 * images), the stage wash on every photo. The dots sit on the photo in the margin's
 * own translucent paper, so they read as the margin's material rather than a
 * control laid on the picture. An ad with more photos than the strip ends on a
 * plain slide that opens the property page, which holds every photo.
 *
 * PHOTOS LOAD ONE STEP AHEAD OF THE SWIPE: the photo on screen and the next get
 * an <img> (native lazy, so a card far down the roll fetches nothing); the rest
 * stay bare frames until the swipe nears them. Native lazy loading alone fetched
 * the first three photos of every card near the viewport in Chromium, and
 * WebKit's rule inside a scroller is unverified — while the roll is newest first,
 * so each of those is a cold 1080 px copy the API makes on its first request.
 *
 * TAP VS SWIPE. The card's text keeps its stretched link, but the strip is NOT
 * under it: an overlay above a scroller takes the touch and the strip never
 * scrolls. Each slide is its own link to the property instead, out of the tab
 * order (the price link stays the card's one keyboard stop). The browser decides
 * the axis — no script touches the scroll — and a touch that scrolls ends in
 * pointercancel, never a click. The guard covers the rest: a click whose gesture
 * travelled past TAP_SLOP or moved the strip (a mouse drag, a flick an engine
 * still ends in a click) is swallowed before the link sees it. */

import { useRef, useState, type MouseEvent, type PointerEvent, type UIEvent } from 'react';
import { Link } from 'react-router-dom';

import { imageSrc } from '@/lib/imageUrl';
import type { ImagePublic } from '@/lib/types';

import { ChevronIcon } from './phoneIcons';
import StageWash, { type StageWashProps } from './StageWash';

/* A dark room for the photo after dusk, as the prototype dims it (26 %); the
 * pipeline's thumbnails and the property page's gallery take the same. */
export const PHOTO_DARK = '[:root[data-theme=dark]_&]:brightness-[0.74]';

/* How far a press may travel and still be a tap (px). */
const TAP_SLOP = 10;

const SLIDE = 'relative isolate block aspect-[4/3] w-full shrink-0 snap-start snap-always overflow-hidden bg-[var(--color-inset)]';

export interface CardPhotoStripProps {
  /* The property page the strip opens. */
  to: string;
  photos: readonly ImagePublic[];
  /* Every photo the ad has; past `photos.length` the strip ends on "all N". */
  total: number;
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

export default function CardPhotoStrip({ to, photos, total, pending, wash, insetRight }: CardPhotoStripProps) {
  const [at, setAt] = useState(0);
  const [reach, setReach] = useState(0);
  const [broken, setBroken] = useState<ReadonlySet<number>>(() => new Set());
  const raf = useRef(0);
  const guard = useTapGuard();

  const washLayer = wash && <StageWash {...wash} insetRight={insetRight} />;

  if (photos.length === 0) {
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

  const more = total > photos.length;
  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (raf.current) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = 0;
      if (!el.clientWidth) return;
      const i = Math.round(el.scrollLeft / el.clientWidth);
      setAt(i);
      setReach((r) => Math.max(r, i));
    });
  };

  return (
    <div className="relative">
      <div
        role="group"
        aria-label={`Fotky, ${photos.length} z ${total}`}
        onScroll={onScroll}
        {...guard}
        className="flex snap-x snap-mandatory overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {photos.map((img, i) => (
          <Link
            key={img.id}
            to={to}
            tabIndex={-1}
            draggable={false}
            aria-label={`Fotka ${i + 1} z ${total}`}
            className={SLIDE}
          >
            {i > reach + 1 ? null : broken.has(img.id) ? (
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
        ))}
        {more && (
          <Link to={to} tabIndex={-1} draggable={false} className={SLIDE}>
            <span
              className="absolute inset-0 flex items-center justify-center gap-1 font-display text-[17px] text-[var(--color-ink)]"
              style={{ paddingRight: insetRight }}
            >
              Zobrazit všech {total} fotek
              <ChevronIcon className="h-4 w-4 text-[var(--color-ink-3)]" />
            </span>
          </Link>
        )}
      </div>
      {photos.length > 1 && (
        <div
          aria-hidden
          className={`pointer-events-none absolute bottom-4 left-0 flex justify-center transition-opacity duration-150 motion-reduce:transition-none ${
            at < photos.length ? 'opacity-100' : 'opacity-0'
          }`}
          style={{ right: insetRight }}
        >
          <span className="flex h-3.5 items-center gap-1 rounded-full bg-[var(--color-paper-2)]/88 px-1.5">
            {photos.map((img, i) => (
              <i
                key={img.id}
                data-dot={i === at ? 'on' : undefined}
                className={`h-[5px] w-[5px] rounded-full transition-colors duration-150 motion-reduce:transition-none ${
                  i === at ? 'bg-[var(--color-ink)]' : 'bg-[var(--color-ink-4)]'
                }`}
              />
            ))}
          </span>
        </div>
      )}
    </div>
  );
}
