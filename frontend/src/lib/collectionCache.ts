/* The one home of the collection READS and of "which caches a collection WRITE
 * must refresh"; import from here instead of re-typing either.
 *
 * Reads: every surface takes the collection list and the whole-set member map
 * from the three hooks below — fourteen hand-typed useQuery copies over the two
 * keys had grown in twelve files, each re-deriving one property's Set.
 *
 * Writes: the curation analogue of lib/browseInvalidation, for the reason its
 * header records — the key list was hand-typed per call site and drifted. Every
 * writer but the menu had lost the shared member map (a property-page chip row
 * since deleted, the collection page's row-remove, both collection DELETEs, and
 * the merge, which invalidated no curation key at all), so a membership change
 * left every Browse card glyph painting the old state until the query went
 * stale on its own. */

import { useMemo } from 'react';
import { useQuery, type QueryClient } from '@tanstack/react-query';

import { listCollections } from '@/lib/api';
import { curationKeys, fetchPropertyCollectionMemberSet } from '@/lib/queries';
import { invalidateBrowseQueries } from '@/lib/browseInvalidation';
import type { Collection } from '@/lib/types';

const NO_COLLECTIONS: Collection[] = [];

/* The caller's collections (GET /collections). A failed read is a retry, never
 * "No collections yet" (MS16): callers branch on `query`, not on an empty list.
 * No keepPreviousData: the key is constant, so its only effect would be painting
 * the previous account's list after auth.tsx's identity-change clear(). */
export function useCollections() {
  const query = useQuery({
    queryKey: curationKeys.collections,
    queryFn: listCollections,
    staleTime: 30_000,
  });
  const collections = query.data?.data ?? NO_COLLECTIONS;
  const byId = useMemo(
    () => new Map(collections.map((c) => [c.id, c] as const)),
    [collections],
  );
  return { collections, byId, query };
}

/* The whole-set member map (property_id -> collection ids), one read for every
 * glyph. Returned as the query itself: cohort readers need `data` undefined
 * while it is unresolved (fail-open, rule #22) and every mark needs refetch. */
export function useCollectionMembers() {
  return useQuery({
    queryKey: curationKeys.propertyCollectionMembers,
    queryFn: fetchPropertyCollectionMemberSet,
    staleTime: 30_000,
  });
}

/* One property's collections. `ready` is true once the member map resolved.
 * Sharing (W5) splits `ids` into mine/shared here once owner_email exists, and
 * `ready` then waits on useCollections too (the split needs its owners). */
export function useCollectionMembership(property_id: number) {
  const query = useCollectionMembers();
  const ids = useMemo(
    () => new Set(query.data?.get(property_id) ?? []),
    [query.data, property_id],
  );
  return { ids, saved: ids.size > 0, ready: query.data !== undefined, query };
}

/** Re-read collections after ANY collection write — one idiom per call site,
 * metadata included. The member map is in the list because a property-grain
 * add/remove, a collection DELETE (migration 202 cascades its memberships away)
 * and a merge (property_carriers re-points collection_properties onto the
 * survivor) each change it. Pass `collection_id` when the write names one
 * collection, so its own page refetches too.
 *
 * `cohortScoped` is the same knob as `usePipelineCard({ cohortScoped })`: when
 * Browse is scoped to collections, membership IS the cohort, so the Browse
 * reads refetch too. Only a caller that knows the current filters can say so;
 * everyone else leaves it false (refetching every Browse surface on a save is
 * otherwise waste). */
export function revalidateCollections(
  qc: QueryClient,
  {
    collection_id,
    cohortScoped = false,
  }: { collection_id?: number; cohortScoped?: boolean } = {},
): void {
  qc.invalidateQueries({ queryKey: curationKeys.propertyCollectionMembers });
  qc.invalidateQueries({ queryKey: curationKeys.collections });
  if (collection_id != null) {
    qc.invalidateQueries({ queryKey: curationKeys.collection(collection_id) });
  }
  if (cohortScoped) invalidateBrowseQueries(qc);
}
