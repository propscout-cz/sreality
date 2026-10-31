/* What a deal-pipeline write does to the client caches — one definition, used
 * by every surface that writes a card (rule #22).
 *
 * Two caches hold "where is this property in the pipeline": `members` (the map
 * every funnel — Browse cards AND the listing-detail toggle, since W3 —
 * renders from) and `board` (the kanban array, which also carries the
 * STRUCTURAL display fields `members` doesn't: price, place, area, is_active).
 * NOT the photo, and not the broker: those are decorations, they live in the
 * ['hydration', …] namespace keyed on listing_id, and putting either back on
 * the board row is precisely the coupling W1 shipped to remove (see
 * types.ts's PipelineBoardCard, which carries the standing warning). A write
 * changes the same
 * fact in both, and each surface used to patch only the one it could see: the
 * board's drag invalidated `board` alone, so after moving a card on the
 * kanban every Browse funnel kept painting the OLD stage badge until the
 * members query went stale on its own. (A THIRD cache, one entry per property
 * for the listing-detail toggle alone, existed until W3 — it was pure
 * duplication of what `members` already held for that property, and the two
 * had already drifted out of sync once on which columns they selected.)
 *
 * Pure patch builders for useOptimisticWrite, which holds the same two caches
 * it patches and rolls them back from `onSettled` (lib/usePipelineCard). What a
 * card write re-reads is lib/curationSync's: the tables it writes,
 * `property_pipeline` and `property_dismissals` (a card at a live stage lifts the
 * caller's dismissal, api/dismissals.py).
 */

import type { QueryClient } from '@tanstack/react-query';

import { actingKey } from '@/lib/acting';
import { pipelineKeys, type PipelineMembers } from '@/lib/queries';
import { cachePatch, type CachePatch } from '@/lib/revalidate';
import type { PipelineBoardCard, PipelineStage } from '@/lib/types';

/* `actAs` (lib/acting): the owner's board entries while acting, else the caller's. */
const MEMBERS = (actAs: string | null) => ({ queryKey: actingKey(actAs, pipelineKeys.members), exact: true });
const BOARD = (actAs: string | null) => ({ queryKey: actingKey(actAs, pipelineKeys.board), exact: true });

/* Show the property as sitting at `stage` — used for both "bookmarked into the
 * entry stage" and "moved to another stage".
 *
 * The board array is patched in place only when it already holds the card: a
 * board entry carries the property's display fields (price, place, area) that
 * a funnel click has no way to synthesise, so a NEW card reaches the board via
 * the revalidation instead of as a half-built row. */
export function placeCard(
  property_id: number,
  stage: PipelineStage,
  actAs: string | null = null,
): CachePatch[] {
  return [
    cachePatch<PipelineMembers>(MEMBERS(actAs), (prev) => {
      if (!prev) return prev;
      const next = new Map(prev);
      next.set(property_id, {
        property_id,
        stage_id: stage.id,
        stage_label: stage.label,
        stage_color: stage.color,
        stage_code: stage.code ?? null,
        stage_position: stage.position,
        is_terminal: stage.is_terminal,
      });
      return next;
    }),
    cachePatch<PipelineBoardCard[]>(BOARD(actAs), (prev) =>
      prev?.map((c) => (c.property_id === property_id ? { ...c, stage_id: stage.id } : c)),
    ),
  ];
}

/* Show the property as off the board. */
export function dropCard(property_id: number, actAs: string | null = null): CachePatch[] {
  return [
    cachePatch<PipelineMembers>(MEMBERS(actAs), (prev) => {
      if (!prev) return prev;
      const next = new Map(prev);
      next.delete(property_id);
      return next;
    }),
    cachePatch<PipelineBoardCard[]>(BOARD(actAs), (prev) =>
      prev?.filter((c) => c.property_id !== property_id),
    ),
  ];
}

/* The stage a write lands on, read from the shared stage list already in cache.
 * Returns null when the list has not loaded yet — the caller then skips the
 * optimistic patch and lets the revalidation paint the result. */
export function cachedStage(
  qc: QueryClient,
  match: number | 'entry',
  actAs: string | null = null,
): PipelineStage | null {
  const stages = qc.getQueryData<PipelineStage[]>(actingKey(actAs, pipelineKeys.stages)) ?? [];
  const found =
    match === 'entry'
      ? stages.find((s) => s.is_entry)
      : stages.find((s) => s.id === match);
  return found ?? null;
}
