/* Czech-locale formatters. Centralised so a price, area, count, or
 * timestamp looks the same wherever it appears. */

import { PPM2_UNIT, type AreaKind, type Ppm2Basis } from './measure';

const NBSP = ' ';
const THIN_SPACE = ' ';

const czNumber = new Intl.NumberFormat('cs-CZ');

const czShortDate = new Intl.DateTimeFormat('cs-CZ', {
  day: 'numeric',
  month: 'numeric',
});

/* Czech short date — "5. 5." (day. month.). Used on listing-card
 * badges where the year is implicit and screen real estate is tiny. */
export const fmtShortDate = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return czShortDate.format(d);
};

/* The Czech noun for a count: 1 `one`, 2–4 `few`, 0 and 5+ `many`
 * (1 inzerát · 2 inzeráty · 5 inzerátů). */
export const czPlural = (n: number, one: string, few: string, many: string): string =>
  n === 1 ? one : n >= 2 && n <= 4 ? few : many;

/* Czech-plural day count: 1 den, 2-4 dny, 5+ dní. Negative or null -> "—". */
export const fmtTomDays = (n: number | null | undefined): string => {
  if (n == null || n < 0) return '—';
  return `${czNumber.format(n)}${NBSP}${czPlural(n, 'den', 'dny', 'dní')}`;
};

export const fmtCount = (n: number | null | undefined): string =>
  n == null ? '—' : czNumber.format(n);

export const fmtCzk = (n: number | null | undefined): string =>
  n == null ? '—' : `${czNumber.format(n)}${NBSP}Kč`;

/* LLM spend is billed and stored in USD (llm_calls.cost_usd) — shown as
 * dollars, never converted. Sub-cent per-call averages need 4 decimals. */
const usdCurrency = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
});

export const fmtUsd = (n: number | null | undefined): string =>
  n == null ? '—' : usdCurrency.format(n);

export const fmtUsdPerCall = (n: number | null | undefined): string =>
  n == null ? '—' : n >= 0.1 ? usdCurrency.format(n) : `$${n.toFixed(4)}`;

/* `areaKind` names the DENOMINATOR, not the unit: `area_m2` is polymorphic by
 * design (floor area for byt/dum/komerční, PLOT area for pozemek — the Option-A
 * fork), so a surface with room to disambiguate passes 'plot' and gets
 * "1 200 m² pozemku". Omitting it renders exactly what it always did, which is
 * what the dense grids (table cells, card footers) still want. */
export const fmtArea = (
  n: number | null | undefined,
  areaKind?: AreaKind,
): string =>
  n == null
    ? '—'
    : `${czNumber.format(Math.round(n))}${NBSP}m²${areaKind === 'plot' ? `${NBSP}pozemku` : ''}`;

/* THE storey, in Czech, and the only place the app says which convention it means.
 *
 * `listings.floor` is ground = 0 on every portal since W8 (přízemí 0, 1. patro 1,
 * suterén −1). Before that it was a ~50/50 mix of two conventions printed as a bare
 * number, so one flat read "3" from its idnes row and "4" from its sreality row in the
 * same list and nothing on screen said which scale either was on. A bare integer cannot
 * carry that fact; the Czech word can, which is why this is the one renderer for every
 * surface that shows `listings.floor` or an estimation subject's storey. The one place
 * still printing a bare number is BuildingDetail's unit table, whose `floor` is the
 * operator-curated building decomposition, not this column.
 *
 * `total_floors` is a PODLAŽÍ count (the ground storey included), so it is spelled out
 * rather than printed as "2/5": under ground = 0 the top storey is total − 1, and the
 * slash form invited reading the 5 as a patro. A null floor renders null, leaving each
 * surface to say what an absent storey looks like on it.
 *
 * Below ground the MAGNITUDE is kept (−2 is "2. podzemní podlaží", not "suterén"):
 * AttrDiffTable diffs the rendered strings, so collapsing every basement to one word
 * would have made −1 and −2 read as agreeing on the one screen asked to show the
 * difference. 109 rows corpus-wide sit below −1. */
export const fmtFloor = (
  floor: number | null | undefined,
  totalFloors?: number | null,
): string | null => {
  if (floor == null) return null;
  const storey =
    floor === 0 ? 'přízemí'
    : floor === -1 ? 'suterén'
    : floor < 0 ? `${-floor}. podzemní podlaží`
    : `${floor}. patro`;
  return totalFloors == null ? storey : `${storey} z ${totalFloors} podlaží`;
};

/* The storey as a tag or chip prints it, "2. patro z 5", with fmtFloor's full
 * wording as its tooltip. The scale is ground = 0, so a bare "2/5" would read as
 * the second of five podlaží when it is the third: the ordinal stays spelled and
 * the count stays behind "z". */
export const fmtFloorTag = (
  floor: number | null | undefined,
  totalFloors?: number | null,
): { text: string; title: string } | null => {
  const storey = fmtFloor(floor);
  const full = fmtFloor(floor, totalFloors);
  if (storey == null || full == null) return null;
  return { text: totalFloors == null ? storey : `${storey} z ${totalFloors}`, title: full };
};

/* Distance from a subject point. Metres under a kilometre — the difference
 * between 80 m and 900 m is the difference between the same street and a
 * different neighbourhood — and one decimal of a kilometre above it, where the
 * metres would be false precision: the point they are measured from is a street
 * or municipality centroid for most listings (see lib/geoLinks). */
export const fmtDistanceM = (n: number | null | undefined): string => {
  if (n == null || !Number.isFinite(n)) return '—';
  return n < 1000
    ? `${czNumber.format(Math.round(n))}${NBSP}m`
    : `${(n / 1000).toLocaleString('cs-CZ', {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      })}${NBSP}km`;
};

/* THE percentage formatter. Czech typography puts a NON-BREAKING space before
 * the sign (`4,2 %`, never `4.2%`) and uses a comma decimal separator — this
 * replaced three hand-rolled variants that disagreed on all three counts, one
 * of which emitted an English decimal point on a Czech UI.
 *
 * `signed` prepends '+' to positives (negatives already carry the locale's
 * minus) — use it wherever the number is a DELTA, so "+3,5 %" and "−3,5 %"
 * are visually symmetric. Bare magnitudes (a yield, a share) stay unsigned. */
export const fmtPct = (
  n: number | null | undefined,
  /* unit: false drops the "%" — the low end of a range ("3,60–4,10 %"). */
  opts: { signed?: boolean; digits?: number; unit?: boolean } = {},
): string => {
  if (n == null || !Number.isFinite(n)) return '—';
  const { signed = false, digits = 1, unit = true } = opts;
  const body = n.toLocaleString('cs-CZ', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const sign = signed && n > 0 ? '+' : '';
  return unit ? `${sign}${body}${NBSP}%` : `${sign}${body}`;
};

/* Percentage POINTS — a difference between two percentages (a yield moving
 * from 4,2 % to 5,1 % changed by +0,9 pp, not by +21 %). Always signed;
 * conflating the two is the classic stats-reporting error. */
export const fmtPP = (n: number | null | undefined, digits = 2): string =>
  n == null || !Number.isFinite(n)
    ? '—'
    : `${n > 0 ? '+' : ''}${n.toLocaleString('cs-CZ', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })}${NBSP}pp`;

/* THE per-m² renderer. Two arguments, both required, because the north star is
 * that no surface renders the number without its basis label — a bare Kč/m²
 * cannot be read: sale runs ~91 535, rent ~319, a 300x difference that a shared
 * suffix would hide.
 *
 * `value` is the SERVER measure (migration 425: basis-resolved and floored),
 * never a client-side price/area. The `fmtPricePerM2(price, area)` this replaced
 * did exactly that re-derivation and is deleted: Browse sorts and filters on the
 * published `price_per_m2` column, so dividing in the cell printed figures the
 * cohort said did not exist — a 136 Kč commercial "rental" rendered "1 Kč/m²"
 * while the measure was NULL and the row was excluded by any Kč/m² bound.
 *
 * NULL value, NULL basis and 'mixed' all render the gap. 'mixed' especially:
 * a cohort spanning sale and rent has no single unit, and printing the number
 * anyway is the category error this program exists to end. */
export const fmtMeasuredPricePerM2 = (
  value: number | null | undefined,
  basis: Ppm2Basis | null,
): string =>
  value == null || basis == null || basis === 'mixed'
    ? '—'
    : `${czNumber.format(Math.round(value))}${NBSP}${PPM2_UNIT[basis]}`;

const SEC = 1, MIN = 60, HOUR = 3600, DAY = 86400;

/* How long ago, in the one unit each surface rounds to — shared by the English and
 * the Czech wording so the two never round differently. Null for no time. */
function relativeBucket(iso: string | null | undefined): [number, Intl.RelativeTimeFormatUnit] | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (isNaN(t)) return null;
  const diff = Math.max(0, (Date.now() - t) / 1000);
  if (diff < MIN)  return [Math.round(diff / SEC), 'second'];
  if (diff < HOUR) return [Math.round(diff / MIN), 'minute'];
  if (diff < DAY)  return [Math.round(diff / HOUR), 'hour'];
  const days = Math.round(diff / DAY);
  if (days < 14) return [days, 'day'];
  if (days < 60) return [Math.round(days / 7), 'week'];
  if (days < 365) return [Math.round(days / 30), 'month'];
  return [Math.round(days / 365), 'year'];
}

const AGO_EN: Partial<Record<Intl.RelativeTimeFormatUnit, string>> = {
  second: 's', minute: 'min', hour: 'h', day: 'days', week: 'weeks', month: 'months', year: 'yr',
};

export const fmtRelative = (iso: string | null | undefined): string => {
  const b = relativeBucket(iso);
  return b ? `${b[0]}${THIN_SPACE}${AGO_EN[b[1]]} ago` : '—';
};

const CS_RELATIVE = new Intl.RelativeTimeFormat('cs', { style: 'short', numeric: 'always' });

/* fmtRelative in Czech, for a Czech surface (the map popup): "před 2 h", "před 3 dny". */
export const fmtRelativeCs = (iso: string | null | undefined): string => {
  const b = relativeBucket(iso);
  return b ? CS_RELATIVE.format(-b[0], b[1]) : '—';
};

export const fmtParkingLots = (n: number | null | undefined): string =>
  n == null ? '—' : `${czNumber.format(n)}${NBSP}${n === 1 ? 'místo' : 'místa'}`;

export const fmtAbsolute = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('cs-CZ', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const pad2 = (n: number) => String(n).padStart(2, '0');

export const fmtDateSlash = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}/${d.getFullYear()}`;
};

export const fmtTime24 = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
};
