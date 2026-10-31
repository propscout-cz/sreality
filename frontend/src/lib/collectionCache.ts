/* The one home of the collection READS; import from here instead of re-typing
 * them. Every surface takes the collection list and the whole-set member map
 * from the three hooks below — fourteen hand-typed useQuery copies over the two
 * keys had grown in twelve files, each re-deriving one property's Set.
 *
 * Which reads a collection WRITE moves is lib/curationSync's map, by the table
 * the write touches (`revalidateTables`): the hand-typed list that lived here
 * had lost the shared member map at every writer but the menu, and re-read
 * acting variants no reader holds. */

import { useMemo } from 'react';
import { useQuery, type QueryClient } from '@tanstack/react-query';

import { listCollections } from '@/lib/api';
import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { curationKeys, fetchPropertyCollectionMemberSet } from '@/lib/queries';
import { settleMemberWrite } from '@/lib/curationSettle';
import { tableReads } from '@/lib/curationSync';
import { isMine } from '@/lib/collectionRules';
import type { Collection } from '@/lib/types';

const NO_COLLECTIONS: Collection[] = [];

/* The caller's collections (GET /collections) — or, inside <ActingAs>, the
 * owner's (lib/acting). A failed read is a retry, never "No collections yet"
 * (MS16): callers branch on `query`, not on an empty list. No keepPreviousData:
 * the key is constant per identity, so its only effect would be painting the
 * previous account's list after auth.tsx's identity-change clear(). `enabled`
 * false skips the read (a read-only board cannot see the owner's). */
export function useCollections({ enabled = true }: { enabled?: boolean } = {}) {
  const actAs = useActAs();
  const query = useQuery({
    queryKey: actingKey(actAs, curationKeys.collections),
    queryFn: () => listCollections(...actOpts(actAs)),
    staleTime: 30_000,
    enabled,
  });
  const collections = query.data?.data ?? NO_COLLECTIONS;
  const byId = useMemo(
    () => new Map(collections.map((c) => [c.id, c] as const)),
    [collections],
  );
  const mine = useMemo(() => collections.filter(isMine), [collections]);
  const shared = useMemo(() => collections.filter((c) => !isMine(c)), [collections]);
  return { collections, mine, shared, byId, query };
}

/* The whole-set member map (property_id -> collection ids), one read for every
 * glyph. Returned as the query itself: cohort readers need `data` undefined
 * while it is unresolved (fail-open, rule #22) and every mark needs refetch. */
export function useCollectionMembers({ enabled = true }: { enabled?: boolean } = {}) {
  const actAs = useActAs();
  return useQuery({
    queryKey: actingKey(actAs, curationKeys.propertyCollectionMembers),
    queryFn: ({ signal }) => fetchPropertyCollectionMemberSet({ signal, actAs }),
    staleTime: 30_000,
    enabled,
  });
}

/* The two reads one property's marks stand on, as one status: `data` is the
 * member map once BOTH resolved, so `readFailed(query)` shows a retry when either
 * failed with nothing cached, and `refetch` re-reads both. */
export interface MembershipRead {
  isLoading: boolean;
  isError: boolean;
  data: ReadonlyMap<number, number[]> | undefined;
  refetch: () => Promise<unknown>;
}

/* One property's collections. `ids` is every visible collection holding it
 * (cohort readers want that); `mine` / `shared` partition it by owner, and an id
 * the list does not know (a stale cache) lands in neither. `saved` means MINE: a
 * property only in someone else's collection never paints as saved, so the mark
 * agrees with the save menu, which lists mine only. `ready` waits for BOTH reads,
 * since the split needs the owners — no mark flashes wrong. `byId` is the list
 * the split read, for callers that need the collections themselves. */
export function useCollectionMembership(property_id: number) {
  const membersQ = useCollectionMembers();
  const { byId, query: listQ } = useCollections();
  const ids = useMemo(
    () => new Set(membersQ.data?.get(property_id) ?? []),
    [membersQ.data, property_id],
  );
  const { mine, shared } = useMemo(() => {
    const m = new Set<number>();
    const sh = new Set<number>();
    for (const id of ids) {
      const c = byId.get(id);
      if (c == null) continue;
      (isMine(c) ? m : sh).add(id);
    }
    return { mine: m, shared: sh };
  }, [ids, byId]);
  const ready = membersQ.data !== undefined && listQ.data !== undefined;
  const query: MembershipRead = {
    isLoading: membersQ.isLoading || listQ.isLoading,
    isError: membersQ.isError || listQ.isError,
    data: ready ? membersQ.data : undefined,
    refetch: () => Promise.all([membersQ.refetch(), listQ.refetch()]),
  };
  return { ids, mine, shared, byId, saved: mine.size > 0, ready, query };
}

/** One property added to / removed from one collection, landed: settled in the
 * Browse reads (lib/curationSettle, ruling 9) and the member map, with the
 * collection reads (lib/curationSync, `collection_properties`) owed in the same
 * hold. Acting (`actAs`), only the owner's entries move: Browse never acts. */
export const settleCollectionWrite = (
  qc: QueryClient,
  property_id: number,
  collection_id: number,
  member: boolean,
  actAs: string | null = null,
): void =>
  settleMemberWrite(
    qc,
    'collections',
    property_id,
    collection_id,
    member,
    actAs,
    tableReads(['collection_properties'], actAs),
  );
