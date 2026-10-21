/* Browse's merge mode as ONE statement of letters (the split's vocabulary, MS18,
 * over properties): the operator writes a letter on each card, and one click
 * makes the statement true. A key's target is the focused card — in merge mode
 * the pointer focuses the card it is over (ListingCards) and Tab moves card to
 * card — and `onKeyDown`, attached to the card grid (never the window, so a
 * filter field keeps its keys), marks that card: a letter key stamps it and
 * becomes the active letter, the same letter again clears it, Backspace and
 * Delete clear it. A click (`stamp`) writes the active letter, A at first, so
 * "click, click, merge" still makes one group as it always did. The letters
 * live here and nowhere else: leaving the mode drops them, and the next session
 * starts at A again. */
import { useCallback, useMemo, useState, type KeyboardEvent } from 'react';

import type { Letters } from '@/lib/api';

export interface MergeMode {
  active: boolean;
  /* property_id → letter. */
  letters: Letters;
  /* What a click writes: the last letter key pressed, or the chip picked. */
  activeLetter: string;
  /* Every letter in use, A first, each with its properties ascending. */
  groups: ReadonlyArray<readonly [string, readonly number[]]>;
  /* One letter at least, and none held by a single property. */
  complete: boolean;
  enter: () => void;
  exit: () => void;
  stamp: (propertyId: number) => void;
  mark: (propertyId: number, letter: string) => void;
  clear: (propertyId: number) => void;
  setActiveLetter: (letter: string) => void;
  onKeyDown: (e: KeyboardEvent) => void;
}

export function useMergeMode(): MergeMode {
  const [active, setActive] = useState(false);
  const [letters, setLetters] = useState<Letters>({});
  const [activeLetter, setActiveLetter] = useState('A');

  const mark = useCallback((propertyId: number, letter: string) => {
    setActiveLetter(letter);
    setLetters((prev) => {
      const next = { ...prev };
      if (prev[propertyId] === letter) delete next[propertyId];
      else next[propertyId] = letter;
      return next;
    });
  }, []);
  const clear = useCallback((propertyId: number) => {
    setLetters((prev) => {
      if (!(propertyId in prev)) return prev;
      const next = { ...prev };
      delete next[propertyId];
      return next;
    });
  }, []);
  const stamp = useCallback(
    (propertyId: number) => mark(propertyId, activeLetter),
    [mark, activeLetter],
  );
  const enter = useCallback(() => setActive(true), []);
  const exit = useCallback(() => {
    setActive(false);
    setLetters({});
    setActiveLetter('A');
  }, []);
  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
      const card = (e.target as Element).closest?.('[data-property-id]');
      const propertyId = Number(card?.getAttribute('data-property-id'));
      if (!card || !Number.isFinite(propertyId)) return;
      if (/^[a-z]$/i.test(e.key)) {
        e.preventDefault();
        mark(propertyId, e.key.toUpperCase());
      } else if (e.key === 'Backspace' || e.key === 'Delete') {
        e.preventDefault();
        clear(propertyId);
      }
    },
    [mark, clear],
  );
  const groups = useMemo(() => {
    const by = new Map<string, number[]>();
    for (const [id, letter] of Object.entries(letters)) {
      by.set(letter, [...(by.get(letter) ?? []), Number(id)]);
    }
    return [...by.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([letter, ids]) => [letter, ids.sort((a, b) => a - b)] as const);
  }, [letters]);
  const complete = groups.length > 0 && groups.every(([, ids]) => ids.length >= 2);

  return useMemo(
    () => ({
      active, letters, activeLetter, groups, complete,
      enter, exit, stamp, mark, clear, setActiveLetter, onKeyDown,
    }),
    [active, letters, activeLetter, groups, complete, enter, exit, stamp, mark, clear, onKeyDown],
  );
}
