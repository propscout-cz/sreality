/* lib/viewings — a viewing is a note with a date (migration 666), entered and
 * read on the Prague wall clock whatever zone the device or the test runner is in. */

import { describe, expect, it } from 'vitest';

import type { Note } from './types';
import {
  byDay,
  dayTitle,
  defaultSlot,
  neighbours,
  relativeDay,
  scoped,
  sectionOrder,
  shortDay,
  slotInstant,
  slotIsComplete,
  viewingSlot,
  viewingWhen,
} from './viewings';

const note = (id: number, viewing_at: string | null, created_at = '2026-10-01T00:00:00Z'): Note => ({
  id, property_id: 40 + id, body: '', created_at, updated_at: null, shared: false, owner_email: null, viewing_at,
});

describe('the Prague wall clock', () => {
  it('turns a slot into its instant across both offsets and the spring gap', () => {
    expect(slotInstant({ day: '2026-01-15', time: '09:00' })).toBe('2026-01-15T08:00:00.000Z');
    expect(slotInstant({ day: '2026-07-15', time: '09:00' })).toBe('2026-07-15T07:00:00.000Z');
    // 2026-03-29: 02:00 → 03:00; 03:30 is already summer time.
    expect(slotInstant({ day: '2026-03-29', time: '03:30' })).toBe('2026-03-29T01:30:00.000Z');
    expect(slotInstant({ day: '2026-10-25', time: '12:00' })).toBe('2026-10-25T11:00:00.000Z');
  });

  it('reads an instant back as the slot it was entered as', () => {
    expect(viewingSlot('2026-10-24T12:00:00+00:00')).toEqual({ day: '2026-10-24', time: '14:00' });
    expect(viewingSlot('2026-12-31T23:30:00Z')).toEqual({ day: '2027-01-01', time: '00:30' });
    expect(viewingSlot(slotInstant({ day: '2026-11-03', time: '08:05' }))).toEqual({ day: '2026-11-03', time: '08:05' });
  });

  it('guesses the next full hour, or tomorrow morning once the evening is late', () => {
    expect(defaultSlot(new Date('2026-10-23T10:20:00Z'))).toEqual({ day: '2026-10-23', time: '13:00' });
    expect(defaultSlot(new Date('2026-10-23T19:30:00Z'))).toEqual({ day: '2026-10-24', time: '10:00' });
  });

  it('knows an unfinished slot from a whole one', () => {
    expect(slotIsComplete({ day: '2026-10-24', time: '14:00' })).toBe(true);
    expect(slotIsComplete({ day: '', time: '14:00' })).toBe(false);
    expect(slotIsComplete({ day: '2026-10-24', time: '' })).toBe(false);
  });
});

describe('the words', () => {
  const now = new Date('2026-10-23T08:00:00Z'); // Friday 10:00 in Prague

  it('says the day relative to today, in Czech', () => {
    expect(relativeDay('2026-10-23T18:00:00Z', now)).toBe('dnes');
    expect(relativeDay('2026-10-24T08:00:00Z', now)).toBe('zítra');
    expect(relativeDay('2026-10-26T08:00:00Z', now)).toBe('za 3 dny');
    expect(relativeDay('2026-10-30T08:00:00Z', now)).toBe('za 7 dní');
    expect(relativeDay('2026-10-22T08:00:00Z', now)).toBe('včera');
    expect(relativeDay('2026-10-18T08:00:00Z', now)).toBe('před 5 dny');
  });

  it('names a day short, with its year only when it is not this one', () => {
    expect(shortDay('2026-10-27', '2026-10-23')).toBe('út 27. 10.');
    expect(shortDay('2027-01-05', '2026-10-23')).toBe('út 5. 1. 2027');
    expect(viewingWhen('2026-10-24T12:00:00Z', now)).toBe('so 24. 10. v 14:00');
  });

  it('titles the tab day relatively near today, else by its weekday and date', () => {
    expect(dayTitle('2026-10-23', '2026-10-23')).toBe('Dnes');
    expect(dayTitle('2026-10-24', '2026-10-23')).toBe('Zítra');
    expect(dayTitle('2026-10-22', '2026-10-23')).toBe('Včera');
    expect(dayTitle('2026-10-27', '2026-10-23')).toBe('úterý 27. října');
  });
});

describe('the order and the days', () => {
  const now = new Date('2026-10-23T08:00:00Z');

  it('lists a property\'s upcoming viewings first, nearest on top, the rest as they came', () => {
    const plain = note(1, null, '2026-10-22T00:00:00Z');
    const past = note(2, '2026-10-20T08:00:00Z', '2026-10-21T00:00:00Z');
    const later = note(3, '2026-10-30T08:00:00Z', '2026-10-20T00:00:00Z');
    const soon = note(4, '2026-10-23T06:00:00Z', '2026-10-19T00:00:00Z'); // this morning: still today
    expect(sectionOrder([plain, past, later, soon], now).map((n) => n.id)).toEqual([4, 3, 1, 2]);
  });

  it('scopes to today on, groups by Prague day and finds the neighbouring days', () => {
    const all = [
      note(1, '2026-10-20T08:00:00Z'),
      note(2, '2026-10-23T12:00:00Z'),
      note(3, '2026-10-23T07:00:00Z'),
      note(4, '2026-10-23T22:30:00Z'), // 00:30 on the 24th in Prague
    ];
    expect(scoped(all, 'upcoming', now).map((n) => n.id)).toEqual([2, 3, 4]);
    expect(scoped(all, 'all', now)).toHaveLength(4);
    const days = byDay(all);
    expect([...days.keys()]).toEqual(['2026-10-20', '2026-10-23', '2026-10-24']);
    expect(days.get('2026-10-23')!.map((n) => n.id)).toEqual([3, 2]);
    expect(neighbours([...days.keys()], '2026-10-22')).toEqual({ prev: '2026-10-20', next: '2026-10-23' });
    expect(neighbours([...days.keys()], '2026-10-23')).toEqual({ prev: '2026-10-20', next: '2026-10-24' });
    expect(neighbours([...days.keys()], '2026-10-25')).toEqual({ prev: '2026-10-24', next: null });
  });
});
