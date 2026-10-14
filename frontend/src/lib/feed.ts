/* The feed's pure half (#1766): which cohort a scope chip means, and how the roll
 * is cut into days. The read itself is Browse's (`fetchListingsForCards`,
 * keyset-paged on `browse_list`, newest first) — nothing here queries.
 *
 * A scope is "Vše" or one of the account's saved Browse presets (#1760 Q16: the
 * presets, not the watchdogs). A preset is applied the way Browse loads one —
 * `readPresetSpec` tolerates old blobs, `filtersForPreset` drops the lenses that
 * ride outside a preset — and keeps a map area the operator saved with it. The
 * preset's saved SORT is not used: the feed is newest first by decision (Q17). */

import { DEFAULT_FILTERS, filtersForPreset, readPresetSpec, type ListingFilters } from './filters';
import type { CardRow } from './queries';
import type { PortalChoice } from './portals';
import type { FilterPreset } from './types';

export const FEED_SCOPE_KEY = 'sreality.feed.scope';
export const FEED_ALL_SCOPE = 'all';

/* "Vše": what came on the market anywhere — every category, sale and rent
 * (`categoryType: null` is "no constraint", rule 22), properties with a live ad. */
export const FEED_ALL_FILTERS: ListingFilters = {
  ...DEFAULT_FILTERS,
  categoryMain: [],
  categoryType: null,
  status: 'active',
};

export function feedFiltersForPreset(preset: FilterPreset | null | undefined): ListingFilters {
  if (!preset) return FEED_ALL_FILTERS;
  return filtersForPreset(readPresetSpec(preset.filter_spec).filters, true);
}

/* The portal chips REPLACE the scope's own portal filter, both sides (a preset may
 * carry one); null = keep the scope's. */
export function withPortals(f: ListingFilters, choice: PortalChoice | null): ListingFilters {
  return choice == null ? f : { ...f, ...choice };
}

export function readFeedScope(): string {
  try {
    return localStorage.getItem(FEED_SCOPE_KEY) || FEED_ALL_SCOPE;
  } catch {
    return FEED_ALL_SCOPE;
  }
}

export function writeFeedScope(scope: string): void {
  try {
    localStorage.setItem(FEED_SCOPE_KEY, scope);
  } catch {
    /* ignore */
  }
}

/* ---------------------------------------------------------------------------
 * Days. The roll is split by the calendar day of the timestamp it is ordered by,
 * in Prague, whatever the phone's own zone: the operator reads "Dnes" as a
 * Czech day, and the market it describes is Czech.
 * ------------------------------------------------------------------------- */

export const FEED_TZ = 'Europe/Prague';

const DAY_KEY = new Intl.DateTimeFormat('en-CA', {
  timeZone: FEED_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

const CLOCK = new Intl.DateTimeFormat('cs-CZ', {
  timeZone: FEED_TZ,
  hour: '2-digit',
  minute: '2-digit',
});

/* 'YYYY-MM-DD' of the Prague calendar day an instant falls on. */
export const pragueDayKey = (at: Date | string | number): string =>
  DAY_KEY.format(at instanceof Date ? at : new Date(at));

/* 'HH:MM' on the Prague clock. */
export const pragueClock = (at: Date | number): string => CLOCK.format(at);

const shiftDay = (dayKey: string, days: number): string => {
  const [y, m, d] = dayKey.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
};

/* "Dnes", "Včera", else "5. 10." (with the year once it is not this one). */
export function dayRuleLabel(dayKey: string, todayKey: string): string {
  if (dayKey === todayKey) return 'Dnes';
  if (dayKey === shiftDay(todayKey, -1)) return 'Včera';
  const [y, m, d] = dayKey.split('-').map(Number);
  return y === Number(todayKey.slice(0, 4)) ? `${d}. ${m}.` : `${d}. ${m}. ${y}`;
}

export interface FeedDay<T> {
  dayKey: string;
  rows: T[];
}

/* Consecutive runs of one Prague day, in the roll's own order. A row without a
 * timestamp stays with the run it follows rather than opening a day of its own. */
export function groupByDay<T>(rows: ReadonlyArray<T>, at: (row: T) => string | null | undefined): FeedDay<T>[] {
  const out: FeedDay<T>[] = [];
  for (const row of rows) {
    const ts = at(row);
    const key = ts ? pragueDayKey(ts) : (out[out.length - 1]?.dayKey ?? '');
    const last = out[out.length - 1];
    if (last && last.dayKey === key) last.rows.push(row);
    else out.push({ dayKey: key, rows: [row] });
  }
  return out;
}

/* The timestamp a row is ORDERED by: its newest ad on the one chosen portal
 * (queries.ts orderPortal / effectiveSort), else the property's first seen. */
export function feedTimestamp(row: CardRow, orderPortal: string | null): string {
  const onPortal = orderPortal ? row[`newest_ad_at_${orderPortal}`] : null;
  return onPortal ?? row.first_seen_at;
}
