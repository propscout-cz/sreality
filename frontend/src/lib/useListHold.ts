/* A hand on a list. An open Browse, Feed or collection list follows a change
 * made elsewhere within one clock read (ruling Q3(c), 2026-11-01;
 * docs/design/curation-sync.md Decision 7b), except while the user's hand is on
 * it: the pointer over it, a finger down on it, or keyboard focus inside it.
 * Then its re-reads PARK (lib/revalidate `parkReads`) and are paid the moment
 * the hand leaves, so no row moves under it. Only the list's own keys are held:
 * counts, map and stats re-read at once, never held. The list's own reads pass
 * (a first load after a filter or sort change, a page, a refresh it asks for),
 * and so do the user's own writes: each holds for itself, and the ledger
 * composes the two.
 *
 * POINTER. Over: React's pointerover reaches the container from its whole React
 * subtree, a popover portalled out of a row included, so a stage menu opened from
 * a card keeps the hand on the list. Leave: React's pointerleave; and since a
 * portal unmounted under the pointer leaves no pointerout to compute a leave
 * from, a pointerover anywhere that did not pass through the container (heard on
 * the document, after React's root) ends it too. A pointerdown counts as over:
 * a pointer that has not moved since the list drew under it.
 *
 * TOUCH. From touchstart until the last finger that came down on the list lifts
 * (lib/fingers): a touch pointer's own events end at the scroll's pointercancel.
 *
 * KEYBOARD. Focus inside that the browser shows (`:focus-visible`), so a click
 * that leaves focus on a card's button does not keep the hand there once the
 * pointer goes. A focus move inside the list is no leave (its focusout waits one
 * task for the focusin); a focusin anywhere outside ends it at once. */

import { useEffect, useMemo, type FocusEvent, type PointerEvent, type TouchEvent } from 'react';
import { hashKey, useQueryClient, type QueryClient, type QueryKey } from '@tanstack/react-query';

import { trackFingers } from '@/lib/fingers';
import { parkReads, type Hold } from '@/lib/revalidate';

/** Spread onto the list's container element. */
export interface ListHand {
  ref: (el: HTMLElement | null) => void;
  onPointerOver: (e: PointerEvent) => void;
  onPointerDown: (e: PointerEvent) => void;
  onPointerLeave: () => void;
  onTouchStart: (e: TouchEvent) => void;
  onFocus: (e: FocusEvent) => void;
  onBlur: () => void;
}

/* Focus the browser shows; a browser that cannot say counts every focus. */
const shown = (target: EventTarget): boolean => {
  try {
    return target instanceof Element && target.matches(':focus-visible');
  } catch {
    return true;
  }
};

function hand(qc: QueryClient) {
  const el: { current: Element | null } = { current: null };
  let keys: readonly QueryKey[] = [];
  let pointer = false;
  let touching = false;
  let keyboard = false;
  /* The last pointerover and focusin that passed through the container. */
  let over: Event | null = null;
  let focused: Event | null = null;
  let blur: ReturnType<typeof setTimeout> | undefined;
  let hold: Hold | null = null;

  const sync = (): void => {
    const on = pointer || touching || keyboard;
    if (on && !hold && keys.length > 0) hold = parkReads(qc, keys.map((queryKey) => ({ queryKey })));
    if (!on && hold) {
      const parked = hold;
      hold = null;
      parked.release();
    }
  };
  const fingers = trackFingers(el, (down) => {
    touching = down;
    sync();
  });
  const point = (e: PointerEvent): void => {
    over = e.nativeEvent;
    pointer = true;
    sync();
  };
  const overElsewhere = (e: Event): void => {
    if (!pointer || e === over) return;
    pointer = false;
    sync();
  };
  const focusElsewhere = (e: Event): void => {
    if (!keyboard || e === focused) return;
    keyboard = false;
    sync();
  };

  const props: ListHand = {
    ref: (node) => {
      el.current = node;
    },
    onPointerOver: point,
    onPointerDown: point,
    onPointerLeave: () => {
      pointer = false;
      sync();
    },
    onTouchStart: (e) => fingers.update(e.touches),
    onFocus: (e) => {
      focused = e.nativeEvent;
      clearTimeout(blur);
      keyboard = shown(e.target);
      sync();
    },
    onBlur: () => {
      clearTimeout(blur);
      blur = setTimeout(() => {
        keyboard = false;
        sync();
      }, 0);
    },
  };

  return {
    props,
    /* The list's keys now: a hand already down moves onto them before it lets the old ones go. */
    aim(next: readonly QueryKey[]): void {
      keys = next;
      const old = hold;
      hold = null;
      sync();
      old?.release();
    },
    listen(): () => void {
      document.addEventListener('pointerover', overElsewhere);
      document.addEventListener('focusin', focusElsewhere);
      return () => {
        document.removeEventListener('pointerover', overElsewhere);
        document.removeEventListener('focusin', focusElsewhere);
        clearTimeout(blur);
        fingers.dispose();
        pointer = false;
        touching = false;
        keyboard = false;
        sync();
      };
    },
  };
}

/** Hold the re-reads of `keys` (by prefix: the list's own queries only) while a hand is on the element the returned props are spread on. */
export function useListHold(keys: readonly QueryKey[]): ListHand {
  const qc = useQueryClient();
  const h = useMemo(() => hand(qc), [qc]);
  const hash = hashKey(keys as unknown as QueryKey);
  useEffect(() => h.listen(), [h]);
  useEffect(
    () => h.aim(keys),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `hash` is the identity of `keys`
    [h, hash],
  );
  return h.props;
}
