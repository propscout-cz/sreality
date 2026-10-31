/* The phone pager's gesture (components/phone/PhonePipeline): a finger on the
 * pages, or the pages still scrolling. While it lasts the pager holds the board's
 * reads (lib/revalidate `useReadHold`), so a card added or moved elsewhere lands
 * once the gesture ends, never under it.
 *
 * FINGERS. A touch event lists every finger on the screen; only those that came
 * down on the pages count, so a second finger on the strip or the header neither
 * holds nor, while the pages' finger lifts, keeps the hold. A finger's touchend
 * and touchcancel go to the node it came down on, even once that node is gone
 * from the page (a stage archived elsewhere, a tag chip re-drawn by its own
 * read), and a removed node's events never bubble to the pager. So each counted
 * finger's own node is listened to, and every lift is read one way: the fingers
 * the event still lists that are on the pages or already counted. A touchcancel
 * that leaves a finger down leaves its hold.
 *
 * SCROLLING lasts until no scroll event came for SCROLL_IDLE_MS (no `scrollend`
 * in React 18, nor in every browser the phone frame meets). The pager's own
 * landing (`land`, on first paint, after a reorder and on a resize) is no
 * gesture: held, it would cancel the board read a stage re-read has in flight
 * beside the stages, only to read it again. Its one scroll event is recognised
 * by where it landed and passed over. */

import { useCallback, useEffect, useRef, useState, type RefObject, type TouchEvent } from 'react';

export const SCROLL_IDLE_MS = 150;

export interface PagerGesture {
  /** A finger is on the pages, or they are still scrolling. */
  active: boolean;
  /** Land on page `index` at once, without starting the gesture. */
  land(index: number): void;
  /** The pages' scroll event. */
  onScroll(): void;
  /** The pages' touch handlers, spread onto the pager: a finger's lift is heard on its own node. */
  touch: {
    onTouchStart(e: TouchEvent<HTMLDivElement>): void;
  };
}

/** The gesture over the pager `pager` points at. */
export function usePagerGesture(pager: RefObject<HTMLDivElement | null>): PagerGesture {
  const [touching, setTouching] = useState(false);
  const [scrolling, setScrolling] = useState(false);
  const fingers = useState(() => trackFingers(pager, setTouching))[0];
  const scrollIdle = useRef<number | undefined>(undefined);
  /* Where `land` put the pages, until its scroll event comes. */
  const landed = useRef<number | null>(null);

  useEffect(
    () => () => {
      window.clearTimeout(scrollIdle.current);
      fingers.dispose();
    },
    [fingers],
  );

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
    touch: { onTouchStart: (e) => fingers.update(e.touches) },
  };
}

/* A touch event's finger list, as React's event and the DOM's both carry it. */
type Touches = ArrayLike<{ target: EventTarget }>;

/* The fingers on the pages, as the nodes they came down on (one entry per
 * finger), each node listened to for its own lift while a finger on it counts. */
function trackFingers(
  pager: RefObject<HTMLDivElement | null>,
  onChange: (touching: boolean) => void,
): { update(touches: Touches): void; dispose(): void } {
  let counted: Node[] = [];
  const listen = (node: Node, on: boolean): void => {
    for (const type of ['touchend', 'touchcancel']) {
      if (on) node.addEventListener(type, lift);
      else node.removeEventListener(type, lift);
    }
  };
  const update = (touches: Touches): void => {
    const el = pager.current;
    const next = Array.from(touches, (t) => t.target).filter(
      (t): t is Node => t instanceof Node && ((el?.contains(t) ?? false) || counted.includes(t)),
    );
    for (const node of new Set(counted)) if (!next.includes(node)) listen(node, false);
    for (const node of new Set(next)) if (!counted.includes(node)) listen(node, true);
    counted = next;
    onChange(next.length > 0);
  };
  function lift(e: Event): void {
    update((e as globalThis.TouchEvent).touches);
  }
  return {
    update,
    dispose: () => {
      for (const node of new Set(counted)) listen(node, false);
      counted = [];
    },
  };
}
