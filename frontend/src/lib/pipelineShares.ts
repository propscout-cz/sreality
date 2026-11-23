/* The caller's pipeline shares (migration 641) and the active board checked
 * against them — the Pipeline tab's one read of GET /pipeline/shares.
 *
 * The read is never acting (sharing is always the caller's own; under the
 * header the API answers empty lists), so its key carries no acting segment
 * and it is called with no `actAs`.
 *
 * A remembered board that the server no longer lists — the owner revoked it,
 * or this device remembers another sign-in's board — resets to "Můj pipeline"
 * with a toast; a mode the owner changed is taken over (and, being a change of
 * the active board, ends "edit as owner"). */

import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getActiveBoard, resetActiveBoard, setActiveBoard, useActiveBoard, type ActiveBoard } from './acting';
import { listPipelineShares } from './api';
import { pipelineShareKeys } from './queries';
import { pushToast } from './toast';
import type { PipelineShare, PipelineShares, PipelineStage } from './types';

export const BOARD_GONE_MESSAGE = 'Sdílený pipeline už není k dispozici — zobrazuji váš.';

export const usePipelineShares = (enabled = true) =>
  useQuery({
    queryKey: pipelineShareKeys.all,
    queryFn: () => listPipelineShares(),
    staleTime: 60_000,
    enabled,
  });

/** The shared board this device was on is gone: back to my own, and say so. */
export function boardGone(): void {
  if (getActiveBoard() == null) return;
  resetActiveBoard();
  pushToast('info', BOARD_GONE_MESSAGE);
}

/** A shared board that reads no stages is gone (revoked, or never ours to
 * read — every live board has its entry stage): back to my own, with a toast.
 * The desktop board and the phone board both mount it. */
export function useSharedBoardGuard(active: ActiveBoard, stages: readonly PipelineStage[] | undefined): void {
  useEffect(() => {
    if (active != null && stages?.length === 0) boardGone();
  }, [active, stages]);
}

/* Pure, for its test: what the server's lists say about the remembered board. */
export function reconcileActiveBoard(
  shares: PipelineShares,
  active: ActiveBoard,
): 'keep' | 'gone' | { update: PipelineShare } {
  if (active == null) return 'keep';
  const live = shares.shared_with_me.find((s) => s.account_id === active.account_id);
  if (live == null) return 'gone';
  if (live.mode !== active.mode || live.email !== active.email) return { update: live };
  return 'keep';
}

/** The active board, kept honest against the server's lists, and the lists.
 * `always: false` (the property page) reads the lists only while a shared
 * board is active, so a page on my own board makes no extra request. */
export function useBoardSelection({
  always = true,
  enabled = true,
}: { always?: boolean; enabled?: boolean } = {}) {
  const active = useActiveBoard();
  const sharesQ = usePipelineShares(enabled && (always || active != null));
  const shares = sharesQ.data;
  useEffect(() => {
    if (shares == null) return;
    const verdict = reconcileActiveBoard(shares, getActiveBoard());
    if (verdict === 'gone') boardGone();
    else if (verdict !== 'keep') setActiveBoard(verdict.update);
  }, [shares, active]);
  return { active, sharesQ };
}
