/* A Filtry sheet row's value in a few words, for the right edge of the row:
 * null when the filter is unset (the row then reads "Vše"). Words only — every
 * value, option label and unit comes from where the desktop control takes it:
 * the registry's Czech option labels, the cohort-resolved Kč/m² unit
 * (`ppm2BoundUnit`, rule 23), the shared portal and place labels. Browse's
 * applied-filter pills (AppliedFilterPills) read the same words, with the
 * connecting ones in English, the language of that screen. */

import type { FilterDef } from '@/lib/filterRegistry.generated';
import { subtypeOptions } from '@/lib/filterLayout';
import type { DistrictChip, ListingFilters } from '@/lib/filters';
import { czPlural } from '@/lib/format';
import { portalScopeLabel } from '@/lib/portals';
import { scopeWords, type CuratedSet, type SetScope } from '@/lib/setScope';

import type { SheetRow } from './filterRows';
import { placeLabelOf } from './placeLabel';

export type WordsLang = 'cs' | 'en';

interface JoinWords {
  from: string;
  to: string;
  yes: string;
  no: string;
  chosen: string;
  whole: string;
  rule: readonly [string, string, string];
  units: Readonly<Record<string, string>>;
}

/* The words a value is joined with; option labels, places and portals pass through. */
const JOIN: Record<WordsLang, JoinWords> = {
  cs: {
    from: 'od', to: 'do', yes: 'ano', no: 'ne', chosen: 'vybraný', whole: 'Celá ČR',
    rule: ['pravidlo', 'pravidla', 'pravidel'], units: { CZK: 'Kč', days: 'dní' },
  },
  en: {
    from: 'from', to: 'up to', yes: 'yes', no: 'no', chosen: 'chosen', whole: 'All of Czechia',
    rule: ['rule', 'rules', 'rules'], units: { CZK: 'Kč', days: 'days' },
  },
};

export interface WordsOptions {
  lang?: WordsLang;
  /* Every pick and every place, for a tooltip, where "+2" would hide what is set. */
  full?: boolean;
}

const num = (n: number): string => n.toLocaleString('cs-CZ');

const withUnit = (text: string, unit: string | null, lang: WordsLang): string => {
  const u = unit ? (JOIN[lang].units[unit] ?? unit) : null;
  return u ? `${text} ${u}` : text;
};

/* "50–90 m²", "od 50 m²", "do 90 m²". */
export function rangeWords(
  lo: number | null,
  hi: number | null,
  unit: string | null,
  lang: WordsLang = 'cs',
): string | null {
  if (lo == null && hi == null) return null;
  const w = JOIN[lang];
  const text = lo != null && hi != null ? `${num(lo)}–${num(hi)}` : lo != null ? `${w.from} ${num(lo)}` : `${w.to} ${num(hi!)}`;
  return withUnit(text, unit, lang);
}

/* The first two picks and how many more: "2+kk, 3+kk +2"; every pick when `full`. */
export function listWords(labels: ReadonlyArray<string>, full = false): string | null {
  if (labels.length === 0) return null;
  return full || labels.length <= 2 ? labels.join(', ') : `${labels.slice(0, 2).join(', ')} +${labels.length - 2}`;
}

/* Every place chip, an exclusion with its "−": "Praha, −Žižkov, Brno". */
const everyPlace = (chips: ReadonlyArray<DistrictChip>): string =>
  chips.map((d) => `${d.excluded ? '−' : ''}${d.context ? `${d.name} · ${d.context}` : d.name}`).join(', ');

const optionLabel = (def: FilterDef, v: unknown): string =>
  def.enum_values?.find((o) => o.value === v)?.label_cs ?? String(v);

const countWords = (n: number, [one, few, many]: JoinWords['rule']): string | null =>
  n > 0 ? `${num(n)} ${czPlural(n, one, few, many)}` : null;

function formWords(
  row: Extract<SheetRow, { kind: 'form' }>,
  view: Readonly<Record<string, unknown>>,
  units: Readonly<Record<string, string>>,
  lang: WordsLang,
  full: boolean,
): string | null {
  const { def, maxDef } = row.row;
  const w = JOIN[lang];
  const value = view[def.id];
  const unit = units[def.id] ?? def.unit;
  if (maxDef) {
    return rangeWords((value as number | null) ?? null, (view[maxDef.id] as number | null) ?? null, unit, lang);
  }
  if (value == null) return null;
  switch (def.ui_control) {
    case 'set_scope':
      return scopeWords(def.id as CuratedSet, value as SetScope);
    case 'city_index_rules':
      return countWords((value as unknown[]).length, w.rule);
    case 'multiselect':
      return listWords((value as unknown[]).map((v) => optionLabel(def, v)), full);
    case 'pill_group':
    case 'single_select':
      /* `any` is the registry's own "no constraint" option (status). */
      return value === 'any' ? null : optionLabel(def, value);
    case 'boolean':
      return value === true ? w.yes : null;
    case 'tristate':
      return value ? w.yes : w.no;
    default: {
      const n = value as number;
      return withUnit(def.id.includes('min') ? `≥ ${num(n)}` : num(n), unit, lang);
    }
  }
}

function customWords(
  row: Extract<SheetRow, { kind: 'custom' }>,
  f: ListingFilters,
  lang: WordsLang,
  full: boolean,
): string | null {
  const w = JOIN[lang];
  switch (row.block.key) {
    case 'subtype': {
      const labels = new Map(subtypeOptions(f.categoryMain).map((o) => [o.value, o.label]));
      return listWords(f.subtype.map((s) => labels.get(s) ?? s), full);
    }
    case 'districts':
      if (!f.districts.length) return null;
      return full ? everyPlace(f.districts) : placeLabelOf(f.districts, w.whole);
    case 'portals':
      return portalScopeLabel(f.portals, f.portalsExcluded, f.portalsExcludedActiveOnly);
    case 'market_growth':
      return countWords(f.priceGrowthRules.length, w.rule);
    case 'include_no_price':
      return f.includeNoPrice ? w.yes : null;
    case 'show_dismissed':
      return f.showDismissed ? w.yes : null;
    case 'broker':
      return f.brokerId != null ? w.chosen : null;
    default:
      return null;
  }
}

export function rowWords(
  row: SheetRow,
  f: ListingFilters,
  view: Readonly<Record<string, unknown>>,
  units: Readonly<Record<string, string>>,
  { lang = 'cs', full = false }: WordsOptions = {},
): string | null {
  return row.kind === 'form' ? formWords(row, view, units, lang, full) : customWords(row, f, lang, full);
}

const WORD_CONTROLS = new Set(['set_scope', 'multiselect', 'pill_group', 'single_select', 'boolean', 'tristate']);

/* Whether the row's words are a figure (a range, a bound, a count of rules),
 * which a surface sets in mono, or words, which it sets in its label face. */
export function rowWordsAreNumbers(row: SheetRow): boolean {
  if (row.kind === 'custom') return row.block.key === 'market_growth';
  return row.row.maxDef != null || !WORD_CONTROLS.has(row.row.def.ui_control);
}
