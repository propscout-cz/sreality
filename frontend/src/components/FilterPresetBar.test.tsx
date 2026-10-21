/* FilterPresetBar — the preset instance of THE saved-set bar.
 *
 * Chip names: the "edited since loaded" bullet used to carry aria-label="edited",
 * which is injected into the enclosing load button's name ("Praha 2+kk edited");
 * the kebab was named by its glyph "⋯" alone. This pins the chip's load button
 * to the preset NAME (state moved to the description) and the kebab to a
 * name-scoped label, so N chips no longer expose N identical "⋯" buttons.
 *
 * The rest pins what the fold into SavedSetBar must not change: the Pipeline
 * chip leads the row, "Update preset" re-captures filters + sort, a new preset
 * offers the map area only while one is applied, and Edit touches metadata only.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { filterPresetKeys, DEFAULT_SORT, sortToParam } from '@/lib/queries';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import type { FilterPreset } from '@/lib/types';

const preset: FilterPreset = {
  id: 'p1',
  name: 'Praha 2+kk',
  // A real sort other than the current one → the chip renders dirty (an unknown
  // one reads as the default once a stored sort is normalized, and would be clean).
  filter_spec: { filters: {} as ListingFilters, sort: '-price_czk' },
  created_at: '2026-05-12T10:00:00+00:00',
  updated_at: '2026-05-12T10:00:00+00:00',
  position: 0,
  color: null,
};

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  isApiConfigured: () => true,
  listFilterPresets: vi.fn(),
  createFilterPreset: vi.fn(),
  updateFilterPreset: vi.fn(),
  deleteFilterPreset: vi.fn(),
}));

import { createFilterPreset, deleteFilterPreset, updateFilterPreset } from '@/lib/api';
import FilterPresetBar from './FilterPresetBar';

const BOUNDS = { west: 14.3, south: 50.0, east: 14.5, north: 50.1 };

function renderBar({
  filters = {} as ListingFilters,
  activePresetId = 'p1',
  presets = [preset],
  onActivePresetIdChange = vi.fn(),
  onLoad = vi.fn(),
  onLoadPipelineView = vi.fn(),
}: {
  filters?: ListingFilters;
  activePresetId?: string | null;
  presets?: FilterPreset[];
  onActivePresetIdChange?: (id: string | null) => void;
  onLoad?: (p: FilterPreset) => void;
  onLoadPipelineView?: () => void;
} = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(filterPresetKeys.all, { data: presets, total: presets.length });
  return render(
    <QueryClientProvider client={qc}>
      <FilterPresetBar
        filters={filters}
        sort={DEFAULT_SORT}
        activePresetId={activePresetId}
        onLoad={onLoad}
        onActivePresetIdChange={onActivePresetIdChange}
        onFiltersChange={vi.fn()}
        onLoadPipelineView={onLoadPipelineView}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(createFilterPreset).mockReset();
  vi.mocked(updateFilterPreset).mockReset();
  vi.mocked(deleteFilterPreset).mockReset();
});

describe('<FilterPresetBar> chip names', () => {
  it('names the load button by the preset name, with "edited" as a description', () => {
    renderBar();
    const load = screen.getByRole('button', { name: 'Praha 2+kk' });
    expect(load).toHaveAccessibleName('Praha 2+kk');
    expect(load).toHaveAccessibleDescription('Edited since loaded');
  });

  it('names the kebab by what it opens, scoped to its preset', () => {
    renderBar();
    const kebab = screen.getByRole('button', { name: 'Preset options: Praha 2+kk' });
    expect(kebab).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('<FilterPresetBar> behaviour', () => {
  it('leads with the Pipeline chip, which loads its view', () => {
    const onLoadPipelineView = vi.fn();
    renderBar({ onLoadPipelineView });
    const buttons = screen.getAllByRole('button');
    expect(buttons[0]).toHaveTextContent('Pipeline');
    fireEvent.click(buttons[0]);
    expect(onLoadPipelineView).toHaveBeenCalledTimes(1);
  });

  it('loads a preset on a chip click', () => {
    const onLoad = vi.fn();
    renderBar({ onLoad, activePresetId: null });
    fireEvent.click(screen.getByRole('button', { name: 'Praha 2+kk' }));
    expect(onLoad).toHaveBeenCalledWith(preset);
  });

  it('offers "Update preset" on a dirty preset and saves filters + sort back into it', async () => {
    vi.mocked(updateFilterPreset).mockResolvedValue(preset);
    renderBar({ filters: DEFAULT_FILTERS });
    fireEvent.click(screen.getByRole('button', { name: 'Update preset' }));
    const dialog = screen.getByRole('dialog', { name: 'Update preset' });
    expect(within(dialog).queryByRole('button', { name: /map area/ })).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update preset' }));
    await waitFor(() => expect(updateFilterPreset).toHaveBeenCalledTimes(1));
    const [id, patch] = vi.mocked(updateFilterPreset).mock.calls[0];
    expect(id).toBe('p1');
    expect(patch).toMatchObject({ name: 'Praha 2+kk', color: null, filter_spec: { sort: sortToParam(DEFAULT_SORT) } });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('offers no update while the loaded preset is clean', () => {
    renderBar({ presets: [{ ...preset, filter_spec: { filters: DEFAULT_FILTERS, sort: null } }], filters: DEFAULT_FILTERS });
    expect(screen.getByRole('button', { name: 'Praha 2+kk' })).toHaveAccessibleDescription('Load preset: Praha 2+kk');
    expect(screen.queryByRole('button', { name: 'Update preset' })).toBeNull();
  });

  it('saves a new preset with the map area only when asked, and selects it', async () => {
    const onActivePresetIdChange = vi.fn();
    vi.mocked(createFilterPreset).mockResolvedValue({ ...preset, id: 'p2', name: 'Okno' });
    renderBar({ filters: { ...DEFAULT_FILTERS, bounds: BOUNDS }, activePresetId: null, onActivePresetIdChange });
    fireEvent.click(screen.getByRole('button', { name: '+ Save preset' }));
    const dialog = screen.getByRole('dialog', { name: 'Save filters as a preset' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Preset name' }), { target: { value: 'Okno' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Include the current map area in this preset' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save preset' }));
    await waitFor(() => expect(createFilterPreset).toHaveBeenCalledTimes(1));
    const input = vi.mocked(createFilterPreset).mock.calls[0][0];
    expect(input.name).toBe('Okno');
    expect(input.filter_spec.filters.bounds).toEqual(BOUNDS);
    await waitFor(() => expect(onActivePresetIdChange).toHaveBeenCalledWith('p2'));
  });

  it('drops the map area from a new preset by default', async () => {
    vi.mocked(createFilterPreset).mockResolvedValue({ ...preset, id: 'p2' });
    renderBar({ filters: { ...DEFAULT_FILTERS, bounds: BOUNDS }, activePresetId: null });
    fireEvent.click(screen.getByRole('button', { name: '+ Save preset' }));
    const dialog = screen.getByRole('dialog', { name: 'Save filters as a preset' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Preset name' }), { target: { value: 'Kritéria' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save preset' }));
    await waitFor(() => expect(createFilterPreset).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createFilterPreset).mock.calls[0][0].filter_spec.filters.bounds).toBeNull();
  });

  it('edits the name and colour only', async () => {
    vi.mocked(updateFilterPreset).mockResolvedValue(preset);
    renderBar();
    fireEvent.click(screen.getByRole('button', { name: 'Preset options: Praha 2+kk' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit…' }));
    const dialog = screen.getByRole('dialog', { name: 'Edit preset' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updateFilterPreset).toHaveBeenCalledTimes(1));
    expect(vi.mocked(updateFilterPreset).mock.calls[0][1]).toEqual({
      name: 'Praha 2+kk',
      filter_spec: undefined,
      color: null,
    });
  });

  it('deletes behind a confirm, and deselects the active preset', async () => {
    const onActivePresetIdChange = vi.fn();
    vi.mocked(deleteFilterPreset).mockResolvedValue({ deleted: true });
    renderBar({ onActivePresetIdChange });
    fireEvent.click(screen.getByRole('button', { name: 'Preset options: Praha 2+kk' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete…' }));
    expect(deleteFilterPreset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Confirm delete' }));
    await waitFor(() => expect(deleteFilterPreset).toHaveBeenCalledWith('p1'));
    await waitFor(() => expect(onActivePresetIdChange).toHaveBeenCalledWith(null));
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
  });
});
