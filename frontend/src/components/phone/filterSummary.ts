/* A Filtry sheet row's value in a few words, for the right edge of the row:
 * null when the filter is unset (the row then reads "Vše"). Words only — every
 * value, option label and unit comes from where the desktop control takes it:
 * the registry's Czech option labels, the cohort-resolved Kč/m² unit
 * (`ppm2BoundUnit`, rule 23), the shared portal and place labels. */

import type { FilterDef } from '@/lib/filterRegistry.generated';
import { subtypeOptions } from '@/lib/filterLayout';
import type { ListingFilters, PipelineScope } from '@/lib/filters';
import { czPlural } from '@/lib/format';
import { portalScopeLabel } from '@/lib/portals';

import type { SheetRow } from './filterRows';
import { placeLabelOf } from './placeLabel';

const UNIT_WORD: Record<string, string> = { CZK: 'Kč', days: 'dní' };

const num = (n: number): string => n.toLocaleString('cs-CZ');

const withUnit = (text: string, unit: string | null): string => {
  const u = unit ? (UNIT_WORD[unit] ?? unit) : null;
  return u ? `${text} ${u}` : text;
};

/* "50–90 m²", "od 50 m²", "do 90 m²". */
export function rangeWords(lo: number | null, hi: number | null, unit: string | null): string | null {
  if (lo == null && hi == null) return null;
  const text = lo != null && hi != null ? `${num(lo)}–${num(hi)}` : lo != null ? `od ${num(lo)}` : `do ${num(hi!)}`;
  return withUnit(text, unit);
}

/* The first two picks and how many more: "2+kk, 3+kk +2". */
export function listWords(labels: ReadonlyArray<string>): string | null {
  if (labels.length === 0) return null;
  return labels.length <= 2 ? labels.join(', ') : `${labels.slice(0, 2).join(', ')} +${labels.length - 2}`;
}

const optionLabel = (def: FilterDef, v: unknown): string =>
  def.enum_values?.find((o) => o.value === v)?.label_cs ?? String(v);

const countWords = (n: number, one: string, few: string, many: string): string | null =>
  n > 0 ? `${num(n)} ${czPlural(n, one, few, many)}` : null;

function formWords(
  row: Extract<SheetRow, { kind: 'form' }>,
  view: Readonly<Record<string, unknown>>,
  units: Readonly<Record<string, string>>,
): string | null {
  const { def, maxDef } = row.row;
  const value = view[def.id];
  const unit = units[def.id] ?? def.unit;
  if (maxDef) return rangeWords((value as number | null) ?? null, (view[maxDef.id] as number | null) ?? null, unit);
  if (value == null) return null;
  switch (def.ui_control) {
    case 'pipeline_scope': {
      const n = (value as PipelineScope).stage_ids.length;
      return n === 0 ? 'celá pipeline' : countWords(n, 'fáze', 'fáze', 'fází');
    }
    case 'city_index_rules':
      return countWords((value as unknown[]).length, 'pravidlo', 'pravidla', 'pravidel');
    case 'multiselect':
      return def.enum_values
        ? listWords((value as unknown[]).map((v) => optionLabel(def, v)))
        : countWords((value as unknown[]).length, 'vybraná', 'vybrané', 'vybraných');
    case 'pill_group':
    case 'single_select':
      /* `any` is the registry's own "no constraint" option (status). */
      return value === 'any' ? null : optionLabel(def, value);
    case 'boolean':
      return value === true ? 'ano' : null;
    case 'tristate':
      return value ? 'ano' : 'ne';
    default: {
      const n = value as number;
      return withUnit(def.id.includes('min') ? `≥ ${num(n)}` : num(n), unit);
    }
  }
}

function customWords(row: Extract<SheetRow, { kind: 'custom' }>, f: ListingFilters): string | null {
  switch (row.block.key) {
    case 'subtype': {
      const labels = new Map(subtypeOptions(f.categoryMain).map((o) => [o.value, o.label]));
      return listWords(f.subtype.map((s) => labels.get(s) ?? s));
    }
    case 'districts':
      return f.districts.length ? placeLabelOf(f.districts) : null;
    case 'tags': {
      const parts = [
        countWords(f.tags.length, 'štítek', 'štítky', 'štítků'),
        f.tagsExcluded.length ? `−${num(f.tagsExcluded.length)}` : null,
      ].filter(Boolean);
      return parts.length ? parts.join(', ') : null;
    }
    case 'portals':
      return portalScopeLabel(f.portals, f.portalsExcluded, f.portalsExcludedActiveOnly);
    case 'market_growth':
      return countWords(f.priceGrowthRules.length, 'pravidlo', 'pravidla', 'pravidel');
    case 'include_no_price':
      return f.includeNoPrice ? 'ano' : null;
    case 'show_dismissed':
      return f.showDismissed ? 'ano' : null;
    default:
      return null;
  }
}

export function rowWords(
  row: SheetRow,
  f: ListingFilters,
  view: Readonly<Record<string, unknown>>,
  units: Readonly<Record<string, string>>,
): string | null {
  return row.kind === 'form' ? formWords(row, view, units) : customWords(row, f);
}
