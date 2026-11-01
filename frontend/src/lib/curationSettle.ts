/* What a curation write (pipeline add / move / remove, collection add / remove,
 * tag attach / detach, a dismissal) does to the cached Browse reads — operator
 * ruling 9, docs/design/curated-set-scope.md. Every cohort that engages the lens
 * the write moved is judged again for that one property, in memory, from the
 * member maps — setScope.matchesScope, the rule migration 629 applies:
 *
 *   still matches, on screen     → nothing (the glyph repaints from the map)
 *   no longer matches, on screen → the row drops IN PLACE (no page re-read: a
 *                                  triage run of twenty clicks must not refetch
 *                                  the list twenty times), the count drops one
 *   no longer matches            → map, stats and both counts re-read
 *   matches, not loaded          → may be a newcomer: that cohort re-reads
 *   cannot judge                 → that cohort re-reads, nothing drops
 *
 * The map can say "no" BEFORE the write while the list shows the row. Which
 * side is stale? The MAP, if one of the member reads the verdict stands on is
 * still owed (lib/revalidate `isOwed`) or was read before the list was: that
 * cohort re-reads, nothing drops. Otherwise the LIST — a change made elsewhere
 * after it was read, whose re-read (ruling Q3(c): within one clock read) has not
 * landed yet, or is parked while the user's hand is on the list (lib/useListHold):
 * the row is judged like any other, so a click that leaves it out drops it in
 * place and only the aggregates re-read. A map's read time is the cache's
 * `dataUpdatedAt`, which a patch keeps (lib/revalidate's ledger); a row's is its
 * page's `readAt` (lib/useInfiniteList): a page read later, under a hand or not,
 * does not refresh the rows before it, so the query's own time would overstate them.
 *
 * `settlePatches` are CachePatches built BEFORE the member maps move (that is
 * when the before-verdict is taken) and judged when applied, after them;
 * `settleRereads` names what to re-read once the write has landed. A change
 * made elsewhere reaches the cohorts through `lensTargets` instead. */

import { hashKey, type InfiniteData, type Query, type QueryClient, type QueryKey } from '@tanstack/react-query';

import { actingKey } from '@/lib/acting';
import { browseKeys } from '@/lib/browseKeys';
import { FEED_HIDDEN_FILTERS } from '@/lib/feed';
import type { ListingFilters } from '@/lib/filters';
import { curationKeys, dismissalKeys, pipelineKeys, type PipelineMembers } from '@/lib/queries';
import {
  cachePatch,
  holdReads,
  isOwed,
  revalidate,
  type CachePatch,
  type ReadMode,
  type Revalidation,
} from '@/lib/revalidate';
import { CURATED_SETS, CURATED_SET_NAMES, matchesScope, scopeActive, type CuratedSet } from '@/lib/setScope';
import type { InfiniteListPage } from '@/lib/useInfiniteList';

/** What a write moved: one curated set, or the dismissal. */
export type CurationLens = CuratedSet | 'dismissed';

type ListPage = InfiniteListPage<{ property_id: number }>;

const LIST_ROOTS = browseKeys.lists.map(([root]) => root);
const ALL_ROOTS = browseKeys.all.map(([root]) => root);
const AGGREGATE_ROOTS = browseKeys.aggregates.map(([root]) => root);

const lensEngaged = (f: ListingFilters, lens: CurationLens): boolean =>
  lens === 'dismissed' ? !f.showDismissed : scopeActive(f[lens]);

/* Every cached Browse read whose cohort the write can move, grouped by cohort. */
function cohorts(qc: QueryClient, lens: CurationLens) {
  const out = new Map<string, { f: ListingFilters; queries: Query[] }>();
  for (const root of ALL_ROOTS) {
    for (const q of qc.getQueryCache().findAll({ queryKey: [root] })) {
      const f = q.queryKey[1] as ListingFilters | undefined;
      if (f == null || typeof f !== 'object' || !lensEngaged(f, lens)) continue;
      const h = hashKey([f]);
      const c = out.get(h) ?? { f, queries: [] };
      c.queries.push(q);
      out.set(h, c);
    }
  }
  return [...out.values()];
}

/* Each set's member map: its key, and a property's element ids in it. */
const elementIds = (map: Map<number, number[]>, propertyId: number) => map.get(propertyId) ?? [];
const MEMBERS: Record<CuratedSet, { key: QueryKey; ids: (map: never, propertyId: number) => number[] }> = {
  tags: { key: curationKeys.propertyTagMembers, ids: elementIds },
  collections: { key: curationKeys.propertyCollectionMembers, ids: elementIds },
  pipeline: {
    key: pipelineKeys.members,
    ids: (map: PipelineMembers, propertyId) => {
      const card = map.get(propertyId);
      return card ? [card.stage_id] : [];
    },
  },
};

const memberIds = (qc: QueryClient, set: CuratedSet, propertyId: number): number[] | null => {
  const map = qc.getQueryData(MEMBERS[set].key);
  return map ? MEMBERS[set].ids(map as never, propertyId) : null;
};

/* Does the property belong to this cohort's lenses now? null = a member map it
 * needs is not in cache. A definite "no" wins over an unknown. */
function verdict(qc: QueryClient, propertyId: number, f: ListingFilters): boolean | null {
  if (!f.showDismissed && qc.getQueryData<boolean>(dismissalKeys.state(propertyId)) === true) return false;
  let unknown = false;
  for (const set of CURATED_SET_NAMES) {
    if (!scopeActive(f[set])) continue;
    const ids = memberIds(qc, set, propertyId);
    if (ids == null) unknown = true;
    else if (!matchesScope(ids, f[set], CURATED_SETS[set].inRule)) return false;
  }
  return unknown ? null : true;
}

/* The member reads a cohort's verdict on one property stands on. */
const memberKeys = (propertyId: number, f: ListingFilters): QueryKey[] => [
  ...(f.showDismissed ? [] : [dismissalKeys.state(propertyId)]),
  ...CURATED_SET_NAMES.filter((set) => scopeActive(f[set])).map((set) => MEMBERS[set].key),
];

/* When `q` read the row: the newest `readAt` of the pages holding it, else (a page
 * from before the stamp) the query's own read time. */
const rowReadAt = (q: Query, propertyId: number): number =>
  Math.max(
    ...((q.state.data as InfiniteData<ListPage> | undefined)?.pages ?? [])
      .filter((p) => p.rows.some((r) => r.property_id === propertyId))
      .map((p) => p.readAt ?? q.state.dataUpdatedAt),
  );

/* A "no" from a map whose read is owed, or older than the row's, is the map's staleness. */
function staleMap(qc: QueryClient, propertyId: number, f: ListingFilters, lists: readonly Query[]): boolean {
  if (verdict(qc, propertyId, f) !== false) return false;
  const listReadAt = Math.max(...lists.map((q) => rowReadAt(q, propertyId)));
  return memberKeys(propertyId, f).some((key) => {
    const readAt = qc.getQueryState(key)?.dataUpdatedAt;
    return isOwed(qc, key) || (readAt != null && readAt < listReadAt);
  });
}

const holds = (data: unknown, propertyId: number): boolean =>
  ((data as InfiniteData<ListPage> | undefined)?.pages ?? []).some((p) =>
    p.rows.some((r) => r.property_id === propertyId),
  );

const isList = (q: Query) => (LIST_ROOTS as readonly unknown[]).includes(q.queryKey[0]);

/* The page keeps the length it was read with, so a full page that lost a row
 * still pages on (useInfiniteList ends the list at a short page). */
const dropRow = (propertyId: number) => (data: InfiniteData<ListPage> | undefined) =>
  data && {
    ...data,
    pages: data.pages.map((p) => ({
      ...p,
      fetched: p.fetched ?? p.rows.length,
      rows: p.rows.filter((r) => r.property_id !== propertyId),
    })),
  };

/** Browse never acts (lib/acting): a write made as a pipeline's owner (`actAs`)
 * settles, patches and re-reads no Browse read. The one place that rule lives. */
export const browseReads = (actAs: string | null | undefined, keys: readonly QueryKey[]): QueryKey[] =>
  actAs ? [] : [...keys];

/** The in-place half: drop the property from every loaded page of a cohort it no
 * longer belongs to, and take one off that cohort's exact count. */
export function settlePatches(
  qc: QueryClient,
  propertyId: number,
  lens: CurationLens,
  actAs: string | null = null,
): CachePatch[] {
  if (actAs) return [];
  const patches: CachePatch[] = [];
  for (const { f, queries } of cohorts(qc, lens)) {
    const lists = queries.filter((q) => isList(q) && holds(q.state.data, propertyId));
    if (lists.length === 0 || staleMap(qc, propertyId, f, lists)) continue;
    let judged: boolean | null | undefined;
    const leaves = () => (judged === undefined ? (judged = verdict(qc, propertyId, f)) : judged) === false;
    for (const q of lists) {
      patches.push(
        cachePatch<InfiniteData<ListPage>>({ queryKey: q.queryKey, exact: true }, (old) =>
          leaves() ? dropRow(propertyId)(old) : old,
        ),
      );
    }
    patches.push(
      cachePatch<number>({ queryKey: browseKeys.count(f), exact: true }, (n) =>
        leaves() && typeof n === 'number' ? Math.max(0, n - 1) : n,
      ),
    );
  }
  return patches;
}

/** What to re-read once the write has landed; everything when it failed (the
 * rollback takes back only this write's patch, and a page that landed meanwhile
 * may hold it). */
export function settleRereads(
  qc: QueryClient,
  propertyId: number,
  lens: CurationLens,
  failed = false,
  actAs: string | null = null,
): QueryKey[] {
  if (failed) return browseReads(actAs, browseKeys.all);
  if (actAs) return [];
  const keys: QueryKey[] = [];
  for (const { f, queries } of cohorts(qc, lens)) {
    const v = verdict(qc, propertyId, f);
    const shown = queries.some((q) => isList(q) && holds(q.state.data, propertyId));
    if (v === true && shown) continue;
    for (const root of v === false && !shown ? AGGREGATE_ROOTS : ALL_ROOTS) keys.push([root, f]);
  }
  return keys;
}

/** Where a change made elsewhere moved `lens`: every cached Browse cohort that
 * engages it, its lists and its aggregates owed as `reread` says (curationSync
 * LENS_REREAD), and for a dismissal the Feed's Skryté roll, a list of the hidden
 * alone that no lens engages (lib/feed `FEED_HIDDEN_FILTERS`). Nothing per
 * property: a remote change names no property. */
export function lensTargets(
  qc: QueryClient,
  lens: CurationLens,
  reread: { lists: ReadMode; aggregates: ReadMode },
): Revalidation[] {
  const out: Revalidation[] = [];
  for (const { f } of cohorts(qc, lens)) {
    for (const root of LIST_ROOTS) out.push({ queryKey: [root, f], mode: reread.lists });
    for (const root of AGGREGATE_ROOTS) out.push({ queryKey: [root, f], mode: reread.aggregates });
  }
  if (lens === 'dismissed') out.push({ queryKey: browseKeys.cohortCards(FEED_HIDDEN_FILTERS), mode: reread.lists });
  return out;
}

/** Settle a landed tag / collection write: judged before, put into its member
 * map (so the settle sees it before the map's own re-read lands), judged after,
 * then `reads` (the caller's table reads, lib/curationSync `tableReads` — passed
 * in, since that module imports this one) and the settle's re-reads owed. One
 * hold spans the paint and the re-read, so no read lands in between and one
 * still in flight is owed, not lost. Acting as an owner (`actAs`, lib/acting)
 * only the owner's map moves: Browse never acts, so no Browse cohort is judged on
 * the owner's sets. */
export function settleMemberWrite(
  qc: QueryClient,
  set: 'tags' | 'collections',
  propertyId: number,
  elementId: number,
  member: boolean,
  { actAs = null, reads = [] }: { actAs?: string | null; reads?: readonly QueryKey[] } = {},
): void {
  const patches = [
    cachePatch<Map<number, number[]>>({ queryKey: actingKey(actAs, MEMBERS[set].key), exact: true }, (prev) => {
      if (!prev) return prev;
      const next = new Map(prev);
      const ids = (prev.get(propertyId) ?? []).filter((id) => id !== elementId);
      if (member) ids.push(elementId);
      if (ids.length) next.set(propertyId, ids);
      else next.delete(propertyId);
      return next;
    }),
    // Built before the paint above lands: that is when the before-verdict is taken.
    ...settlePatches(qc, propertyId, set, actAs),
  ];
  const hold = holdReads(
    qc,
    patches.map((p) => p.filters),
    patches,
  );
  void revalidate(qc, [...reads, ...settleRereads(qc, propertyId, set, false, actAs)], { release: hold });
}
