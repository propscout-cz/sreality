/* Whether adding a viewing date to a property should offer to put its card at the
 * board's viewing stage (operator 2026-11-06, #2429): the stage the owner marked with
 * the `viewing` role (migration 734), whatever its name.
 *
 * Offered when the property is not on the board (add it there), at an earlier open
 * stage, or closed (move it, which re-opens the deal). Silent at the viewing stage,
 * at a later open stage (a second viewing never drags a deal in negotiation back:
 * operator ruling 2026-11-06), with no viewing stage marked, and whenever the board
 * is not known: an offer is never built on a read that has not answered. */

import type { PipelineMembership } from './queries';
import type { PipelineStage } from './types';

export type ViewingStageOffer = { kind: 'add' | 'move'; stage: PipelineStage; from: PipelineStage | null };

export function viewingStageOffer(
  stages: readonly PipelineStage[] | undefined,
  /* The property's card; null = not on the board; undefined = the board is not known. */
  card: Pick<PipelineMembership, 'stage_id'> | null | undefined,
): ViewingStageOffer | null {
  const target = stages?.find((s) => s.role === 'viewing');
  if (!stages || !target || card === undefined) return null;
  if (card === null) return { kind: 'add', stage: target, from: null };
  if (card.stage_id === target.id) return null;
  const from = stages.find((s) => s.id === card.stage_id);
  if (!from) return null;
  if (!from.is_terminal && from.position > target.position) return null;
  return { kind: 'move', stage: target, from };
}
