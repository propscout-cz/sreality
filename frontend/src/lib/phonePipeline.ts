/* The phone pipeline's model (#1768, Phone app map #1759 W3): the desktop
 * board's one read laid out as one page per stage, swiped (#1760 Q8).
 *
 * Pure, so the page order, the closed-stage separator and the day count are
 * tested without a browser. The cards and stages are the board's own
 * (`pipeline_board_public`, `pipeline_stages_public`), ordered within a stage by
 * the board's default sort: the phone offers no sort and no filter, so it shows
 * what the desktop board shows before anyone touches its controls. */

import { pragueDayKey } from './feed';
import { czPlural, fmtTomDays } from './format';
import { DEFAULT_PIPELINE_SORT, sortPipelineCards } from './pipelineSort';
import type { PipelineBoardCard, PipelineStage } from './types';

export interface StagePage {
  stage: PipelineStage;
  cards: PipelineBoardCard[];
  /* A hairline in the strip before this tab: the first of a run of closed
   * stages after a live one, so the outcomes read apart from the steps. */
  separatorBefore: boolean;
}

/* Stages in `position` order (the board's), id as the tiebreak so two stages
 * at one position never swap places between reads. */
export function orderStages(stages: ReadonlyArray<PipelineStage>): PipelineStage[] {
  return [...stages].sort((a, b) => a.position - b.position || a.id - b.id);
}

export function stagePages(
  stages: ReadonlyArray<PipelineStage>,
  cards: ReadonlyArray<PipelineBoardCard>,
): StagePage[] {
  const byStage = new Map<number, PipelineBoardCard[]>();
  for (const c of cards) {
    const bucket = byStage.get(c.stage_id);
    if (bucket) bucket.push(c);
    else byStage.set(c.stage_id, [c]);
  }
  const ordered = orderStages(stages);
  return ordered.map((stage, i) => ({
    stage,
    cards: sortPipelineCards(byStage.get(stage.id) ?? [], DEFAULT_PIPELINE_SORT),
    separatorBefore: stage.is_terminal && i > 0 && !ordered[i - 1].is_terminal,
  }));
}

/* "1 karta", "3 karty", "12 karet". */
export const cardsWord = (n: number): string =>
  `${n} ${czPlural(n, 'karta', 'karty', 'karet')}`;

const dayNumber = (dayKey: string): number => {
  const [y, m, d] = dayKey.split('-').map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
};

/* Whole Prague calendar days since the card entered its stage: 0 = today, so
 * "today" means the operator's Czech day, not the last 24 hours. Null on a
 * missing or unparseable stamp, or one in the future (a clock skew). */
export function daysInStage(enteredAt: string | null | undefined, now: Date | number = Date.now()): number | null {
  if (!enteredAt) return null;
  const at = new Date(enteredAt);
  if (Number.isNaN(at.getTime())) return null;
  const days = dayNumber(pragueDayKey(now)) - dayNumber(pragueDayKey(at));
  return days < 0 ? null : days;
}

/* "ve fázi od dneška", "1 den ve fázi", "3 dny ve fázi", "12 dní ve fázi". */
export function fmtDaysInStage(days: number | null): string | null {
  if (days == null || days < 0) return null;
  return days === 0 ? 've fázi od dneška' : `${fmtTomDays(days)} ve fázi`;
}

/* The stage page this device last looked at, by stage id (stages are
 * reordered on the desktop; an index would land on the wrong one), per board:
 * my own under the plain key, a board shared with me (lib/acting) under the
 * key plus its owner's account. */
export const PHONE_PIPELINE_STAGE_KEY = 'sreality.phone.pipelineStage';

const lastStageKey = (board: string | null = null): string =>
  board ? `${PHONE_PIPELINE_STAGE_KEY}:${board}` : PHONE_PIPELINE_STAGE_KEY;

export function readLastStage(board: string | null = null): number | null {
  try {
    const raw = localStorage.getItem(lastStageKey(board));
    const id = raw == null ? NaN : Number(raw);
    return Number.isInteger(id) ? id : null;
  } catch {
    /* Storage can throw (private-mode lockdown): start on the entry stage. */
    return null;
  }
}

export function writeLastStage(stageId: number, board: string | null = null): void {
  try {
    localStorage.setItem(lastStageKey(board), String(stageId));
  } catch {
    /* ignore */
  }
}

/* The page to open on: the remembered stage while it still exists, else the
 * entry stage (where a new card lands), else the first. */
export function initialStageIndex(pages: ReadonlyArray<StagePage>, remembered: number | null): number {
  const at = remembered == null ? -1 : pages.findIndex((p) => p.stage.id === remembered);
  if (at >= 0) return at;
  return Math.max(0, pages.findIndex((p) => p.stage.is_entry));
}
