/* The Filtry sheet: Browse's whole registry filter set on the phone, grouped as
 * the desktop sidebar groups it, every filter reachable (the registry walk), a
 * filled sheet surviving the URL, Browse's count of the DRAFT in the footer, and
 * save / update through the preset API in the shapes Browse writes. */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { FEED_ALL_FILTERS, feedSearch, feedViewOf } from '@/lib/feed';
import { BROWSE_FILTER_BANDS } from '@/lib/filterLayout';
import { FILTER_REGISTRY } from '@/lib/filterRegistry.generated';
import { DEFAULT_FILTERS, filtersForPreset, type ListingFilters } from '@/lib/filters';
import * as queries from '@/lib/queries';
import type { FilterPreset } from '@/lib/types';

import FilterSheet from './FilterSheet';
import { sheetBands } from './filterRows';

const never = vi.hoisted(() => () => new Promise<never>(() => {}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  createFilterPreset: vi.fn(),
  updateFilterPreset: vi.fn(),
  listCollections: vi.fn(never),
  listTags: vi.fn(never),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchBrowseCount: vi.fn(),
  fetchNoPriceCount: vi.fn(never),
  fetchDismissedCount: vi.fn(never),
  fetchPipelineStages: vi.fn(never),
}));
vi.mock('@/lib/pgRead', () => ({ pgRead: vi.fn(never) }));

/* Browse filters neither surface draws, each with its reason. A browse filter in
 * neither this list nor the sheet fails the walk: a registry addition has to be
 * PLACED (lib/filterLayout), so it reaches the desktop and the phone together. */
const NOT_ON_SHEET: Record<string, string> = {
  location: 'the centre + radius drawn on the full-page map — the phone has no map',
  category_sub_cb: 'dropped from the sidebar in #300: the registry entry has no options',
  min_garden_area: 'not placed on the desktop sidebar either',
  max_garden_area: 'not placed on the desktop sidebar either',
};

/* Every cohort-conditional group open: a house cohort shows Subtype, Prodej shows Yield. */
const HOUSES_FOR_SALE: ListingFilters = { ...DEFAULT_FILTERS, categoryMain: ['dum'], categoryType: 'prodej' };

const PRESET: FilterPreset = {
  id: 'vys',
  name: 'Vysočany byty',
  filter_spec: { filters: { ...DEFAULT_FILTERS, categoryType: 'prodej', dispositions: ['2+kk'] }, sort: '-price_czk' },
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  position: 0,
  color: null,
};

beforeEach(() => {
  vi.mocked(queries.fetchBrowseCount).mockResolvedValue(42);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderSheet(props: Partial<React.ComponentProps<typeof FilterSheet>> = {}) {
  const onApply = vi.fn();
  const onClose = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <FilterSheet
          filters={FEED_ALL_FILTERS}
          sort={queries.DEFAULT_SORT}
          base={FEED_ALL_FILTERS}
          preset={null}
          onApply={onApply}
          onClose={onClose}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onApply, onClose, qc };
}

const sheet = () => screen.getByRole('dialog', { name: 'Filtry' });

describe('the registry walk', () => {
  it('draws a row for every browse filter in the registry', () => {
    renderSheet({ filters: HOUSES_FOR_SALE });
    const drawn = new Set(
      [...sheet().querySelectorAll('[data-filter-ids]')].flatMap((el) =>
        (el.getAttribute('data-filter-ids') ?? '').split(' ').filter(Boolean),
      ),
    );
    const browse = FILTER_REGISTRY.filters.filter((f) => f.agendas.includes('browse')).map((f) => f.id);
    const missing = browse.filter((id) => !drawn.has(id) && !(id in NOT_ON_SHEET));
    expect(missing, 'browse filters with no row on the phone — place them in lib/filterLayout').toEqual([]);
    /* The allowlist cannot go stale: a listed filter that gained a row leaves it. */
    expect(Object.keys(NOT_ON_SHEET).filter((id) => drawn.has(id))).toEqual([]);
    expect(drawn.size).toBe(browse.length - Object.keys(NOT_ON_SHEET).length);
  });

  it("groups and orders the rows as the desktop sidebar does, the map's group left out", () => {
    renderSheet({ filters: HOUSES_FOR_SALE });
    const bands = within(sheet()).getAllByRole('region').map((el) => el.getAttribute('aria-label'));
    expect(bands).toEqual(BROWSE_FILTER_BANDS.map((b) => b.title));
    const groups = sheetBands(HOUSES_FOR_SALE).map((b) => [b.title, b.groups.map((g) => g.title)]);
    expect(groups).toEqual([
      ['Essentials', ['Category', 'Subtype', 'Status', 'Location', 'Disposition', 'Size', 'Condition & material', 'Price', 'Yield']],
      ['Features', ['Unit', 'Amenities']],
      ['Curation & quality', ['Curation', 'City quality', 'Advanced city-quality rules', 'Market growth (datasets)']],
      ['Other', ['Source portal', 'Velocity']],
    ]);
    /* Řazení leads, then the sidebar's own words. */
    const rows = [...sheet().querySelectorAll('[data-filter-row]')].map((el) => el.getAttribute('data-filter-row'));
    expect(rows.slice(0, 4)).toEqual(['sort', 'category_main_in', 'category_type', 'subtype']);
    expect(within(sheet()).getByRole('button', { name: /^Listing for/ })).toBeInTheDocument();
  });

  it('shows Subtype and Yield only for the cohorts the sidebar shows them for', () => {
    const titles = (f: ListingFilters) => sheetBands(f)[0].groups.map((g) => g.title);
    expect(titles(DEFAULT_FILTERS)).not.toContain('Subtype');
    expect(titles(DEFAULT_FILTERS)).not.toContain('Yield');
    expect(titles(HOUSES_FOR_SALE)).toEqual(expect.arrayContaining(['Subtype', 'Yield']));
  });
});

/* Every field any row edits, set away from its default. */
const FILLED: ListingFilters = {
  ...DEFAULT_FILTERS,
  categoryMain: ['dum', 'komercni'],
  categoryType: 'prodej',
  subtype: ['vila'],
  status: 'inactive',
  recentlyAddedDays: 7,
  recentlyChangedDays: 3,
  districts: [
    { name: 'Praha', context: null, level: 'obec', id: 554782 },
    { name: 'Žižkov', context: 'Praha', level: 'cast_obce', id: 400301, excluded: true },
  ],
  dispositions: ['2+kk', '3+1'],
  areaMin: 50,
  areaMax: 90.5,
  estateAreaMin: 300,
  estateAreaMax: 900,
  usableAreaMin: 40,
  usableAreaMax: 120,
  conditionMatch: ['novostavba'],
  buildingMaterial: ['cihla'],
  buildingConditionLevelMin: 2,
  buildingConditionLevelMax: 4,
  apartmentConditionLevelMin: 1,
  apartmentConditionLevelMax: 3,
  priceMin: 2_000_000,
  priceMax: 9_000_000,
  includeNoPrice: true,
  pricePerM2Min: 50_000,
  pricePerM2Max: 140_000,
  priceChangeCountMin: 2,
  priceChangeWindowDays: 90,
  totalPriceChangePct: -10,
  mfGrossYieldPctMin: 3.5,
  mfGrossYieldPctMax: 6,
  furnished: ['ano', '__unknown__'],
  ownership: ['osobni'],
  hasBalcony: 'yes',
  hasLift: 'no',
  hasParking: 'yes',
  terrace: 'no',
  cellar: 'yes',
  garage: 'no',
  parkingLotsMin: 2,
  pipeline: { stage_ids: [3, 5] },
  collections: [4, 9],
  withEstimates: true,
  tags: [11],
  tagsExcluded: [12],
  showDismissed: true,
  minCityPopulation: 20_000,
  maxCityPopulation: 400_000,
  nearPop5kmMin: 50_000,
  nearPop15kmMin: 100_000,
  nearJobs5kmMin: 2.5,
  nearJobs15kmMin: 3,
  nearYouth5kmMin: 1.5,
  nearYouth15kmMin: 2,
  nearOverall5kmMin: 4,
  nearOverall15kmMin: 4.5,
  cityIndexRules: [{ index_name: 'bezpecnost', op: '>=', value: 6 }],
  priceGrowthRules: [{ datasetId: 3, fromYm: '2020-01', toYm: '2025-06', rentMinPct: 2, saleMinPct: null }],
  portals: ['idnes'],
  portalsExcluded: ['sreality', 'bazos'],
  portalsExcludedActiveOnly: true,
  firstSeenMinDays: 1,
  firstSeenMaxDays: 30,
  lastSeenMinDays: 0,
  lastSeenMaxDays: 2,
  tomDaysMin: 5,
  tomDaysMax: 60,
  tomObservedOnly: true,
};

describe('a filled sheet in the URL', () => {
  it('sets every field a row edits (so the round trip below covers them all)', () => {
    const fields = sheetBands(FILLED).flatMap((b) => b.groups.flatMap((g) => g.rows.flatMap((r) => r.fields)));
    const unset = fields.filter((k) => JSON.stringify(FILLED[k]) === JSON.stringify(DEFAULT_FILTERS[k]));
    expect(unset).toEqual([]);
  });

  it("survives Browse's URL contract, the order and the preset with it", () => {
    const view = { filters: FILLED, sort: { field: 'price_per_m2' as const, direction: 'desc' as const }, presetId: 'vys' };
    const sp = feedSearch(view);
    const back = feedViewOf(new URLSearchParams(sp.toString()));
    expect(back).toEqual(view);
    /* …and the same query string means the same filters on /browse. */
    expect(queries.parseSort(sp.get('sort'))).toEqual(view.sort);
  });
});

describe('the footer', () => {
  it("counts the DRAFT with Browse's count, and shows it on the button", async () => {
    const user = userEvent.setup();
    const { onApply, onClose } = renderSheet();
    await user.click(within(sheet()).getByRole('button', { name: /^Disposition/ }));
    await user.click(within(sheet()).getByRole('button', { name: '2+kk' }));
    await waitFor(() =>
      expect(vi.mocked(queries.fetchBrowseCount)).toHaveBeenCalledWith(
        { ...FEED_ALL_FILTERS, dispositions: ['2+kk'] },
        expect.anything(),
      ),
    );
    const show = await within(sheet()).findByRole('button', { name: 'Zobrazit 42 nemovitostí' });
    /* Nothing reaches the feed until the button. */
    expect(onApply).not.toHaveBeenCalled();
    await user.click(show);
    expect(onApply).toHaveBeenCalledWith({
      filters: { ...FEED_ALL_FILTERS, dispositions: ['2+kk'] },
      sort: queries.DEFAULT_SORT,
      presetId: null,
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('applies the sheet’s order with the filters', async () => {
    const user = userEvent.setup();
    const { onApply } = renderSheet();
    await user.click(within(sheet()).getByRole('button', { name: /^Řazení/ }));
    await user.click(within(sheet()).getByRole('button', { name: 'Price: low → high' }));
    await user.click(await within(sheet()).findByRole('button', { name: /^Zobrazit/ }));
    expect(onApply.mock.calls[0][0].sort).toEqual({ field: 'price_czk', direction: 'asc' });
  });

  it("Vymazat returns the draft to the scope's own filters", async () => {
    const user = userEvent.setup();
    const base = { ...FEED_ALL_FILTERS, dispositions: ['2+kk' as const] };
    const { onApply } = renderSheet({ filters: { ...base, priceMax: 5_000_000 }, base });
    await user.click(within(sheet()).getByRole('button', { name: 'Vymazat' }));
    expect(within(sheet()).getByRole('button', { name: 'Vymazat' })).toBeDisabled();
    await user.click(within(sheet()).getByRole('button', { name: /^Zobrazit/ }));
    expect(onApply.mock.calls[0][0].filters).toEqual(base);
  });

  it('offers the preset menu only once the draft differs from its scope', async () => {
    const user = userEvent.setup();
    renderSheet();
    expect(within(sheet()).queryByRole('button', { name: 'Předvolba' })).toBeNull();
    await user.click(within(sheet()).getByRole('button', { name: /^Disposition/ }));
    await user.click(within(sheet()).getByRole('button', { name: '2+kk' }));
    expect(within(sheet()).getByRole('button', { name: 'Předvolba' })).toBeInTheDocument();
  });
});

describe('presets from the phone', () => {
  const presetBase = filtersForPreset({ ...DEFAULT_FILTERS, categoryType: 'prodej', dispositions: ['2+kk'] }, true);
  const draft: ListingFilters = { ...presetBase, priceMax: 6_000_000, pipeline: { stage_ids: [] } };

  it('saves the draft as a new preset — filters (lenses dropped) and the order — and selects it', async () => {
    const user = userEvent.setup();
    vi.mocked(api.createFilterPreset).mockResolvedValue({ ...PRESET, id: 'new', name: 'Do 6 M' });
    const { onApply } = renderSheet({ filters: draft, base: presetBase, preset: PRESET });
    await user.click(within(sheet()).getByRole('button', { name: 'Předvolba' }));
    const menu = screen.getByRole('dialog', { name: 'Předvolba' });
    await user.type(within(menu).getByRole('textbox', { name: 'Název předvolby' }), 'Do 6 M');
    await user.click(within(menu).getByRole('button', { name: 'Uložit' }));
    await waitFor(() => expect(api.createFilterPreset).toHaveBeenCalled());
    expect(vi.mocked(api.createFilterPreset).mock.calls[0][0]).toEqual({
      name: 'Do 6 M',
      filter_spec: { filters: filtersForPreset(draft, true), sort: '-first_seen_at' },
      color: null,
    });
    /* The pipeline scope never rides into a preset (rule 22). */
    expect(vi.mocked(api.createFilterPreset).mock.calls[0][0].filter_spec.filters.pipeline).toBeNull();
    await waitFor(() => expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ presetId: 'new' })));
  });

  it('updates the chosen preset in two steps, keeping its stored order', async () => {
    const user = userEvent.setup();
    vi.mocked(api.updateFilterPreset).mockResolvedValue(PRESET);
    const { onApply } = renderSheet({ filters: draft, base: presetBase, preset: PRESET });
    await user.click(within(sheet()).getByRole('button', { name: 'Předvolba' }));
    const menu = screen.getByRole('dialog', { name: 'Předvolba' });
    await user.click(within(menu).getByRole('button', { name: 'Aktualizovat předvolbu' }));
    expect(api.updateFilterPreset).not.toHaveBeenCalled();
    await user.click(within(menu).getByRole('button', { name: 'Přepsat' }));
    await waitFor(() => expect(api.updateFilterPreset).toHaveBeenCalled());
    expect(vi.mocked(api.updateFilterPreset).mock.calls[0]).toEqual([
      'vys',
      { filter_spec: { filters: filtersForPreset(draft, true), sort: '-price_czk' } },
    ]);
    await waitFor(() => expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ presetId: 'vys' })));
  });

  it('offers no update while Vše is the scope', async () => {
    const user = userEvent.setup();
    renderSheet({ filters: { ...FEED_ALL_FILTERS, priceMax: 6_000_000 } });
    await user.click(within(sheet()).getByRole('button', { name: 'Předvolba' }));
    const menu = screen.getByRole('dialog', { name: 'Předvolba' });
    expect(within(menu).queryByRole('button', { name: 'Aktualizovat předvolbu' })).toBeNull();
    expect(within(menu).getByRole('textbox', { name: 'Název předvolby' })).toBeInTheDocument();
  });
});

describe('390 px', () => {
  /* jsdom lays nothing out; what can be pinned is that the sheet's own classes
   * carry no fixed width wider than a 390 px phone minus its 16 px gutters. */
  it('sets no fixed width over 358 px', () => {
    const files = ['FilterSheet.tsx', 'BottomSheet.tsx', 'filterRows.ts', 'filterSummary.ts'];
    const widths: string[] = [];
    for (const f of files) {
      const src = readFileSync(join(process.cwd(), 'src/components/phone', f), 'utf8');
      for (const m of src.matchAll(/\b(?:min-|max-)?w-\[(\d+)px\]/g)) if (Number(m[1]) > 358) widths.push(`${f}: ${m[0]}`);
      for (const m of src.matchAll(/\b(?:min-|max-)?w-(\d+)\b/g)) if (Number(m[1]) * 4 > 358) widths.push(`${f}: ${m[0]}`);
      for (const m of src.matchAll(/width:\s*['"]?(\d+)px/g)) if (Number(m[1]) > 358) widths.push(`${f}: ${m[0]}`);
    }
    expect(widths).toEqual([]);
  });
});
