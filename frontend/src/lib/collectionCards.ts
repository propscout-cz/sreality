/* A collection's members as Browse cards, for the desktop collection page and
 * the phone's (ruling P32). THE CARDS ARE BROWSE'S READ: `fetchListingsForCards`
 * with the collection as its collection scope (`collections: {in: [id]}`,
 * judged server-side through `collection_properties_public`, rule 18 + migration
 * 629), every status and category, hidden properties included: a property the
 * operator hid stays in the list he filed it in. A member Browse does not serve
 * (no served location, rule 25; not yet published, rule 16) has no card, so the
 * pages list it apart from the collection's own read. */

import { useMemo } from 'react';
import type { QueryKey } from '@tanstack/react-query';

import { browseKeys } from '@/lib/browseKeys';
import { useCollectionMembers } from '@/lib/collectionCache';
import { FEED_ALL_FILTERS } from '@/lib/feed';
import type { ListingFilters } from '@/lib/filters';
import type { KeysetCursor } from '@/lib/keyset';
import { CARD_PAGE_SIZE, curationKeys, fetchListingsForCards, type CardRow, type SortSpec } from '@/lib/queries';
import { CURATED_SETS, matchesScope } from '@/lib/setScope';
import type { CollectionPropertyRow } from '@/lib/types';
import { useInfiniteList } from '@/lib/useInfiniteList';

/* The collection as a Browse cohort: no category, sale and rent, any status,
 * hidden included, scoped to this one collection. */
export function collectionCardFilters(id: number): ListingFilters {
  return {
    ...FEED_ALL_FILTERS,
    status: 'any',
    showDismissed: true,
    collections: { set: null, in: [id], out: [] },
  };
}

/** What the collection page lists, for a hand on it (lib/useListHold): the
 * collection's own read (its table, the cards' "without a card" rows) and its
 * cards in every order. */
export function collectionListKeys(id: number): QueryKey[] {
  return [curationKeys.collection(id), browseKeys.cohortCards(collectionCardFilters(id))];
}

export function useCollectionCards({
  collectionId,
  members,
  sort,
}: {
  collectionId: number;
  members: CollectionPropertyRow[];
  sort: SortSpec;
}) {
  const filters = useMemo(() => collectionCardFilters(collectionId), [collectionId]);

  const list = useInfiniteList<CardRow>({
    queryKey: browseKeys.cards(filters, sort),
    queryFn: (cursor, signal) => fetchListingsForCards(filters, sort, cursor as KeysetCursor | null, { signal }),
    pageSize: CARD_PAGE_SIZE,
    getRowId: (r) => r.property_id,
  });

  /* The member map every collection glyph paints from: a member without a card
   * leaves with it before the collection's own read lands. A failed read hides
   * nothing. */
  const memberMap = useCollectionMembers().data;
  const stillHere = (property_id: number): boolean =>
    memberMap == null
    || matchesScope(memberMap.get(property_id), filters.collections, CURATED_SETS.collections.inRule);

  /* Members with no card: only once the whole roll is in, never mid-paging. */
  const cardIds = new Set(list.rows.map((r) => r.property_id));
  const complete = !list.isLoading && !list.isError && !list.hasNextPage;
  const uncarded = complete
    ? members.filter((m) => !cardIds.has(m.property_id) && stillHere(m.property_id))
    : [];

  return { filters, list, complete, uncarded };
}
