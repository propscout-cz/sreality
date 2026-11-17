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
  /* The slide this slot shows. */
  index: number;
  /* A copy standing at the far end, not the slide's own slot. */
  copy: boolean;
}

/* The slots in strip order: the slides, between two copies when there is more than one. */
export function loopSlots(slideCount: number): LoopSlot[] {
  const own = Array.from({ length: slideCount }, (_, index) => ({ index, copy: false }));
  if (slideCount < 2) return own;
  return [{ index: slideCount - 1, copy: true }, ...own, { index: 0, copy: true }];
}

export interface LoopStrip {
  ref: RefObject<HTMLDivElement>;
  /* The slide on screen, the one a swipe on would show and the one a swipe back would. */
  at: number;
  next: number;
  prev: number;
  /* On the first or the last slide: the only places a copy can be seen from. */
  atAnEnd: boolean;
  /* The strip has been used: a finger came down on it, or it left where it opened. */
  touched: boolean;
  handlers: {
    onScroll(): void;
    onTouchStart(): void;
    onTouchEnd(e: TouchEvent<HTMLDivElement>): void;
    onTouchCancel(e: TouchEvent<HTMLDivElement>): void;
  };
}

export function useLoopStrip(slideCount: number): LoopStrip {
  const ref = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState(0);
  const [touched, setTouched] = useState(false);
  const touching = useRef(false);
  const raf = useRef(0);
  const idle = useRef<number | undefined>(undefined);
  const loops = slideCount > 1;
  const slots = useRef(0);
  slots.current = loops ? slideCount + 2 : slideCount;

  /* scrollWidth over the slot count, not clientWidth: on a fractional-width
   * screen the rounding error would grow with the slide count. */
  const slotWidth = useCallback((): number => {
    const el = ref.current;
    if (!el || slots.current === 0) return 0;
    return (el.scrollWidth || el.clientWidth * slots.current) / slots.current;
  }, []);

  /* Past the copy before the first slide. Keyed on `loops` alone: a slide count
   * that changes under a strip in use (a re-read) must not send it back to the start. */
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && loops) el.scrollLeft = slotWidth();
  }, [loops, slotWidth]);

  useEffect(
    () => () => {
      cancelAnimationFrame(raf.current);
      window.clearTimeout(idle.current);
    },
    [],
  );

  const settle = useCallback((): void => {
    const el = ref.current;
    const w = slotWidth();
    if (!el || !loops || !w || touching.current) return;
    if (el.scrollLeft <= 1) el.scrollLeft = slideCount * w;
    else if (el.scrollLeft >= (slideCount + 1) * w - 1) el.scrollLeft = w;
  }, [slideCount, loops, slotWidth]);

  const onScroll = (): void => {
    window.clearTimeout(idle.current);
    idle.current = window.setTimeout(settle, SETTLE_IDLE_MS);
    if (raf.current) return;
    raf.current = requestAnimationFrame(() => {
      raf.current = 0;
      const el = ref.current;
      const w = slotWidth();
      if (!el || !w) return;
      /* The opening position's own scroll event is not use (a trackpad never touches). */
      if (Math.abs(el.scrollLeft - (loops ? w : 0)) > 1) setTouched(true);
      const slot = Math.round(el.scrollLeft / w);
      setAt(loops ? (slot - 1 + slideCount) % slideCount : Math.min(slot, Math.max(slideCount - 1, 0)));
      settle();
    });
  };

  const onLift = (e: TouchEvent<HTMLDivElement>): void => {
    if (e.touches.length > 0) return;
    touching.current = false;
    settle();
  };

  const last = Math.max(slideCount - 1, 0);
  const here = Math.min(at, last);
  return {
    ref,
    at: here,
    next: here === last ? 0 : here + 1,
    prev: here === 0 ? last : here - 1,
    atAnEnd: here === 0 || here === last,
    touched,
    handlers: {
      onScroll,
      onTouchStart: () => {
        touching.current = true;
        setTouched(true);
      },
      onTouchEnd: onLift,
      onTouchCancel: onLift,
    },
  };
}
