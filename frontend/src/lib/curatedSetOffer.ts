/* Which curated-set chips the pipeline board's bar offers — collections and
 * tags, one rule: only the sets with a member ON the board, plus any unit the
 * scope names and a known set carries (a unit no set carries any more is the
 * control's `#id` chip, components/filter-controls/CuratedSetFilter).
 * A live scope (lib/setScope) is always shown; otherwise the row is offered
 * only when it could change the view. Membership is multi-valued and partial, so an option count
 * would wrongly hide a working control — two sets can partition a board with
 * every card inside one. Undefined members = the read is unresolved or failed
 * (fail-open, rule #22): nothing is offered. */

import { scopeActive, type SetScope } from './setScope';

export interface CuratedSetOffer<T> {
  options: T[];
  show: boolean;
}

export function offerCuratedSet<T extends { id: number }>(
  cards: ReadonlyArray<{ property_id: number }>,
  members: ReadonlyMap<number, readonly number[]> | undefined,
  known: ReadonlyArray<T>,
  scope: SetScope | null,
): CuratedSetOffer<T> {
  const selected = scope ? [...scope.in, ...scope.out] : [];
  if (!members) return { options: [], show: false };
  const held = new Set<number>();
  let inSet = 0;
  for (const c of cards) {
    const ids = members.get(c.property_id);
    if (ids?.length) {
      inSet += 1;
      for (const id of ids) held.add(id);
    }
  }
  const onBoard = known.filter((s) => held.has(s.id));
  const options = [...onBoard];
  for (const id of selected) {
    if (options.some((o) => o.id === id)) continue;
    const k = known.find((s) => s.id === id);
    if (k) options.push(k);
  }
  return {
    options,
    show:
      scopeActive(scope) ||
      (onBoard.length > 0 && (inSet < cards.length || onBoard.length >= 2)),
  };
}
