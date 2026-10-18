/* The Filtry sheet's rows: Browse's sidebar layout (lib/filterLayout) unrolled
 * into one row per filter, a min/max pair as one, in the sidebar's order. The
 * sheet draws them (FilterSheet.tsx) and the feed counts them (the Filtry
 * chip's "filters beyond the scope"), so both read one list. */

import { formRows, type FormRow } from '@/components/FilterForm';
import { BROWSE_FILTER_BANDS, type CustomBlock, type CustomBlockKey, type FilterGroup } from '@/lib/filterLayout';
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

export interface SheetGroup {
  title: string;
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
  tags: { ids: ['tags', 'tags_excluded'], fields: ['tags', 'tagsExcluded'] },
  show_dismissed: { ids: ['show_dismissed'], fields: ['showDismissed'] },
  city_population_hint: { ids: [], fields: [] },
  market_growth: { ids: [], fields: ['priceGrowthRules'] },
  portals: {
    ids: ['portals', 'portals_excluded', 'portals_excluded_active_only'],
    fields: ['portals', 'portalsExcluded', 'portalsExcludedActiveOnly'],
  },
  location_mode: { ids: ['location'], fields: ['locationMode', 'centerRadius'] },
};

function groupOf(g: FilterGroup): SheetGroup {
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
  return { title: g.title, rows, notes };
}

/* What the sheet draws for these filters: the sidebar's bands and groups,
 * without the map-only one (no map on the phone), the cohort-conditional
 * groups (Subtype, Yield) exactly when the sidebar shows them. */
export function sheetBands(f: ListingFilters): SheetBand[] {
  return BROWSE_FILTER_BANDS.map((band) => ({
    title: band.title,
    groups: band.groups.filter((g) => !g.mapOnly && (!g.when || g.when(f))).map(groupOf),
  }));
}

/* Every row any cohort can show, the map-only one included: a desktop link may
 * carry a centre + radius, and it narrows the roll all the same. */
const ALL_ROWS: SheetRow[] = BROWSE_FILTER_BANDS.flatMap((band) =>
  band.groups.flatMap((g) => groupOf(g).rows),
);

const canon = (v: unknown): string =>
  Array.isArray(v) ? JSON.stringify(v.map((x) => JSON.stringify(x)).sort()) : JSON.stringify(v ?? null);

const differs = (a: ListingFilters, b: ListingFilters, k: keyof ListingFilters): boolean =>
  canon(a[k]) !== canon(b[k]);

/* How many filters `f` sets beyond its scope's own — a row counts once however
 * many fields it edits, and a field no row draws (a map viewport, a broker from
 * a desktop link) counts on its own, since it narrows the roll all the same. */
export function filtersBeyond(f: ListingFilters, base: ListingFilters): number {
  const covered = new Set<keyof ListingFilters>();
  let n = 0;
  for (const row of ALL_ROWS) {
    row.fields.forEach((k) => covered.add(k));
    if (row.fields.some((k) => differs(f, base, k))) n += 1;
  }
  for (const k of Object.keys(DEFAULT_FILTERS) as Array<keyof ListingFilters>) {
    if (!covered.has(k) && differs(f, base, k)) n += 1;
  }
  return n;
}
