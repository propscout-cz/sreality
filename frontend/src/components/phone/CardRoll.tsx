/* A roll of FeedCards over one keyset-paged Browse read, undivided: the
 * collection's members, the broker's ads. Its own file so the card stays out of
 * RollParts, which desktop pages import eagerly (the first-visit budget). */

import { useMemo, type ReactNode } from 'react';

import InfiniteSentinel from '@/components/InfiniteSentinel';
import { useCardCoverTag } from '@/lib/browseLayout';
import { CardHydrationProvider } from '@/lib/hydration';
import type { CardRow } from '@/lib/queries';

import FeedCard from './FeedCard';
import { RollEmpty, RollSkeleton } from './RollParts';

/* Every photo the Browse carousel would hold, so the cover choice is the same and
 * the hydration cache is shared with Browse (BrowseExperience's perId). The card
 * shows the first eight (CARD_STRIP_CAP) and counts them all. */
export const PHOTOS_PER_CARD = 50;

export interface CardRollList {
  rows: CardRow[];
  isLoading: boolean;
  isError: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  refetch: () => void;
}

/* `rows` may be a filtered view of `list.rows` (a member removed a moment
 * ago); paging follows the list. */
export function CardRoll({
  list,
  rows = list.rows,
  errorTitle,
  empty,
  endLabel,
}: {
  list: CardRollList;
  rows?: CardRow[];
  errorTitle: string;
  empty: ReactNode;
  endLabel: string;
}) {
  const coverTag = useCardCoverTag().value;
  const listingIds = useMemo(() => rows.map((r) => r.listing_id), [rows]);

  if (list.isLoading) return <RollSkeleton />;
  if (list.isError) {
    return (
      <RollEmpty
        title={errorTitle}
        detail="Spojení nebo databáze teď neodpověděly. Zkuste to znovu."
        action={{ label: 'Zkusit znovu', onClick: list.refetch }}
      />
    );
  }
  if (rows.length === 0 && !list.hasNextPage) return <>{empty}</>;

  return (
    <CardHydrationProvider listingIds={listingIds} renders={{ photos: PHOTOS_PER_CARD }}>
      <div>
        {rows.map((r) => (
          <FeedCard key={r.property_id} r={r} coverTag={coverTag} />
        ))}
      </div>
      <InfiniteSentinel
        onReach={list.fetchNextPage}
        hasNextPage={list.hasNextPage}
        isFetchingNextPage={list.isFetchingNextPage}
        loadedCount={rows.length}
        total={null}
        loadingLabel="Načítám…"
        endLabel={endLabel}
      />
    </CardHydrationProvider>
  );
}
