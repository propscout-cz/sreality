/* The pieces every photo roll on the phone shares (#1762): the loading
 * skeleton, the dashed empty/error block, and the plain undivided roll of
 * FeedCards that a collection's members and a broker's ads are drawn as. The
 * feed keeps its own day-cut roll (pages/Feed.tsx) but paints its loading and
 * empty states from here, so the four screens wait and fail in one voice. */

import { useMemo, type ReactNode } from 'react';

import InfiniteSentinel from '@/components/InfiniteSentinel';
import { useCardCoverTag } from '@/lib/browseLayout';
import { CardHydrationProvider } from '@/lib/hydration';
import type { CardRow } from '@/lib/queries';

import FeedCard from './FeedCard';

/* Every photo the Browse carousel would hold, so the cover choice is the same and
 * the hydration cache is shared with Browse (BrowseExperience's perId). */
export const PHOTOS_PER_CARD = 50;

/* Two card-shaped placeholders: the photo frame and two text lines. */
export function RollSkeleton({ count = 2 }: { count?: number }) {
  return (
    <>
      <p className="sr-only" role="status">
        Načítám…
      </p>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} aria-hidden className="border-t border-[var(--color-rule)]">
          <div className="aspect-[4/3] animate-pulse bg-[var(--color-inset)] motion-reduce:animate-none" />
          <div className="flex flex-col gap-2 px-4 pb-4 pt-3">
            <div className="h-5 w-40 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
            <div className="h-4 w-56 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          </div>
        </div>
      ))}
    </>
  );
}

/* The dashed block that stands in for a roll with nothing in it, or one that
 * failed to load: a Fraunces line, a muted explanation, at most one action. */
export function RollEmpty({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="mx-4 mb-4 mt-1 flex flex-col items-start gap-2 rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] px-4 py-6">
      <p className="font-display text-[17px] text-[var(--color-ink)]">{title}</p>
      <p className="text-[13px] text-[var(--color-ink-3)]">{detail}</p>
      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className="inline-flex h-9 items-center rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-3 text-[13px] text-[var(--color-ink-2)] active:bg-[var(--color-inset)]"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}

export interface CardRollList {
  rows: CardRow[];
  isLoading: boolean;
  isError: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  refetch: () => void;
}

/* A roll of FeedCards over one keyset-paged Browse read, undivided: the
 * collection's members, the broker's ads. `rows` may be a filtered view of
 * `list.rows` (a member removed a moment ago); paging follows the list. */
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
