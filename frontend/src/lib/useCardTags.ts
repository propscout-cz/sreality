/* A property's tags as a card prints them: the two keys every tag surface
 * shares (the index + the one member map, lib/tagCache revalidates both), so
 * a tag put on anywhere is on every card without a reload, and the board,
 * the phone board and the drag ghost cannot drift on what a card carries. */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { listTags } from '@/lib/api';
import { curationKeys, fetchPropertyTagMemberSet } from '@/lib/queries';

/* The tag index and the member map — the caller's, or inside <ActingAs> the
 * owner's (lib/acting). `enabled` false skips both reads (a read-only board
 * cannot see the owner's tags); `index` / `members` false skips that one (the
 * filter's tag list needs no whole-set member read, a tag row no index). */
export function useTagReads({
  enabled = true,
  index = true,
  members = true,
}: { enabled?: boolean; index?: boolean; members?: boolean } = {}) {
  const actAs = useActAs();
  const tagsQ = useQuery({
    queryKey: actingKey(actAs, curationKeys.tags),
    queryFn: () => listTags(...actOpts(actAs)),
    staleTime: 60_000,
    enabled: enabled && index,
  });
  const membersQ = useQuery({
    queryKey: actingKey(actAs, curationKeys.propertyTagMembers),
    queryFn: ({ signal }) => fetchPropertyTagMemberSet({ signal, actAs }),
    staleTime: 30_000,
    enabled: enabled && members,
  });
  return { tagsQ, membersQ };
}

export function useCardTags(property_id: number) {
  const { tagsQ, membersQ } = useTagReads();
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
