/* The deal-pipeline's three reads, as hooks, so the desktop kanban, the phone's
 * stage pages (#1768) and every funnel sit on ONE cache entry each, with one
 * freshness window: a move on either repaints both, and neither can drift to
 * its own key or staleTime. The writes stay in lib/usePipelineCard (rule #22).
 *
 * Inside <ActingAs> (lib/acting) each reads the OWNER's board under the owner's
 * key; the three views are GET reads, so a read share sees them too. */

import { useQuery } from '@tanstack/react-query';

import { actingKey, useActAs } from './acting';
import {
  fetchPipelineBoard,
  fetchPipelineMembers,
  fetchPipelineStages,
  pipelineKeys,
} from './queries';

export const usePipelineStages = () => {
  const actAs = useActAs();
  return useQuery({
    queryKey: actingKey(actAs, pipelineKeys.stages),
    queryFn: ({ signal }) => fetchPipelineStages({ signal, actAs }),
    staleTime: 60_000,
  });
};

export const usePipelineBoard = () => {
  const actAs = useActAs();
  return useQuery({
    queryKey: actingKey(actAs, pipelineKeys.board),
    queryFn: ({ signal }) => fetchPipelineBoard({ signal, actAs }),
    staleTime: 30_000,
  });
};

/* Every card, by property: the funnels' and the stage menu's one read. */
export const usePipelineMembers = ({ enabled = true }: { enabled?: boolean } = {}) => {
  const actAs = useActAs();
  return useQuery({
    queryKey: actingKey(actAs, pipelineKeys.members),
    queryFn: ({ signal }) => fetchPipelineMembers({ signal, actAs }),
    staleTime: 30_000,
    enabled,
  });
};
