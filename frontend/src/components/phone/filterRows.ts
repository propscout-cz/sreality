/* The Filtry sheet's rows: Browse's sidebar layout (lib/filterLayout) unrolled
 * into one row per filter, a min/max pair as one, in the sidebar's order. The
 * sheet draws them (FilterSheet.tsx), the feed counts them (the Filtry chip's
 * "filters beyond the scope") and Browse says them as its applied-filter pills
 * (AppliedFilterPills, ruling P09), so all three read one list. */

import { formRows, type FormRow } from '@/components/FilterForm';
import { BROWSE_FILTER_BANDS, type CustomBlock, type CustomBlockKey, type FilterRow } from '@/lib/filterLayout';
import { DEFAULT_FILTERS, REGISTRY_KEY_MAP, type ListingFilters } from '@/lib/filters';

interface RowBase {
  key: string;
  label: string;
  /* The registry ids the row edits — what the registry walk counts. */
  ids: string[];
  fields: Array<keyof ListingFilters>;
  /* The desktop control is already one labelled line (a tri-state, an on/off),
   * so the row IS the control; every other row opens to show it. */
  inline: boolean;
}

export type SheetRow =
  | (RowBase & { kind: 'form'; row: FormRow })
  | (RowBase & { kind: 'custom'; block: CustomBlock });

/* One sidebar row's filters: a row each on the phone, no caption of its own. */
export interface SheetGroup {
  rows: SheetRow[];
  notes: CustomBlock[];
}

export interface SheetBand {
  title: string;
  groups: SheetGroup[];
}

const KEY_OF = REGISTRY_KEY_MAP as Readonly<Record<string, keyof ListingFilters>>;

/* What each custom block edits: the registry ids it renders (the registry walk
 * counts them) and the ListingFilters fields behind them (the Filtry count). */
const CUSTOM_REACH: Record<CustomBlockKey, { ids: string[]; fields: Array<keyof ListingFilters> }> = {
  subtype: { ids: ['subtype'], fields: ['subtype'] },
  districts: { ids: ['districts'], fields: ['districts'] },
  include_no_price: { ids: ['include_no_price'], fields: ['includeNoPrice'] },
  show_dismissed: { ids: ['show_dismissed'], fields: ['showDismissed'] },
  city_population_hint: { ids: [], fields: [] },
  market_growth: { ids: [], fields: ['priceGrowthRules'] },
  portals: {
    ids: ['portals', 'portals_excluded', 'portals_excluded_active_only'],
    fields: ['portals', 'portalsExcluded', 'portalsExcludedActiveOnly'],
  },
  location_mode: { ids: ['location'], fields: ['locationMode', 'centerRadius'] },
  broker: { ids: [], fields: ['brokerId'] },
};

function groupOf(g: FilterRow): SheetGroup {
  const rows: SheetRow[] = [];
  const notes: CustomBlock[] = [];
  for (const b of g.blocks) {
    if (b.kind === 'custom') {
      if (b.note) notes.push(b);
      else {
        rows.push({ kind: 'custom', key: b.key, label: b.label, ...CUSTOM_REACH[b.key], inline: false, block: b });
      }
      continue;
    }
    for (const row of formRows('browse', { includeOnly: b.ids })) {
      const ids = [row.def.id, ...(row.maxDef ? [row.maxDef.id] : [])];
      rows.push({
        kind: 'form',
        key: row.def.id,
        label: b.labels[row.def.id] ?? row.def.id,
        ids,
        fields: ids.map((id) => KEY_OF[id]).filter(Boolean),
        inline: !row.maxDef && (row.def.ui_control === 'tristate' || row.def.ui_control === 'boolean'),
        row,
      });
    }
  }
  return { rows, notes };
}

/* What the sheet draws for these filters: the sidebar's bands and rows,
 * without the map-only one (no map on the phone), the cohort-conditional
 * rows (Subtype, Yield) exactly when the sidebar shows them. */
export function sheetBands(f: ListingFilters): SheetBand[] {
  return BROWSE_FILTER_BANDS.map((band) => ({
    title: band.title,
    groups: band.rows.filter((g) => !g.mapOnly && (!g.when || g.when(f))).map(groupOf),
  }));
}

/* Every row any cohort can show, the map-only one included: a desktop link may
 * carry a centre + radius, and it narrows the roll all the same. */
const ALL_ROWS: SheetRow[] = BROWSE_FILTER_BANDS.flatMap((band) =>
  band.rows.flatMap((g) => groupOf(g).rows),
);

const canon = (v: unknown): string =>
  Array.isArray(v) ? JSON.stringify(v.map((x) => JSON.stringify(x)).sort()) : JSON.stringify(v ?? null);

const differs = (a: ListingFilters, b: ListingFilters, k: keyof ListingFilters): boolean =>
  canon(a[k]) !== canon(b[k]);

/* One filter `f` sets beyond its base: the sidebar row that draws it, or null
 * for a field no row draws, and every field it spans. */
export interface AppliedFilter {
  key: string;
  label: string;
  row: SheetRow | null;
  fields: Array<keyof ListingFilters>;
}

/* The names of the fields no row draws, which narrow the cohort all the same:
 * a map viewport, URL-only bounds. A field in neither a row nor here fails the
 * census (filterRows.test.ts) before a pill could name it by its key. */
const UNROWED_LABELS: Readonly<Record<string, string>> = {
  bounds: 'Map area',
  categorySubCb: 'Sub-category',
  gardenArea: 'Garden area',
};

const ROWED = new Set(ALL_ROWS.flatMap((r) => r.fields));

/* The fields no row draws, in DEFAULT_FILTERS order, a `…Min` / `…Max` pair as one. */
export const UNROWED_FILTERS: ReadonlyArray<Omit<AppliedFilter, 'row'>> = (() => {
  const groups = new Map<string, Array<keyof ListingFilters>>();
  for (const k of Object.keys(DEFAULT_FILTERS) as Array<keyof ListingFilters>) {
    if (ROWED.has(k)) continue;
    const key = k.replace(/(Min|Max)$/, '');
    groups.set(key, [...(groups.get(key) ?? []), k]);
  }
  return [...groups].map(([key, fields]) => ({ key, label: UNROWED_LABELS[key] ?? key, fields }));
})();

/* The words the sidebar names registry filter `id` by: the label of the row
 * that draws it, else the name of its field no row draws, else the id. */
export function filterLabel(id: string): string {
  const field = KEY_OF[id];
  return (
    ALL_ROWS.find((r) => r.ids.includes(id))?.label ??
    UNROWED_FILTERS.find((g) => field != null && g.fields.includes(field))?.label ??
    id
  );
}

/* What `f` sets beyond its scope's own, in the sidebar's order: a row once
 * however many fields it edits, then each field no row draws. */
export function appliedFilters(f: ListingFilters, base: ListingFilters): AppliedFilter[] {
  const out: AppliedFilter[] = [];
  for (const row of ALL_ROWS) {
    if (row.fields.some((k) => differs(f, base, k))) {
      out.push({ key: row.key, label: row.label, row, fields: row.fields });
    }
  }
  for (const g of UNROWED_FILTERS) {
    if (g.fields.some((k) => differs(f, base, k))) out.push({ ...g, row: null });
  }
  return out;
}

/* How many filters `f` sets beyond its scope's own: the Filtry chip's count. */
export const filtersBeyond = (f: ListingFilters, base: ListingFilters): number =>
  appliedFilters(f, base).length;

/* `f` with one applied filter set back to the base, every other one kept. */
export function withoutApplied(f: ListingFilters, base: ListingFilters, a: AppliedFilter): ListingFilters {
  const reset: Partial<Record<keyof ListingFilters, unknown>> = {};
  for (const k of a.fields) reset[k] = base[k];
  return { ...f, ...reset } as ListingFilters;
}
