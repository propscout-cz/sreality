/* The phone pager's gesture (components/phone/PhonePipeline): a finger on the
 * pages, or the pages still scrolling. While it lasts the pager holds the board's
 * reads (lib/revalidate `useReadHold`), so a card added or moved elsewhere lands
 * once the gesture ends, never under it.
 *
 * FINGERS. A touch event lists every finger on the screen; only those that came
 * down on the pages count, so a second finger on the strip or the header neither
 * holds nor, while the pages' finger lifts, keeps the hold (its own touchend goes
 * to the strip, which the pager never hears). A finger whose page unmounted under
 * it (a stage archived elsewhere) stops counting at that commit: its touchend
 * goes to a detached node, which the pager never hears either. A touchcancel
 * ends every finger.
 *
 * SCROLLING lasts until no scroll event came for SCROLL_IDLE_MS (no `scrollend`
 * in React 18, nor in every browser the phone frame meets). The pager's own
 * landing (`land`, on first paint, after a reorder and on a resize) is no
 * gesture: held, it would cancel the board read a stage re-read has in flight
 * beside the stages, only to read it again. Its one scroll event is recognised
 * by where it landed and passed over. */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
  type TouchEvent,
} from 'react';

export const SCROLL_IDLE_MS = 150;

export interface PagerGesture {
  /** A finger is on the pages, or they are still scrolling. */
  active: boolean;
  /** Land on page `index` at once, without starting the gesture. */
  land(index: number): void;
  /** The pages' scroll event. */
  onScroll(): void;
  /** The pages' touch handlers, spread onto the pager. */
  touch: {
    onTouchStart(e: TouchEvent<HTMLDivElement>): void;
    onTouchEnd(e: TouchEvent<HTMLDivElement>): void;
    onTouchCancel(): void;
  };
}

/** The gesture over the pager `pager` points at. */
export function usePagerGesture(pager: RefObject<HTMLDivElement | null>): PagerGesture {
  const [touching, setTouching] = useState(false);
  const [scrolling, setScrolling] = useState(false);
  /* What each finger on the pages came down on. */
  const fingers = useRef<Node[]>([]);
  const scrollIdle = useRef<number | undefined>(undefined);
  /* Where `land` put the pages, until its scroll event comes. */
  const landed = useRef<number | null>(null);

  const keep = (targets: Node[]): void => {
    fingers.current = targets;
    setTouching(targets.length > 0);
  };
  const onPages = (e: TouchEvent<HTMLDivElement>): Node[] => {
    const el = pager.current;
    if (!el) return [];
    return Array.from(e.touches, (t) => t.target).filter((t): t is Node => t instanceof Node && el.contains(t));
  };

  useLayoutEffect(() => {
    const el = pager.current;
    if (fingers.current.length === 0) return;
    const still = el ? fingers.current.filter((t) => el.contains(t)) : [];
    if (still.length !== fingers.current.length) keep(still);
  });
  useEffect(() => () => window.clearTimeout(scrollIdle.current), []);

  const land = useCallback(
    (index: number): void => {
      const el = pager.current;
      if (!el || !el.clientWidth) return;
      const left = index * el.clientWidth;
      if (el.scrollLeft === left) return;
      el.scrollLeft = left;
      landed.current = el.scrollLeft;
    },
    [pager],
  );

  const onScroll = (): void => {
    const at = pager.current?.scrollLeft;
    const ours = landed.current != null && at != null && Math.abs(at - landed.current) < 1;
    landed.current = null;
    if (ours) return;
    if (!scrolling) setScrolling(true);
    window.clearTimeout(scrollIdle.current);
    scrollIdle.current = window.setTimeout(() => setScrolling(false), SCROLL_IDLE_MS);
  };

  return {
    active: touching || scrolling,
    land,
    onScroll,
    touch: {
      onTouchStart: (e) => keep(onPages(e)),
      onTouchEnd: (e) => keep(onPages(e)),
      onTouchCancel: () => keep([]),
    },
  };
}
