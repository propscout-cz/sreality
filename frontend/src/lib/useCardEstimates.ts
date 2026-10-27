/* The on-card rent estimate of a card grid (ListingCards' estimate corner):
 * the latest run per card, the ids whose submit is in flight, the API's refusal
 * sentence per card, and the submit: Browse's, lifted out of BrowseExperience
 * so the collection page's grid behaves the same (ruling P32). */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';

import { createEstimation, latestEstimationsByListing } from '@/lib/api';
import type { CardRow } from '@/lib/queries';
import type { ListingEstimate } from '@/lib/types';

export interface CardEstimates {
  estimates: Record<number, ListingEstimate>;
  estimatingIds: ReadonlySet<number>;
  estimateRefusals: Readonly<Record<number, string>>;
  onEstimate: (srealityId: number) => void;
}

export function useCardEstimates({
  rows,
  enabled,
  resetKey,
}: {
  rows: ReadonlyArray<CardRow>;
  /* Reads (the latest run, the poll) only while true; a submit always runs. */
  enabled: boolean;
  /* The cohort (filters + sort): a new one starts from nothing. */
  resetKey: string;
}): CardEstimates {
  /* The estimation backend runs by sreality_id, so a card without one can't be
   * estimated: the nulls never reach the batch read or the state map. */
  const cardIds = useMemo(
    () => rows.map((r) => r.sreality_id).filter((x): x is number => x != null),
    [rows],
  );
  const [estimatingIds, setEstimatingIds] = useState<ReadonlySet<number>>(() => new Set());
  const [estimates, setEstimates] = useState<Record<number, ListingEstimate>>({});
  /* A refused submit (a 422 for an ad the API cannot place) makes no run, so
   * nothing in `estimates` would say it failed: the sentence is kept per card,
   * until the next attempt. */
  const [estimateRefusals, setEstimateRefusals] = useState<Record<number, string>>({});
  const requestedRef = useRef<Set<number>>(new Set());

  useEffect(() => {
    setEstimates({});
    setEstimateRefusals({});
    requestedRef.current = new Set();
  }, [resetKey]);

  useEffect(() => {
    if (!enabled) return;
    const requested = requestedRef.current;
    const fresh = cardIds.filter((id) => !requested.has(id));
    if (fresh.length === 0) return;
    fresh.forEach((id) => requested.add(id));
    let cancelled = false;
    let answered = false;
    latestEstimationsByListing(fresh)
      .then((map) => {
        answered = true;
        if (!cancelled) setEstimates((prev) => ({ ...prev, ...map }));
      })
      .catch(() => {
        answered = true;
      });
    return () => {
      cancelled = true;
      /* A read dropped before it answered reached no card, so its ids are asked
       * again: a new cohort first re-runs this on the previous rows (the list's
       * placeholder), then on its own rows, which can share those ids. */
      if (!answered) fresh.forEach((id) => requested.delete(id));
    };
  }, [cardIds, enabled, resetKey]);

  const pendingIds = useMemo(
    () => [
      ...estimatingIds,
      ...Object.values(estimates)
        .filter((e) => e.status === 'pending' || e.status === 'running')
        .map((e) => e.sreality_id),
    ],
    [estimatingIds, estimates],
  );
  useEffect(() => {
    if (!enabled || pendingIds.length === 0) return;
    const timer = setInterval(() => {
      latestEstimationsByListing(pendingIds)
        .then((map) => setEstimates((prev) => ({ ...prev, ...map })))
        .catch(() => {});
    }, 4000);
    return () => clearInterval(timer);
  }, [enabled, pendingIds]);

  const mut = useMutation({
    mutationFn: (srealityId: number) =>
      createEstimation({
        sreality_id: srealityId,
        source: 'ui',
        mode: 'agent',
        provider: 'anthropic',
        estimate_kind: 'rent',
        lifecycle: 'active',
      }),
    onMutate: (srealityId) => {
      setEstimatingIds((prev) => new Set(prev).add(srealityId));
      setEstimateRefusals((prev) => {
        if (!(srealityId in prev)) return prev;
        const next = { ...prev };
        delete next[srealityId];
        return next;
      });
    },
    onError: (err, srealityId) => {
      setEstimateRefusals((prev) => ({
        ...prev,
        [srealityId]: err instanceof Error ? err.message : 'Odhad se nepodařilo spustit',
      }));
    },
    onSuccess: (run, srealityId) => {
      setEstimates((prev) => ({
        ...prev,
        [srealityId]: {
          sreality_id: srealityId,
          run_id: run.id,
          status: run.status,
          estimate_kind: run.estimate_kind,
          gross_yield_pct: run.gross_yield_pct,
          estimated_monthly_rent_czk: run.estimated_monthly_rent_czk,
          created_at: run.created_at,
        },
      }));
    },
    onSettled: (_run, _err, srealityId) => {
      setEstimatingIds((prev) => {
        const next = new Set(prev);
        next.delete(srealityId);
        return next;
      });
    },
  });
  return { estimates, estimatingIds, estimateRefusals, onEstimate: mut.mutate };
}
