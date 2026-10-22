/* A curated set as ONE kind of filter (docs/design/curated-set-scope.md): tags,
 * collections and pipeline stages share one value shape, one URL grammar, one
 * in-memory rule and one RPC argument shape. A scope has a whole-set side ('in' =
 * has any element of the set, 'out' = has none) and two element lists (elements that must
 * be present — tags EVERY, the others SOME — and elements that must be absent).
 * Membership itself is judged in the database under the caller's RLS (migration
 * 629); only element ids travel. */

import { czPlural } from './format';
import type { ListingFilters } from './filters';

export interface SetScope {
  set: 'in' | 'out' | null;
  in: number[];
  out: number[];
}

export type CuratedSet = 'tags' | 'collections' | 'pipeline';

export interface CuratedSetDescriptor {
  inRule: 'every' | 'some';
  words: { in: string; out: string };
  /* The dedicated "not in" chip (operator ruling 2, 2026-10-19). */
  notInLabel: string;
  /* The whole-set include chip, offered for the pipeline only. */
  inAnyLabel: string | null;
  /* The element's Czech noun for a count: 1 / 2–4 / 5+; after "bez": 1 / 2+. */
  elementNoun: [string, string, string];
  elementNounWithout: [string, string];
  /* The control's frame: the read-failed noun, the no-elements-yet line. */
  what: string;
  empty: string;
}

export const CURATED_SETS: Record<CuratedSet, CuratedSetDescriptor> = {
  tags: {
    inRule: 'every',
    words: { in: 'se štítkem', out: 'bez štítků' },
    notInLabel: 'Bez štítků',
    inAnyLabel: null,
    elementNoun: ['štítek', 'štítky', 'štítků'],
    elementNounWithout: ['štítku', 'štítků'],
    what: 'Štítky',
    empty: 'No tags yet. Add one from any property page or pipeline card.',
  },
  collections: {
    inRule: 'some',
    words: { in: 'v kolekci', out: 'v žádné kolekci' },
    notInLabel: 'V žádné kolekci',
    inAnyLabel: null,
    elementNoun: ['kolekce', 'kolekce', 'kolekcí'],
    elementNounWithout: ['kolekce', 'kolekcí'],
    what: 'Kolekce',
    empty: 'No collections yet. Create one from the Collections page.',
  },
  pipeline: {
    inRule: 'some',
    words: { in: 'v pipeline', out: 'mimo pipeline' },
    notInLabel: 'Mimo pipeline',
    inAnyLabel: 'V pipeline',
    elementNoun: ['fáze', 'fáze', 'fází'],
    elementNounWithout: ['fáze', 'fází'],
    what: 'Pipeline',
    empty: 'No pipeline stages yet.',
  },
};

export const CURATED_SET_NAMES = Object.keys(CURATED_SETS) as CuratedSet[];

const NO_SCOPE: SetScope = { set: null, in: [], out: [] };

const dedupe = (xs: ReadonlyArray<number>): number[] => [...new Set(xs)];

/* The normal form every scope is held in, so a contradiction cannot be stored:
 * the 'out' side empties both lists; an element on the in side supersedes the
 * 'in' side; an element sits on at most one list (the out side wins — the later
 * step of the chip cycle); lists are deduplicated; all-empty is null (off). */
export function normalizeScope(s: SetScope | null | undefined): SetScope | null {
  if (!s) return null;
  if (s.set === 'out') return { set: 'out', in: [], out: [] };
  const out = dedupe(s.out);
  const excluded = new Set(out);
  const inc = dedupe(s.in).filter((id) => !excluded.has(id));
  const set = s.set === 'in' && inc.length === 0 ? 'in' : null;
  if (set == null && inc.length === 0 && out.length === 0) return null;
  return { set, in: inc, out };
}

/* THE tri-state chip transition (portals, curated-set elements): one tap moves x
 * off → included → excluded → off; every other value keeps its place. */
export function cycle<T>(
  inc: ReadonlyArray<T>,
  exc: ReadonlyArray<T>,
  x: T,
): { inc: T[]; exc: T[] } {
  const without = (xs: ReadonlyArray<T>) => xs.filter((v) => v !== x);
  if (inc.includes(x)) return { inc: without(inc), exc: [...exc, x] };
  if (exc.includes(x)) return { inc: [...inc], exc: without(exc) };
  return { inc: [...inc, x], exc: [...exc] };
}

/* An element chip. Element chips are disabled while the whole set is excluded; a tap
 * that still arrives lifts the exclusion so the element choice is not swallowed. */
export function cycleElement(scope: SetScope | null, id: number): SetScope | null {
  const s = scope ?? NO_SCOPE;
  const { inc, exc } = cycle(s.in, s.out, id);
  return normalizeScope({ set: s.set === 'out' ? null : s.set, in: inc, out: exc });
}

/* A whole-set chip: the dedicated "not in" chip (side 'out') or, where offered,
 * the whole-set include chip (side 'in'): off ↔ on. Turning 'in' on supersedes
 * the element includes (the chip means "any element"); the element excludes stay. */
export function cycleSet(scope: SetScope | null, side: 'in' | 'out' = 'out'): SetScope | null {
  const s = scope ?? NO_SCOPE;
  if (s.set === side) return normalizeScope({ ...s, set: null });
  if (side === 'out') return { set: 'out', in: [], out: [] };
  return normalizeScope({ set: 'in', in: [], out: s.out });
}

/* The rule per property, in memory (the pipeline board, the phone roll):
 * `undefined` memberIds = in no element of the set. */
export function matchesScope(
  memberIds: readonly number[] | undefined,
  scope: SetScope | null,
  inRule: 'every' | 'some',
): boolean {
  if (!scope) return true;
  const has = new Set(memberIds ?? []);
  if (scope.set === 'in' && has.size === 0) return false;
  if (scope.set === 'out' && has.size > 0) return false;
  if (scope.in.length) {
    const ok = inRule === 'every'
      ? scope.in.every((id) => has.has(id))
      : scope.in.some((id) => has.has(id));
    if (!ok) return false;
  }
  return !scope.out.some((id) => has.has(id));
}

export const scopeActive = (s: SetScope | null | undefined): boolean =>
  normalizeScope(s) != null;

/* The in side only — "show me what is in it" (FilterPresetBar's Pipeline chip). */
export const scopeIncludes = (s: SetScope | null | undefined): boolean =>
  s != null && s.set !== 'out' && (s.set === 'in' || s.in.length > 0);

/* The scope in Czech words for summaries and phone rows; null when off.
 * "v pipeline", "v kolekci (2 kolekce)", "se štítkem (1 štítek), bez 2 štítků",
 * "mimo pipeline". */
export function scopeWords(name: CuratedSet, scope: SetScope | null | undefined): string | null {
  const s = normalizeScope(scope);
  if (!s) return null;
  const d = CURATED_SETS[name];
  if (s.set === 'out') return d.words.out;
  const count = (n: number) => `${n} ${czPlural(n, ...d.elementNoun)}`;
  const parts: string[] = [];
  if (s.set === 'in') parts.push(d.words.in);
  else if (s.in.length) parts.push(`${d.words.in} (${count(s.in.length)})`);
  const n = s.out.length;
  if (n) parts.push(`bez ${n} ${d.elementNounWithout[n === 1 ? 0 : 1]}`);
  return parts.join(', ');
}

/* Positive integer ids from a CSV; anything else is dropped. */
export const parseIntList = (s: string | null): number[] => {
  if (!s) return [];
  const out: number[] = [];
  for (const part of s.split(',')) {
    const n = Number(part);
    if (Number.isInteger(n) && n > 0) out.push(n);
  }
  return out;
};

/* One side of the wire: `any` = the whole set, a CSV = elements, junk = off. */
const parseSide = (s: string | null): { any: boolean; ids: number[] } =>
  s === 'any' ? { any: true, ids: [] } : { any: false, ids: parseIntList(s) };

/* The URL grammar, one codec for Browse and the pipeline board:
 * `<set>=any|<csv>` is the in side, `<set>_excluded=any|<csv>` the out side —
 * the set's name, as the RPC arguments are `<set>_set` / `_in` / `_out`. Junk
 * on any side degrades to off. */
export function parseSetScope(sp: URLSearchParams, name: CuratedSet): SetScope | null {
  const inc = parseSide(sp.get(name));
  const exc = parseSide(sp.get(`${name}_excluded`));
  return normalizeScope({
    set: exc.any ? 'out' : inc.any ? 'in' : null,
    in: inc.ids,
    out: exc.ids,
  });
}

/* Writes both params of one set (deleting a side that is off), so a caller may
 * patch an existing URL in place. */
export function setScopeToParams(
  sp: URLSearchParams,
  name: CuratedSet,
  scope: SetScope | null,
): void {
  const s = normalizeScope(scope);
  const inSide = s?.set === 'in' ? 'any' : s?.in.length ? s.in.join(',') : null;
  const outSide = s?.set === 'out' ? 'any' : s?.out.length ? s.out.join(',') : null;
  if (inSide) sp.set(name, inSide);
  else sp.delete(name);
  if (outSide) sp.set(`${name}_excluded`, outSide);
  else sp.delete(`${name}_excluded`);
}

export type ScopeArgs = Partial<
  Record<`${CuratedSet}_set`, 'in' | 'out'> & Record<`${CuratedSet}_in` | `${CuratedSet}_out`, number[]>
>;

/* The four cohort functions' scope arguments (migration 629) with ONLY the
 * active sides as keys: postgrest-js stringifies a null GET argument to the
 * text 'null', so an off side is absent, never null. */
export function scopeArgs(f: Pick<ListingFilters, CuratedSet>): ScopeArgs {
  const args: Record<string, 'in' | 'out' | number[]> = {};
  for (const name of CURATED_SET_NAMES) {
    const s = normalizeScope(f[name]);
    if (!s) continue;
    if (s.set) args[`${name}_set`] = s.set;
    if (s.in.length) args[`${name}_in`] = s.in;
    if (s.out.length) args[`${name}_out`] = s.out;
  }
  return args as ScopeArgs;
}
