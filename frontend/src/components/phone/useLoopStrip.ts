/* A photo strip that goes round (operator's ask, 2026-11-15): past the last photo
 * comes the first, before the first the last. Shared by the feed card's strip and
 * the property page's swiper.
 *
 * The strip stays a native scroll-snap scroller, so the browser still owns the
 * swipe. Round is done with two extra slides: a copy of the last photo before the
 * first and a copy of the first after the last (`loopSlots`). Once the strip comes
 * to rest on a copy, `settle` moves it, in the same frame and without animation,
 * to the photo the copy stands for; both show the same picture, so nothing is seen.
 *
 * It never moves the strip under a finger (the gesture would lose its origin):
 * a rest reached with the finger still down settles when the finger lifts. The
 * idle timer covers an engine that drops a scroll write made during momentum. */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject, type TouchEvent } from 'react';

const SETTLE_IDLE_MS = 120;

export interface LoopSlot {
  /* The photo this slide shows. */
  index: number;
  /* A copy standing at the far end, not the photo's own slide. */
  copy: boolean;
}

/** The slides in strip order: the photos, between two copies when there is more than one. */
export function loopSlots(count: number): LoopSlot[] {
  const own = Array.from({ length: count }, (_, index) => ({ index, copy: false }));
  if (count < 2) return own;
  return [{ index: count - 1, copy: true }, ...own, { index: 0, copy: true }];
}

export interface LoopStrip {
  ref: RefObject<HTMLDivElement>;
  /* The photo on screen. */
  at: number;
  /* Spread onto the scroller. */
  handlers: {
    onScroll(): void;
    onTouchStart(): void;
    onTouchEnd(e: TouchEvent<HTMLDivElement>): void;
    onTouchCancel(e: TouchEvent<HTMLDivElement>): void;
  };
}

export function useLoopStrip(count: number): LoopStrip {
  const ref = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState(0);
  const touching = useRef(false);
  const raf = useRef(0);
  const idle = useRef<number | undefined>(undefined);
  const loops = count > 1;
  const slides = loops ? count + 2 : count;

  /* One slide's width. scrollWidth over the slide count, not clientWidth: on a
   * fractional-width screen the rounding error would grow with the photo count. */
  const slideWidth = useCallback((): number => {
    const el = ref.current;
    if (!el || slides === 0) return 0;
    return (el.scrollWidth || el.clientWidth * slides) / slides;
  }, [slides]);

  /* Open on the first photo, past the copy before it. */
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && loops) el.scrollLeft = slideWidth();
  }, [loops, slideWidth]);

  useEffect(
    () => () => {
      cancelAnimationFrame(raf.current);
      window.clearTimeout(idle.current);
    },
    [],
  );

  const settle = useCallback((): void => {
    const el = ref.current;
    const w = slideWidth();
    if (!el || !loops || !w || touching.current) return;
    if (el.scrollLeft <= 1) el.scrollLeft = count * w;
    else if (el.scrollLeft >= (count + 1) * w - 1) el.scrollLeft = w;
  }, [count, loops, slideWidth]);

  const onScroll = (): void => {
    window.clearTimeout(idle.current);
    idle.current = window.setTimeout(settle, SETTLE_IDLE_MS);
    if (raf.current) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = 0;
      const el = ref.current;
      const w = slideWidth();
      if (!el || !w) return;
      const slot = Math.round(el.scrollLeft / w);
      setAt(loops ? (slot - 1 + count) % count : Math.min(slot, Math.max(count - 1, 0)));
      settle();
    });
  };

  const lift = (e: TouchEvent<HTMLDivElement>): void => {
    if (e.touches.length > 0) return;
    touching.current = false;
    settle();
  };

  return {
    ref,
    at,
    handlers: {
      onScroll,
      onTouchStart: () => {
        touching.current = true;
      },
      onTouchEnd: lift,
      onTouchCancel: lift,
    },
  };
}
