/* The deal-pipeline board's two reads, as hooks, so the desktop kanban and the
 * phone's stage pages (#1768) sit on ONE cache entry each, with one freshness
 * window: a move on either repaints both, and neither can drift to its own key
 * or staleTime. The writes stay in lib/usePipelineCard (rule #22). */

import { useQuery } from '@tanstack/react-query';

import { fetchPipelineBoard, fetchPipelineStages, pipelineKeys } from './queries';

export const usePipelineStages = () =>
  useQuery({
    queryKey: pipelineKeys.stages,
    queryFn: fetchPipelineStages,
    staleTime: 60_000,
  });

export const usePipelineBoard = () =>
  useQuery({
    queryKey: pipelineKeys.board,
    queryFn: fetchPipelineBoard,
    staleTime: 30_000,
  });
