/* The phone pipeline's model (#1768): pages in stage order with the closed
 * stages set apart, cards in the board's default order, the Czech day count,
 * and the remembered stage. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  PHONE_PIPELINE_STAGE_KEY,
  cardsWord,
  daysInStage,
  fmtDaysInStage,
  initialStageIndex,
  orderStages,
  readLastStage,
  stagePages,
  writeLastStage,
} from './phonePipeline';
import type { PipelineBoardCard, PipelineStage } from './types';

const stage = (id: number, position: number, extra: Partial<PipelineStage> = {}): PipelineStage => ({
  id,
  key: `s${id}`,
  label: `Fáze ${id}`,
  position,
  color: null,
  is_terminal: false,
  is_entry: false,
  code: null,
  ...extra,
});

/* Czech number and unit run on no-break spaces; the assertions read plain ones. */
const plain = (s: string | null): string | null => (s == null ? s : s.replace(/[\u00a0\u202f]/g, ' '));

const card = (property_id: number, stage_id: number, board_position: number): PipelineBoardCard =>
  ({ property_id, stage_id, board_position }) as PipelineBoardCard;

describe('orderStages / stagePages', () => {
  it('orders the pages by position, id breaking a tie', () => {
    const out = orderStages([stage(4, 3), stage(2, 1), stage(9, 3), stage(1, 2)]);
    expect(out.map((s) => s.id)).toEqual([2, 1, 4, 9]);
  });

  it('draws one separator, before the first closed stage after a live one', () => {
    const pages = stagePages(
      [
        stage(10, 1, { is_entry: true }),
        stage(11, 2),
        stage(12, 3, { is_terminal: true }),
        stage(13, 4, { is_terminal: true }),
      ],
      [],
    );
    expect(pages.map((p) => p.separatorBefore)).toEqual([false, false, true, false]);
  });

  it('sets apart a closed stage wherever a live run ends, and never the first tab', () => {
    const pages = stagePages(
      [
        stage(1, 1, { is_terminal: true }),
        stage(2, 2),
        stage(3, 3, { is_terminal: true }),
      ],
      [],
    );
    expect(pages.map((p) => p.separatorBefore)).toEqual([false, false, true]);
  });

  it('buckets the cards per stage in the board order, dropping a card whose stage is gone', () => {
    const pages = stagePages(
      [stage(1, 1), stage(2, 2)],
      [card(30, 1, 2), card(31, 2, 0), card(32, 1, 0), card(33, 1, 0), card(34, 99, 0)],
    );
    expect(pages[0].cards.map((c) => c.property_id)).toEqual([32, 33, 30]);
    expect(pages[1].cards.map((c) => c.property_id)).toEqual([31]);
    expect(pages.flatMap((p) => p.cards)).toHaveLength(4);
  });
});

describe('cardsWord', () => {
  it('declines karta', () => {
    expect([0, 1, 2, 4, 5, 12, 22].map((n) => plain(cardsWord(n)))).toEqual([
      '0 karet',
      '1 karta',
      '2 karty',
      '4 karty',
      '5 karet',
      '12 karet',
      '22 karet',
    ]);
  });
});

describe('daysInStage / fmtDaysInStage', () => {
  /* 10:00 in Prague (CEST, UTC+2). */
  const now = new Date('2026-10-12T08:00:00Z');

  it('counts Prague calendar days, not 24-hour spans', () => {
    /* 23:30 Prague the evening before: one day, though only 10.5 hours ago. */
    expect(daysInStage('2026-10-11T21:30:00Z', now)).toBe(1);
    /* 00:30 Prague today, still the 11th in UTC. */
    expect(daysInStage('2026-10-11T22:30:00Z', now)).toBe(0);
    expect(daysInStage('2026-09-30T12:00:00Z', now)).toBe(12);
  });

  it('crosses the end of summer time without losing a day', () => {
    expect(daysInStage('2026-10-24T10:00:00Z', new Date('2026-10-26T10:00:00Z'))).toBe(2);
  });

  it('says nothing for a missing, broken or future stamp', () => {
    expect(daysInStage(null, now)).toBeNull();
    expect(daysInStage('not a date', now)).toBeNull();
    expect(daysInStage('2026-10-14T08:00:00Z', now)).toBeNull();
  });

  it('declines den', () => {
    expect(plain(fmtDaysInStage(0))).toBe('ve fázi od dneška');
    expect(plain(fmtDaysInStage(1))).toBe('1 den ve fázi');
    expect(plain(fmtDaysInStage(3))).toBe('3 dny ve fázi');
    expect(plain(fmtDaysInStage(5))).toBe('5 dní ve fázi');
    expect(plain(fmtDaysInStage(1200))).toBe('1 200 dní ve fázi');
    expect(fmtDaysInStage(null)).toBeNull();
  });
});

describe('the remembered stage', () => {
  beforeEach(() => localStorage.clear());

  const pages = stagePages([stage(1, 1), stage(2, 2, { is_entry: true }), stage(3, 3)], []);

  it('opens on the remembered stage while it exists, else on the entry stage', () => {
    expect(initialStageIndex(pages, 3)).toBe(2);
    expect(initialStageIndex(pages, 77)).toBe(1);
    expect(initialStageIndex(pages, null)).toBe(1);
    expect(initialStageIndex(stagePages([stage(5, 1), stage(6, 2)], []), null)).toBe(0);
  });

  it('round-trips through storage by stage id', () => {
    expect(readLastStage()).toBeNull();
    writeLastStage(42);
    expect(localStorage.getItem(PHONE_PIPELINE_STAGE_KEY)).toBe('42');
    expect(readLastStage()).toBe(42);
    localStorage.setItem(PHONE_PIPELINE_STAGE_KEY, 'junk');
    expect(readLastStage()).toBeNull();
  });

  it('survives storage that throws', () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(readLastStage()).toBeNull();
    expect(() => writeLastStage(1)).not.toThrow();
    get.mockRestore();
    set.mockRestore();
  });
});
