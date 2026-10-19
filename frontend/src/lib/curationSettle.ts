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
 *   cannot judge, or the map said "no" BEFORE the write while the server shows
 *   the row (a stale map)        → that cohort re-reads, nothing drops
 *
 * `settlePatches` are CachePatches built BEFORE the member maps move (that is
 * when the before-verdict is taken) and judged when applied, after them;
 * `settleRereads` names what to re-read once the write has landed. */

import { hashKey, type InfiniteData, type Query, type QueryClient, type QueryKey } from '@tanstack/react-query';

import { browseKeys } from '@/lib/browseKeys';
import type { ListingFilters } from '@/lib/filters';
import { curationKeys, dismissalKeys, pipelineKeys, type PipelineMembers } from '@/lib/queries';
import { CURATED_SETS, CURATED_SET_NAMES, matchesScope, scopeActive, type CuratedSet } from '@/lib/setScope';
import { cachePatch, type CachePatch } from '@/lib/useOptimisticWrite';
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

/** The in-place half: drop the property from every loaded page of a cohort it no
 * longer belongs to, and take one off that cohort's exact count. */
export function settlePatches(qc: QueryClient, propertyId: number, lens: CurationLens): CachePatch[] {
  const patches: CachePatch[] = [];
  for (const { f, queries } of cohorts(qc, lens)) {
    const lists = queries.filter((q) => isList(q) && holds(q.state.data, propertyId));
    if (lists.length === 0 || verdict(qc, propertyId, f) === false) continue;
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
 * rollback restored the lists as they were at the click). */
export function settleRereads(
  qc: QueryClient,
  propertyId: number,
  lens: CurationLens,
  failed = false,
): QueryKey[] {
  if (failed) return [...browseKeys.all];
  const keys: QueryKey[] = [];
  for (const { f, queries } of cohorts(qc, lens)) {
    const v = verdict(qc, propertyId, f);
    const shown = queries.some((q) => isList(q) && holds(q.state.data, propertyId));
    if (v === true && shown) continue;
    for (const root of v === false && !shown ? AGGREGATE_ROOTS : ALL_ROOTS) keys.push([root, f]);
  }
  return keys;
}

/** Settle a landed tag / collection write: judged before, put into its member
 * map (so the settle sees it before the map's own re-read lands), judged after. */
export async function settleMemberWrite(
  qc: QueryClient,
  set: 'tags' | 'collections',
  propertyId: number,
  elementId: number,
  member: boolean,
): Promise<void> {
  const patches = settlePatches(qc, propertyId, set);
  qc.setQueryData<Map<number, number[]>>(MEMBERS[set].key, (prev) => {
    if (!prev) return prev;
    const next = new Map(prev);
    const ids = (prev.get(propertyId) ?? []).filter((id) => id !== elementId);
    if (member) ids.push(elementId);
    if (ids.length) next.set(propertyId, ids);
    else next.delete(propertyId);
    return next;
  });
  await Promise.all(patches.map((p) => qc.cancelQueries(p.filters)));
  for (const p of patches) qc.setQueriesData(p.filters, p.update);
  for (const queryKey of settleRereads(qc, propertyId, set)) void qc.invalidateQueries({ queryKey });
}
