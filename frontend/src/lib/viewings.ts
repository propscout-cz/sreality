/* Viewings ("prohlídky"): a viewing is a NOTE WITH A DATE (migration 666) —
 * `Note.viewing_at`, when its author sees the property in person; the note's
 * text is what to check there and, afterwards, what they found. So a viewing is
 * written, shared, carried and split exactly as a note is; this module holds
 * only what the date adds: the Prague wall clock it is entered and read on (the
 * feed's day, lib/feed), the words it is said in, and the day arithmetic of the
 * phone's Prohlídky tab (pages/phone/PhoneViewings). */

import { FEED_TZ, pragueDayKey, shiftDay } from './feed';
import type { Note } from './types';

export type ViewingScope = 'upcoming' | 'all';

/* A viewing as the composer holds it: a Prague day ('YYYY-MM-DD') and time ('HH:MM'). */
export interface ViewingSlot {
  day: string;
  time: string;
}

const PARTS = new Intl.DateTimeFormat('en-US', {
  timeZone: FEED_TZ,
  hourCycle: 'h23',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

function pragueFields(at: Date): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of PARTS.formatToParts(at)) if (p.type !== 'literal') out[p.type] = Number(p.value);
  return out;
}

/* Minutes Prague is ahead of UTC at an instant (60 in winter, 120 in summer). */
function pragueOffset(at: Date): number {
  const f = pragueFields(at);
  return (Date.UTC(f.year, f.month - 1, f.day, f.hour, f.minute, f.second) - at.getTime()) / 60_000;
}

const pad2 = (n: number) => String(n).padStart(2, '0');

/* The slot an instant shows on the Prague clock. */
export function viewingSlot(iso: string): ViewingSlot {
  const f = pragueFields(new Date(iso));
  return { day: `${f.year}-${pad2(f.month)}-${pad2(f.day)}`, time: `${pad2(f.hour)}:${pad2(f.minute)}` };
}

/* The instant a Prague wall-clock slot names, as ISO (the API refuses a naive time).
 * The second pass settles a slot whose first guess fell across a DST change. */
export function slotInstant({ day, time }: ViewingSlot): string {
  const [y, m, d] = day.split('-').map(Number);
  const [hh, mm] = time.split(':').map(Number);
  const wall = Date.UTC(y, m - 1, d, hh, mm);
  let t = wall - pragueOffset(new Date(wall)) * 60_000;
  t = wall - pragueOffset(new Date(t)) * 60_000;
  return new Date(t).toISOString();
}

export const slotIsComplete = (s: ViewingSlot): boolean =>
  /^\d{4}-\d{2}-\d{2}$/.test(s.day) && /^\d{2}:\d{2}$/.test(s.time);

/* A new viewing's first guess: the next full hour today, or tomorrow at 10:00
 * once that would be past 20:00 (a viewing is rarely booked for the night). */
export function defaultSlot(now: Date = new Date()): ViewingSlot {
  const { day, time } = viewingSlot(now.toISOString());
  const next = Number(time.slice(0, 2)) + 1;
  return next > 20 ? { day: shiftDay(day, 1), time: '10:00' } : { day, time: `${pad2(next)}:00` };
}

const WEEKDAY_SHORT = new Intl.DateTimeFormat('cs-CZ', { timeZone: 'UTC', weekday: 'short' });
const WEEKDAY_LONG = new Intl.DateTimeFormat('cs-CZ', { timeZone: 'UTC', weekday: 'long' });
const MONTH_LONG = new Intl.DateTimeFormat('cs-CZ', { timeZone: 'UTC', day: 'numeric', month: 'long' });
const MONTH_SHORT = new Intl.DateTimeFormat('cs-CZ', { timeZone: 'UTC', month: 'short' });

const dayDate = (day: string): Date => new Date(`${day}T12:00:00Z`);

/* "čt 24. 10." — the year only once it is not this one. */
export function shortDay(day: string, todayKey: string): string {
  const [y, m, d] = day.split('-').map(Number);
  const year = day.slice(0, 4) === todayKey.slice(0, 4) ? '' : ` ${y}`;
  return `${WEEKDAY_SHORT.format(dayDate(day))} ${d}. ${m}.${year}`;
}

/* The Prohlídky tab's day title: "Dnes", "Zítra", "Včera", else "čtvrtek 24. října". */
export function dayTitle(day: string, todayKey: string): string {
  if (day === todayKey) return 'Dnes';
  if (day === shiftDay(todayKey, 1)) return 'Zítra';
  if (day === shiftDay(todayKey, -1)) return 'Včera';
  const year = day.slice(0, 4) === todayKey.slice(0, 4) ? '' : ` ${day.slice(0, 4)}`;
  return `${WEEKDAY_LONG.format(dayDate(day))} ${MONTH_LONG.format(dayDate(day))}${year}`;
}

/* The full date under a relative title: "čtvrtek 24. října" (always, so "Zítra" names its date). */
export const dayLine = (day: string): string =>
  `${WEEKDAY_LONG.format(dayDate(day))} ${MONTH_LONG.format(dayDate(day))}`;

/* The calendar leaf's two lines: "říj" and "24". */
export function leaf(iso: string): { month: string; day: number } {
  const { day } = viewingSlot(iso);
  return { month: MONTH_SHORT.format(dayDate(day)).replace('.', ''), day: Number(day.slice(8)) };
}

const daysBetween = (from: string, to: string): number =>
  Math.round((dayDate(to).getTime() - dayDate(from).getTime()) / 86_400_000);

/* How far a viewing is, in days on the Prague calendar: "dnes", "zítra", "za 3 dny",
 * "včera", "před 5 dny". */
export function relativeDay(iso: string, now: Date = new Date()): string {
  const n = daysBetween(pragueDayKey(now), viewingSlot(iso).day);
  if (n === 0) return 'dnes';
  if (n === 1) return 'zítra';
  if (n === -1) return 'včera';
  if (n > 0) return `za ${n} ${n <= 4 ? 'dny' : 'dní'}`;
  return `před ${-n} dny`;
}

/* "čt 24. 10. v 14:00" — a viewing's whole time, as its card and its aria label say it. */
export function viewingWhen(iso: string, now: Date = new Date()): string {
  const { day, time } = viewingSlot(iso);
  return `${shortDay(day, pragueDayKey(now))} v ${time}`;
}

/* Upcoming = on today's Prague day or later: today's morning viewing stays on today's page. */
export const isUpcoming = (iso: string, now: Date = new Date()): boolean =>
  viewingSlot(iso).day >= pragueDayKey(now);

/* A property's notes as its Poznámky section lists them: its upcoming viewings first, the
 * nearest on top, then every other note in the order it came (newest written first). */
export function sectionOrder(notes: readonly Note[], now: Date = new Date()): Note[] {
  const ahead = notes
    .filter((n) => n.viewing_at && isUpcoming(n.viewing_at, now))
    .sort((a, b) => a.viewing_at!.localeCompare(b.viewing_at!));
  return [...ahead, ...notes.filter((n) => !ahead.includes(n))];
}

/* The tab's viewings in a scope, earliest first (the read already orders them). */
export function scoped(viewings: readonly Note[], scope: ViewingScope, now: Date = new Date()): Note[] {
  return viewings.filter((v) => v.viewing_at && (scope === 'all' || isUpcoming(v.viewing_at, now)));
}

/* Each Prague day that holds a viewing, with its viewings in time order. */
export function byDay(viewings: readonly Note[]): Map<string, Note[]> {
  const out = new Map<string, Note[]>();
  const sorted = [...viewings].sort((a, b) => (a.viewing_at ?? '').localeCompare(b.viewing_at ?? '') || a.id - b.id);
  for (const v of sorted) {
    const day = viewingSlot(v.viewing_at!).day;
    out.set(day, [...(out.get(day) ?? []), v]);
  }
  return out;
}

/* The nearest days with a viewing before and after `day` (ascending keys). */
export function neighbours(days: readonly string[], day: string): { prev: string | null; next: string | null } {
  let prev: string | null = null;
  let next: string | null = null;
  for (const d of days) {
    if (d < day) prev = d;
    else if (d > day && next == null) next = d;
  }
  return { prev, next };
}
