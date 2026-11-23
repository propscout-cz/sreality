/* viewingStageOffer — when a viewing date offers the viewing stage (operator ruling 2026-11-06). */

import { describe, expect, it } from 'vitest';

import type { PipelineStage } from './types';
import { viewingStageOffer } from './viewingStage';

const stage = (id: number, position: number, over: Partial<PipelineStage> = {}): PipelineStage => ({
  id, key: `s${id}`, label: `${position}. Stage`, position, color: null,
  is_terminal: false, is_entry: position === 1, code: null, role: null, ...over,
});

/* The default board's shape: review, call, VISIT, negotiations, then two closed stages. */
const BOARD = [
  stage(1, 1),
  stage(2, 2),
  stage(3, 3, { role: 'viewing', label: '3. For Visit' }),
  stage(4, 4),
  stage(8, 8, { is_terminal: true }),
  stage(9, 9, { is_terminal: true }),
];

describe('viewingStageOffer', () => {
  it('offers to add a property that is not on the board', () => {
    expect(viewingStageOffer(BOARD, null)).toEqual({ kind: 'add', stage: BOARD[2], from: null });
  });

  it('offers to move a card from an earlier open stage, or from a closed one', () => {
    expect(viewingStageOffer(BOARD, { stage_id: 1 })).toMatchObject({ kind: 'move', stage: { id: 3 }, from: { id: 1 } });
    expect(viewingStageOffer(BOARD, { stage_id: 2 })).toMatchObject({ kind: 'move', from: { id: 2 } });
    expect(viewingStageOffer(BOARD, { stage_id: 9 })).toMatchObject({ kind: 'move', from: { id: 9 } });
  });

  it('stays silent at the viewing stage and at a later open stage', () => {
    expect(viewingStageOffer(BOARD, { stage_id: 3 })).toBeNull();
    expect(viewingStageOffer(BOARD, { stage_id: 4 })).toBeNull();
  });

  it('stays silent with no viewing stage marked, or a board it does not know', () => {
    expect(viewingStageOffer(BOARD.map((s) => ({ ...s, role: null })), null)).toBeNull();
    expect(viewingStageOffer(undefined, null)).toBeNull();
    expect(viewingStageOffer(BOARD, undefined)).toBeNull();
    /* A card on a stage the list does not hold (retired, or the list is stale). */
    expect(viewingStageOffer(BOARD, { stage_id: 77 })).toBeNull();
  });
});
