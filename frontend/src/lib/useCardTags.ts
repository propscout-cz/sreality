/* A property's tags as a card prints them: the two keys every tag surface
 * shares (the index + the one member map, lib/tagCache revalidates both), so
 * a tag put on anywhere is on every card without a reload, and the board,
 * the phone board and the drag ghost cannot drift on what a card carries. */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { listTags } from '@/lib/api';
import { curationKeys, fetchPropertyTagMemberSet } from '@/lib/queries';

export function useCardTags(property_id: number) {
  const tagsQ = useQuery({
    queryKey: curationKeys.tags,
    queryFn: listTags,
    staleTime: 60_000,
  });
  const membersQ = useQuery({
    queryKey: curationKeys.propertyTagMembers,
    queryFn: fetchPropertyTagMemberSet,
    staleTime: 30_000,
  });
  const memberIds = useMemo(
    () => new Set(membersQ.data?.get(property_id) ?? []),
    [membersQ.data, property_id],
  );
  const mine = useMemo(
    () =>
      (tagsQ.data?.data ?? [])
        .filter((t) => memberIds.has(t.id))
        .sort((a, b) => a.name.localeCompare(b.name, 'cs')),
    [tagsQ.data, memberIds],
  );
  return { membersQ, memberIds, mine };
}
