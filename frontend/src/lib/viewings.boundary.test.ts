/* lib/viewings at every day boundary: "now" swept every 15 minutes across Prague
 * midnight in winter and in summer, both DST changes and the night #2048's test went
 * red, each against slots two days either side. The oracle reads Prague calendar days
 * and clocks straight from Intl, never from the code under test, and no answer may
 * move with the runner's own zone (run it under TZ=UTC, Europe/Prague, America/New_York). */

import { describe, expect, it } from 'vitest';

import { pragueDayKey } from './feed';
import type { ViewingSlot } from './viewings';
import { dayTitle, defaultSlot, isUpcoming, relativeDay, slotInstant, viewingSlot, viewingWhen } from './viewings';

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const inPrague = (locale: string, opts: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat(locale, { timeZone: 'Europe/Prague', ...opts });
const CALENDAR = inPrague('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' });
const CLOCK = inPrague('en-CA', { hourCycle: 'h23', hour: '2-digit', minute: '2-digit' });
const WEEKDAY_SHORT = inPrague('cs-CZ', { weekday: 'short' });
const WEEKDAY_LONG = inPrague('cs-CZ', { weekday: 'long' });
const DAY_MONTH = inPrague('cs-CZ', { day: 'numeric', month: 'long' });

const dayOf = (t: number) => CALENDAR.format(t);
const clockOf = (t: number) => CLOCK.format(t);
// A day key as a plain count of calendar days, so day arithmetic carries no clock.
const ordinal = (key: string) => {
  const [y, m, d] = key.split('-').map(Number);
  return Date.UTC(y, m - 1, d) / DAY;
};
const keyOf = (n: number) => new Date(n * DAY).toISOString().slice(0, 10);

/* Every instant the Prague calendar and clock read as the slot (Prague is UTC+1 or
 * UTC+2): none in the spring gap, two in the autumn's repeated hour. */
function readingsOf({ day, time }: ViewingSlot): number[] {
  const [y, m, d] = day.split('-').map(Number);
  const [hh, mm] = time.split(':').map(Number);
  const wall = Date.UTC(y, m - 1, d, hh, mm);
  return [wall - 2 * HOUR, wall - HOUR].filter((t) => dayOf(t) === day && clockOf(t) === time);
}

const RELATIVE: Record<number, string> = { [-2]: 'před 2 dny', [-1]: 'včera', 0: 'dnes', 1: 'zítra', 2: 'za 2 dny' };
const TITLE: Record<number, string> = { [-1]: 'Včera', 0: 'Dnes', 1: 'Zítra' };

function mismatchesAt(now: number): string[] {
  const out: string[] = [];
  const check = (what: string, got: unknown, want: unknown) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) out.push(`${what}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  };
  const at = new Date(now);
  const today = dayOf(now);
  check('pragueDayKey(now)', pragueDayKey(at), today);

  // "The next full hour today, or tomorrow at 10:00 once that would be past 20:00."
  const nextHour = (Math.floor(now / HOUR) + 1) * HOUR;
  const guess = defaultSlot(at);
  check(
    'defaultSlot(now)',
    guess,
    dayOf(nextHour) === today && clockOf(nextHour) <= '20:00'
      ? { day: today, time: clockOf(nextHour) }
      : { day: keyOf(ordinal(today) + 1), time: '10:00' },
  );
  check('slotInstant(defaultSlot(now)) is ahead of now', Date.parse(slotInstant(guess)) > now, true);

  for (const shift of [-2, -1, 0, 1, 2]) {
    for (const time of ['00:15', '02:30', '12:00', '23:45']) {
      const slot = { day: keyOf(ordinal(today) + shift), time };
      const label = `${slot.day} ${slot.time}`;
      const iso = slotInstant(slot);
      const t = Date.parse(iso);
      const readings = readingsOf(slot);
      if (readings.length === 0) {
        // The spring gap: the offset before it applies (RFC 5545 §3.3.5), so the
        // clock reads the slot an hour on, on the same day.
        const hourOn = `${String(Number(time.slice(0, 2)) + 1).padStart(2, '0')}${time.slice(2)}`;
        check(`slotInstant(${label}) in the gap`, [dayOf(t), clockOf(t)], [slot.day, hourOn]);
      } else if (!readings.includes(t)) {
        check(`slotInstant(${label})`, iso, readings.map((r) => new Date(r).toISOString()));
      }
      check(`viewingSlot(slotInstant(${label}))`, viewingSlot(iso), { day: dayOf(t), time: clockOf(t) });

      const days = ordinal(dayOf(t)) - ordinal(today);
      const year = dayOf(t).slice(0, 4) === today.slice(0, 4) ? '' : ` ${dayOf(t).slice(0, 4)}`;
      const [, m, d] = dayOf(t).split('-').map(Number);
      check(`relativeDay(${label})`, relativeDay(iso, at), RELATIVE[days]);
      check(`isUpcoming(${label})`, isUpcoming(iso, at), days >= 0);
      check(`viewingWhen(${label})`, viewingWhen(iso, at), `${WEEKDAY_SHORT.format(t)} ${d}. ${m}.${year} v ${clockOf(t)}`);
      check(
        `dayTitle(${label})`,
        dayTitle(viewingSlot(iso).day, pragueDayKey(at)),
        TITLE[days] ?? `${WEEKDAY_LONG.format(t)} ${DAY_MONTH.format(t)}${year}`,
      );
    }
  }
  return out;
}

const nowsBetween = (from: string, to: string): string[] => {
  const out: string[] = [];
  for (let t = Date.parse(from); t <= Date.parse(to); t += HOUR / 4) out.push(new Date(t).toISOString());
  return out;
};

describe.each([
  ['Prague midnight in CET, into a new year', '2026-12-31T21:00:00Z', '2027-01-01T01:00:00Z'],
  ['Prague midnight in CEST, into a new month', '2026-07-31T20:00:00Z', '2026-08-01T00:00:00Z'],
  ['the CEST→CET change (03:00 → 02:00)', '2026-10-25T00:00:00Z', '2026-10-25T04:00:00Z'],
  ['the first CET midnight, where #2048 went red', '2026-10-25T21:00:00Z', '2026-10-26T01:00:00Z'],
  ['the CET→CEST change (02:00 → 03:00)', '2027-03-28T00:00:00Z', '2027-03-28T03:00:00Z'],
  ['the first CEST midnight', '2027-03-28T20:00:00Z', '2027-03-29T00:00:00Z'],
])('%s', (_name, from, to) => {
  it.each(nowsBetween(from, to))('agrees with the Prague calendar at %s', (now) => {
    const mismatches = mismatchesAt(Date.parse(now));
    expect(mismatches, mismatches.join('; ')).toEqual([]);
  });
});
