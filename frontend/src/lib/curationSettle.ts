/* What a curation write does to the Browse reads (operator ruling 9,
 * docs/design/curated-set-scope.md) — one module for every write that moves a
 * property in or out of a lens: a pipeline add / move / remove, a collection add
 * / remove, a tag attach / detach, a dismissal.
 *
 * The write names the property and the LENS it moved. Every cached Browse read
 * whose filters (slot 1 of its key, lib/browseKeys) engage that lens is judged
 * again for that one property, in memory, from the member maps the write has
 * already patched — the same rule the server applies (setScope.matchesScope,
 * migration 629) — so a card funnelled, saved, tagged or hidden while its lens
 * excludes it leaves the list at once:
 *
 *   still matches, on screen     → nothing (the glyph repaints from the map)
 *   no longer matches, on screen → the row drops IN PLACE from every loaded
 *                                  page (no page is re-read: a triage run of
 *                                  twenty clicks must not refetch the list
 *                                  twenty times) and the exact count drops one
 *   no longer matches            → map, stats and both counts re-read
 *   matches, not loaded          → may be a newcomer: that cohort re-reads
 *   cannot judge (a member map not in cache) → that cohort re-reads
 *
 * Two halves, because an optimistic write paints before the server has it and
 * must re-read only after: `settlePatches` (CachePatches for useOptimisticWrite,
 * held and rolled back with the rest; judged when APPLIED, so they go after the
 * member-map patches) and `settleRereads` (the keys to re-read once the write
 * has landed). `settleCurationWrite` runs both for a write that has already
 * landed, after `markMember` put it in the member map. */

import { hashKey, type InfiniteData, type Query, type QueryClient, type QueryKey } from '@tanstack/react-query';

import { browseKeys } from '@/lib/browseKeys';
import type { ListingFilters } from '@/lib/filters';
import { curationKeys, dismissalKeys, pipelineKeys, type PipelineMembers } from '@/lib/queries';
import { CURATED_SETS, CURATED_SET_NAMES, matchesScope, scopeActive, type CuratedSet } from '@/lib/setScope';
import { cachePatch, type CachePatch } from '@/lib/useOptimisticWrite';

/** What a write moved: one curated set, or the dismissal. */
export type CurationLens = CuratedSet | 'dismissed';

type ListPage = { rows: ReadonlyArray<{ property_id: number }> };

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

const memberIds = (qc: QueryClient, set: CuratedSet, propertyId: number): number[] | null => {
  if (set === 'pipeline') {
    const map = qc.getQueryData<PipelineMembers>(pipelineKeys.members);
    if (!map) return null;
    const m = map.get(propertyId);
    return m ? [m.stage_id] : [];
  }
  const key = set === 'tags' ? curationKeys.propertyTagMembers : curationKeys.propertyCollectionMembers;
  const map = qc.getQueryData<Map<number, number[]>>(key);
  return map ? (map.get(propertyId) ?? []) : null;
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

const dropRow = (propertyId: number) => (data: InfiniteData<ListPage> | undefined) =>
  data && {
    ...data,
    pages: data.pages.map((p) => ({ ...p, rows: p.rows.filter((r) => r.property_id !== propertyId) })),
  };

/** The in-place half: drop the property from every loaded page of a cohort it no
 * longer belongs to, and take one off that cohort's exact count. */
export function settlePatches(qc: QueryClient, propertyId: number, lens: CurationLens): CachePatch[] {
  const patches: CachePatch[] = [];
  for (const { f, queries } of cohorts(qc, lens)) {
    const lists = queries.filter((q) => isList(q) && holds(q.state.data, propertyId));
    if (lists.length === 0) continue;
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
    if (v === true && queries.some((q) => isList(q) && holds(q.state.data, propertyId))) continue;
    for (const root of v === false ? AGGREGATE_ROOTS : ALL_ROOTS) keys.push([root, f]);
  }
  return keys;
}

/** Settle a write that has already landed (its member map patched by the caller). */
export async function settleCurationWrite(
  qc: QueryClient,
  propertyId: number,
  lens: CurationLens,
): Promise<void> {
  const patches = settlePatches(qc, propertyId, lens);
  await Promise.all(patches.map((p) => qc.cancelQueries(p.filters)));
  for (const p of patches) qc.setQueriesData(p.filters, p.update);
  for (const queryKey of settleRereads(qc, propertyId, lens)) void qc.invalidateQueries({ queryKey });
}

/** Put a landed tag / collection write into its member map, so the settle judges
 * the new membership before the map's own re-read lands. */
export function markMember(
  qc: QueryClient,
  set: 'tags' | 'collections',
  propertyId: number,
  unitId: number,
  member: boolean,
): void {
  const key = set === 'tags' ? curationKeys.propertyTagMembers : curationKeys.propertyCollectionMembers;
  qc.setQueryData<Map<number, number[]>>(key, (prev) => {
    if (!prev) return prev;
    const next = new Map(prev);
    const ids = (prev.get(propertyId) ?? []).filter((id) => id !== unitId);
    if (member) ids.push(unitId);
    if (ids.length) next.set(propertyId, ids);
    else next.delete(propertyId);
    return next;
  });
}
