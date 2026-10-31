/* The one place a deal-pipeline card is written from (rule #22) — the Browse
 * funnels, the listing header, the stage menu they share, AND the kanban's
 * drag and trash.
 *
 * Add / remove / move were duplicated across those surfaces, each with its own
 * idea of which caches to invalidate (the board forgot the members set, the
 * cards forgot the board). They are one hook now: same audited API calls, one
 * cache patch (`lib/pipelineCache.ts`), one write policy
 * (`lib/useOptimisticWrite.ts`), so "what a pipeline write does to the client
 * state" has one answer whether the operator clicked a funnel or dragged a card.
 * The property id travels with each call, so one hook instance serves a whole
 * board.
 *
 * Every write is optimistic. A funnel click has to repaint before the round
 * trip to Frankfurt or the menu closes onto a stale badge; on failure the
 * rollback fires from `onSettled` (not `onError`, which would silence the app's
 * global error toast) and the revalidation reconciles with the server either
 * way. `pending(id)` is per property and spans hook instances: the funnel stays
 * busy while the menu it opened is still writing.
 */

import type { QueryClient } from '@tanstack/react-query';

import { actingKey, actOpts, useActAs } from '@/lib/acting';
import { addPipelineCard, movePipelineCard, removePipelineCard } from '@/lib/api';
import { settlePatches, settleRereads } from '@/lib/curationSettle';
import { tableReads } from '@/lib/curationSync';
import { dismissalKeys } from '@/lib/queries';
import { cachedStage, dropCard, placeCard } from '@/lib/pipelineCache';
import type { PipelineStage } from '@/lib/types';
import { useOptimisticWrite } from '@/lib/useOptimisticWrite';

/* What planMove (pages/Pipeline) resolves a drag into. */
export interface PipelineMove {
  propertyId: number;
  stageId: number;
}

/* A write lands on the members map, then settles the Browse reads it moved
 * (lib/curationSettle, ruling 9). With no cached stage nothing is painted, so
 * there is nothing to judge from and every Browse read re-reads instead.
 * Acting as an owner (`actAs`, lib/acting) a write moves the owner's board
 * entries only: Browse never acts, so it neither settles nor re-reads. */
const land = (qc: QueryClient, propertyId: number, stage: PipelineStage | null, actAs: string | null) =>
  stage ? [...placeCard(propertyId, stage, actAs), ...settlePatches(qc, propertyId, 'pipeline', actAs)] : [];
/* The tables a card write touches: the card, and the dismissal a live stage lifts. */
const reread = (qc: QueryClient, propertyId: number, failed: boolean, actAs: string | null) => [
  ...tableReads(['property_pipeline', 'property_dismissals'], actAs),
  ...settleRereads(qc, propertyId, 'pipeline', failed, actAs),
];
/* A card at a live stage lifts the caller's dismissal (api/dismissals.py), so
 * a property this device knows as dismissed may re-enter any list that hides
 * dismissals: everything re-reads, as on a restore. */
const lifts = (qc: QueryClient, propertyId: number, stage: PipelineStage | null, actAs: string | null) =>
  stage != null &&
  !stage.is_terminal &&
  qc.getQueryData<boolean>(actingKey(actAs, dismissalKeys.state(propertyId))) === true;

/* `onAddError` ONLY for a surface that shows a failed add itself (the new-property
 * dialog's inline block): declaring it silences the global toast for this
 * instance's adds (lib/useOptimisticWrite). */
export function usePipelineCard({ onAddError }: { onAddError?: (error: Error) => void } = {}) {
  /* The mutation keys carry the acting segment too, so the pending index of the
   * owner's board and the caller's own never answer for each other. */
  const actAs = useActAs();
  const add = useOptimisticWrite({
    mutationKey: actingKey(actAs, ['write', 'pipeline', 'add']),
    mutationFn: (property_id: number) => addPipelineCard(property_id, ...actOpts(actAs)),
    onError: onAddError,
    // The entry stage IS the bookmark (rule #22). Unknown until the stage list
    // has loaded — then nothing is painted and the funnel fills when the
    // revalidation lands.
    patch: (property_id, qc) => land(qc, property_id, cachedStage(qc, 'entry', actAs), actAs),
    revalidate: (property_id, failed, qc) => {
      const stage = cachedStage(qc, 'entry', actAs);
      return reread(qc, property_id, failed || stage == null || lifts(qc, property_id, stage, actAs), actAs);
    },
    pendingKey: (property_id) => property_id,
  });

  const remove = useOptimisticWrite({
    mutationKey: actingKey(actAs, ['write', 'pipeline', 'remove']),
    mutationFn: (property_id: number) => removePipelineCard(property_id, ...actOpts(actAs)),
    patch: (property_id, qc) => [
      ...dropCard(property_id, actAs),
      ...settlePatches(qc, property_id, 'pipeline', actAs),
    ],
    revalidate: (property_id, failed, qc) => reread(qc, property_id, failed, actAs),
    pendingKey: (property_id) => property_id,
  });

  const move = useOptimisticWrite({
    mutationKey: actingKey(actAs, ['write', 'pipeline', 'move']),
    mutationFn: ({ propertyId, stageId }: PipelineMove) =>
      /* Not `...actOpts`: the option sits behind the positional board_position,
       * and outside a provider the call stays the two-argument call it was. */
      actAs
        ? movePipelineCard(propertyId, stageId, undefined, { actAs })
        : movePipelineCard(propertyId, stageId),
    patch: ({ propertyId, stageId }, qc) => land(qc, propertyId, cachedStage(qc, stageId, actAs), actAs),
    revalidate: ({ propertyId, stageId }, failed, qc) => {
      const stage = cachedStage(qc, stageId, actAs);
      return reread(qc, propertyId, failed || stage == null || lifts(qc, propertyId, stage, actAs), actAs);
    },
    pendingKey: ({ propertyId }) => propertyId,
  });

  return {
    add,
    remove,
    move,
    pending: (property_id: number) =>
      add.pendingFor(property_id) || remove.pendingFor(property_id) || move.pendingFor(property_id),
  };
}
