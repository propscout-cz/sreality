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

import { addPipelineCard, movePipelineCard, removePipelineCard } from '@/lib/api';
import { settlePatches, settleRereads } from '@/lib/curationSettle';
import { dismissalKeys } from '@/lib/queries';
import {
  cachedStage,
  dropCard,
  PIPELINE_REVALIDATE,
  placeCard,
} from '@/lib/pipelineCache';
import type { PipelineStage } from '@/lib/types';
import { useOptimisticWrite } from '@/lib/useOptimisticWrite';

/* What planMove (pages/Pipeline) resolves a drag into. */
export interface PipelineMove {
  propertyId: number;
  stageId: number;
}

/* A write lands on the members map, then settles the Browse reads it moved
 * (lib/curationSettle, ruling 9). With no cached stage nothing is painted, so
 * there is nothing to judge from and every Browse read re-reads instead. */
const land = (qc: QueryClient, propertyId: number, stage: PipelineStage | null) =>
  stage ? [...placeCard(propertyId, stage), ...settlePatches(qc, propertyId, 'pipeline')] : [];
const reread = (qc: QueryClient, propertyId: number, failed: boolean) => [
  ...PIPELINE_REVALIDATE,
  ...settleRereads(qc, propertyId, 'pipeline', failed),
];
/* A card at a live stage lifts the caller's dismissal (api/dismissals.py), so
 * a property this device knows as dismissed may re-enter any list that hides
 * dismissals: everything re-reads, as on a restore. */
const lifts = (qc: QueryClient, propertyId: number, stage: PipelineStage | null) =>
  stage != null && !stage.is_terminal && qc.getQueryData<boolean>(dismissalKeys.state(propertyId)) === true;

export function usePipelineCard() {
  const add = useOptimisticWrite({
    mutationKey: ['write', 'pipeline', 'add'],
    mutationFn: (property_id: number) => addPipelineCard(property_id),
    // The entry stage IS the bookmark (rule #22). Unknown until the stage list
    // has loaded — then nothing is painted and the funnel fills when the
    // revalidation lands.
    patch: (property_id, qc) => land(qc, property_id, cachedStage(qc, 'entry')),
    revalidate: (property_id, failed, qc) => {
      const stage = cachedStage(qc, 'entry');
      return reread(qc, property_id, failed || stage == null || lifts(qc, property_id, stage));
    },
    pendingKey: (property_id) => property_id,
  });

  const remove = useOptimisticWrite({
    mutationKey: ['write', 'pipeline', 'remove'],
    mutationFn: (property_id: number) => removePipelineCard(property_id),
    patch: (property_id, qc) => [...dropCard(property_id), ...settlePatches(qc, property_id, 'pipeline')],
    revalidate: (property_id, failed, qc) => reread(qc, property_id, failed),
    pendingKey: (property_id) => property_id,
  });

  const move = useOptimisticWrite({
    mutationKey: ['write', 'pipeline', 'move'],
    mutationFn: ({ propertyId, stageId }: PipelineMove) => movePipelineCard(propertyId, stageId),
    patch: ({ propertyId, stageId }, qc) => land(qc, propertyId, cachedStage(qc, stageId)),
    revalidate: ({ propertyId, stageId }, failed, qc) => {
      const stage = cachedStage(qc, stageId);
      return reread(qc, propertyId, failed || stage == null || lifts(qc, propertyId, stage));
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
