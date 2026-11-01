/* The fingers on an element, for a hold that lasts while a finger is down on it
 * (components/phone/usePagerGesture, lib/useListHold).
 *
 * A touch event lists every finger on the screen; only those that came down on
 * the element count. A finger's touchend and touchcancel go to the node it came
 * down on, even once that node is gone from the page, and a removed node's
 * events never bubble to the element. So each counted finger's own node is
 * listened to, and every lift is read one way: the fingers the event still lists
 * that are on the element or already counted. */

import type { RefObject } from 'react';

/* A touch event's finger list, as React's event and the DOM's both carry it. */
export type Touches = ArrayLike<{ target: EventTarget }>;

/** The fingers on `on`, as the nodes they came down on (one entry per finger),
 * each node listened to for its own lift while a finger on it counts. */
export function trackFingers(
  on: RefObject<Element | null>,
  onChange: (touching: boolean) => void,
): { update(touches: Touches): void; dispose(): void } {
  let counted: Node[] = [];
  const listen = (node: Node, add: boolean): void => {
    for (const type of ['touchend', 'touchcancel']) {
      if (add) node.addEventListener(type, lift);
      else node.removeEventListener(type, lift);
    }
  };
  const update = (touches: Touches): void => {
    const el = on.current;
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
