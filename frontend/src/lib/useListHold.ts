/* A hand on a list. An open Browse, Feed or collection list follows a change
 * made elsewhere within one clock read (ruling Q3(c), 2026-11-01;
 * docs/design/curation-sync.md Decision 7b), except while the user's hand is on
 * it: the pointer over it, or a finger down on it (the ruling's two; keyboard
 * focus is not a hand). Then its re-reads PARK (lib/revalidate `parkReads`) and
 * are paid the moment the hand leaves, so no row moves under it. Only the list's
 * own keys are held: counts, map and stats re-read at once, never held. The
 * list's own reads pass (a first load after a filter or sort change, a page, a
 * refresh the user asks for, a sort revisited), and so do the user's own writes:
 * each holds for itself, and the ledger composes the two. A reconnect refetch
 * never starts on a list root (lib/curationSync), so none lands under a hand.
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
 * HELD IN THE VIEW. A list whose rows share a read with what is never held (the
 * collection page: its header's name and member count ride its own read) holds
 * those rows in the view instead (`useHeld`): the read lands at once, the rows
 * stay as drawn until the hand leaves. */

import { useEffect, useMemo, useReducer, useRef, type PointerEvent, type TouchEvent } from 'react';
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
}

interface Hand {
  props: ListHand;
  aim(next: readonly QueryKey[]): void;
  listen(): () => void;
  isOn(): boolean;
  /* Told each time the hand arrives or leaves. */
  watch(listener: (on: boolean) => void): () => void;
}

/* Each hand by the props it handed out, for `useHeld`. */
const hands = new WeakMap<ListHand, Hand>();

function hand(qc: QueryClient): Hand {
  const el: { current: Element | null } = { current: null };
  let keys: readonly QueryKey[] = [];
  let pointer = false;
  let touching = false;
  let on = false;
  /* The last pointerover that passed through the container. */
  let over: Event | null = null;
  let hold: Hold | null = null;
  const watchers = new Set<(on: boolean) => void>();

  const sync = (): void => {
    const now = pointer || touching;
    if (now && !hold && keys.length > 0) hold = parkReads(qc, keys.map((queryKey) => ({ queryKey })));
    if (!now && hold) {
      const parked = hold;
      hold = null;
      parked.release();
    }
    if (now === on) return;
    on = now;
    for (const watcher of watchers) watcher(on);
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
  };

  return {
    props,
    /* The list's keys now: a hand already down moves onto them before it lets the old ones go. */
    aim(next) {
      keys = next;
      const old = hold;
      hold = null;
      sync();
      old?.release();
    },
    listen() {
      document.addEventListener('pointerover', overElsewhere);
      return () => {
        document.removeEventListener('pointerover', overElsewhere);
        fingers.dispose();
        pointer = false;
        touching = false;
        sync();
      };
    },
    isOn: () => on,
    watch(listener) {
      watchers.add(listener);
      return () => {
        watchers.delete(listener);
      };
    },
  };
}

/** Hold the re-reads of `keys` (by prefix: the list's own queries only) while a hand is on the element the returned props are spread on. */
export function useListHold(keys: readonly QueryKey[]): ListHand {
  const qc = useQueryClient();
  const h = useMemo(() => {
    const made = hand(qc);
    hands.set(made.props, made);
    return made;
  }, [qc]);
  const hash = hashKey(keys);
  useEffect(() => h.listen(), [h]);
  useEffect(
    () => h.aim(keys),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `hash` is the identity of `keys`
    [h, hash],
  );
  return h.props;
}

/** `value` as the list under `hand` shows it: the current one while no hand is
 * on the list, else the one drawn when the hand arrived (a component that mounts
 * under the hand, or is given its hand under it, draws the current one), until
 * the hand leaves. No `hand`: the current value. A row the user removes here is
 * the caller's to drop at once. */
export function useHeld<T>(hand: ListHand | undefined, value: T): T {
  const h = hand ? hands.get(hand) : undefined;
  const shown = useRef(value);
  const latest = useRef(value);
  const given = useRef(h);
  const [, repaint] = useReducer((n: number) => n + 1, 0);
  latest.current = value;
  if (!h?.isOn() || given.current !== h) shown.current = value;
  given.current = h;
  useEffect(
    () => h?.watch((on) => {
      if (!on && shown.current !== latest.current) repaint();
    }),
    [h],
  );
  return shown.current;
}
