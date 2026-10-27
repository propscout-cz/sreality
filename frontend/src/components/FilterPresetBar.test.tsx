/* FilterPresetBar — the preset instance of THE saved-set bar.
 *
 * Chip names: the "edited since loaded" bullet used to carry aria-label="edited",
 * which is injected into the enclosing load button's name ("Praha 2+kk edited");
 * the kebab was named by its glyph "⋯" alone. This pins the chip's load button
 * to the preset NAME (state moved to the description) and the kebab to a
 * name-scoped label, so N chips no longer expose N identical "⋯" buttons.
 *
 * The rest pins what the fold into SavedSetBar must not change: the Pipeline
 * chip leads the row, the menu's "Save changes" re-captures filters + sort (every filter,
 * the curation lenses included — ruling 2026-10-22), a new preset
 * offers the map area only while one is applied (ticked for a new preset), and Edit
 * touches metadata only.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { filterPresetKeys, DEFAULT_SORT, sortToParam } from '@/lib/queries';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import type { FilterPreset, FilterPresetGroup } from '@/lib/types';

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
  group_id: null,
};

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  isApiConfigured: () => true,
  listFilterPresets: vi.fn(),
  createFilterPreset: vi.fn(),
  updateFilterPreset: vi.fn(),
  deleteFilterPreset: vi.fn(),
  layoutFilterPresets: vi.fn(),
  createFilterPresetGroup: vi.fn(),
  deleteFilterPresetGroup: vi.fn(),
}));

import {
  createFilterPreset, createFilterPresetGroup, deleteFilterPreset, deleteFilterPresetGroup, layoutFilterPresets,
  updateFilterPreset,
} from '@/lib/api';
import FilterPresetBar, { PRESETS_EXPANDED_KEY } from './FilterPresetBar';

const BOUNDS = { west: 14.3, south: 50.0, east: 14.5, north: 50.1 };

/* The dirty chip's menu: Save changes writes at once (no dialog, operator 2026-10-27). */
const saveChanges = () => {
  fireEvent.click(screen.getByRole('button', { name: 'Preset options: Praha 2+kk' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Save changes' }));
};

function renderBar({
  filters = {} as ListingFilters,
  activePresetId = 'p1',
  presets = [preset],
  groups = [],
  onActivePresetIdChange = vi.fn(),
  onLoad = vi.fn(),
  onLoadPipelineView = vi.fn(),
}: {
  filters?: ListingFilters;
  activePresetId?: string | null;
  presets?: FilterPreset[];
  groups?: FilterPresetGroup[];
  onActivePresetIdChange?: (id: string | null) => void;
  onLoad?: (p: FilterPreset) => void;
  onLoadPipelineView?: () => void;
} = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(filterPresetKeys.all, { data: presets, total: presets.length, groups });
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
  localStorage.removeItem(PRESETS_EXPANDED_KEY);
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
    fireEvent.click(screen.getByRole('button', { name: /^Presety/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Praha 2+kk' }));
    expect(onLoad).toHaveBeenCalledWith(preset);
  });

  /* Eleven presets wrapped onto three rows above the map; folded, the bar keeps
   * one row and the preset in force. */
  it('folds to the active preset, and the Presety toggle unfolds them all, remembered', () => {
    const other = { ...preset, id: 'p2', name: 'Brno 3+1' };
    const { unmount } = renderBar({ presets: [preset, other], activePresetId: 'p1' });
    const toggle = screen.getByRole('button', { name: /^Presety/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveTextContent('2');
    expect(screen.getByRole('button', { name: 'Praha 2+kk' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Brno 3+1' })).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Brno 3+1' })).toBeInTheDocument();
    unmount();
    renderBar({ presets: [preset, other], activePresetId: 'p1' });
    expect(screen.getByRole('button', { name: /^Presety/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Brno 3+1' })).toBeInTheDocument();
  });

  it('saves filters + sort back into a dirty preset from its menu, with no dialog', async () => {
    vi.mocked(updateFilterPreset).mockResolvedValue(preset);
    renderBar({ filters: DEFAULT_FILTERS });
    saveChanges();
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(updateFilterPreset).toHaveBeenCalledTimes(1));
    const [id, patch] = vi.mocked(updateFilterPreset).mock.calls[0];
    expect(id).toBe('p1');
    expect(patch).toMatchObject({ name: 'Praha 2+kk', color: null, filter_spec: { sort: sortToParam(DEFAULT_SORT) } });
  });

  it('reverts a dirty preset by loading it again', () => {
    const onLoad = vi.fn();
    renderBar({ onLoad });
    fireEvent.click(screen.getByRole('button', { name: 'Preset options: Praha 2+kk' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Revert to saved' }));
    expect(onLoad).toHaveBeenCalledWith(preset);
    expect(updateFilterPreset).not.toHaveBeenCalled();
  });

  it('saves a drifted curation filter into the preset', async () => {
    vi.mocked(updateFilterPreset).mockResolvedValue(preset);
    const outside = { set: 'out' as const, in: [], out: [] };
    renderBar({
      presets: [{ ...preset, filter_spec: { filters: DEFAULT_FILTERS, sort: null } }],
      filters: { ...DEFAULT_FILTERS, pipeline: outside, collections: outside },
    });
    saveChanges();
    await waitFor(() => expect(updateFilterPreset).toHaveBeenCalledTimes(1));
    const saved = vi.mocked(updateFilterPreset).mock.calls[0][1].filter_spec?.filters;
    expect(saved?.pipeline).toEqual(outside);
    expect(saved?.collections).toEqual(outside);
  });

  it('offers no save while the loaded preset is clean', () => {
    renderBar({ presets: [{ ...preset, filter_spec: { filters: DEFAULT_FILTERS, sort: null } }], filters: DEFAULT_FILTERS });
    expect(screen.getByRole('button', { name: 'Praha 2+kk' })).toHaveAccessibleDescription('Load preset: Praha 2+kk');
    fireEvent.click(screen.getByRole('button', { name: 'Preset options: Praha 2+kk' }));
    expect(screen.queryByRole('menuitem', { name: 'Save changes' })).toBeNull();
  });

  it('saves a new preset with the map area by default, and selects it', async () => {
    const onActivePresetIdChange = vi.fn();
    vi.mocked(createFilterPreset).mockResolvedValue({ ...preset, id: 'p2', name: 'Okno' });
    renderBar({ filters: { ...DEFAULT_FILTERS, bounds: BOUNDS }, activePresetId: null, onActivePresetIdChange });
    fireEvent.click(screen.getByRole('button', { name: '+ Save preset' }));
    const dialog = screen.getByRole('dialog', { name: 'Save filters as a preset' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Preset name' }), { target: { value: 'Okno' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save preset' }));
    await waitFor(() => expect(createFilterPreset).toHaveBeenCalledTimes(1));
    const input = vi.mocked(createFilterPreset).mock.calls[0][0];
    expect(input.name).toBe('Okno');
    expect(input.filter_spec.filters.bounds).toEqual(BOUNDS);
    await waitFor(() => expect(onActivePresetIdChange).toHaveBeenCalledWith('p2'));
  });

  it('drops the map area from a new preset when unticked', async () => {
    vi.mocked(createFilterPreset).mockResolvedValue({ ...preset, id: 'p2' });
    renderBar({ filters: { ...DEFAULT_FILTERS, bounds: BOUNDS }, activePresetId: null });
    fireEvent.click(screen.getByRole('button', { name: '+ Save preset' }));
    const dialog = screen.getByRole('dialog', { name: 'Save filters as a preset' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Preset name' }), { target: { value: 'Kritéria' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Include the current map area in this preset' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save preset' }));
    await waitFor(() => expect(createFilterPreset).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createFilterPreset).mock.calls[0][0].filter_spec.filters.bounds).toBeNull();
  });

  it('keeps a preset saved without the map area without it on save', async () => {
    vi.mocked(updateFilterPreset).mockResolvedValue(preset);
    renderBar({ filters: { ...DEFAULT_FILTERS, bounds: BOUNDS } });
    saveChanges();
    await waitFor(() => expect(updateFilterPreset).toHaveBeenCalledTimes(1));
    expect(vi.mocked(updateFilterPreset).mock.calls[0][1].filter_spec?.filters.bounds).toBeNull();
  });

  it('keeps a preset saved with a map area with the current one on save', async () => {
    vi.mocked(updateFilterPreset).mockResolvedValue(preset);
    const before = { ...BOUNDS, north: 50.2 };
    renderBar({
      presets: [{ ...preset, filter_spec: { filters: { ...DEFAULT_FILTERS, bounds: before }, sort: null } }],
      filters: { ...DEFAULT_FILTERS, bounds: BOUNDS },
    });
    saveChanges();
    await waitFor(() => expect(updateFilterPreset).toHaveBeenCalledTimes(1));
    expect(vi.mocked(updateFilterPreset).mock.calls[0][1].filter_spec?.filters.bounds).toEqual(BOUNDS);
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

describe('<FilterPresetBar> groups (migration 672)', () => {
  const GROUP: FilterPresetGroup = {
    id: 'g1', name: 'Praha', color: null, position: 0,
    created_at: '2026-10-25T00:00:00+00:00', updated_at: '2026-10-25T00:00:00+00:00',
  };
  const other: FilterPreset = { ...preset, id: 'p2', name: 'Brno 3+kk', group_id: 'g1' };

  beforeEach(() => {
    localStorage.setItem(PRESETS_EXPANDED_KEY, '1');
    vi.mocked(layoutFilterPresets).mockReset();
    vi.mocked(createFilterPresetGroup).mockReset();
    vi.mocked(deleteFilterPresetGroup).mockReset();
  });

  it('draws the listed groups as boxes, each preset in its own', () => {
    renderBar({ presets: [preset, other], groups: [GROUP] });
    const box = screen.getByRole('group', { name: 'Praha' });
    expect(within(box).getByRole('button', { name: 'Brno 3+kk' })).toBeInTheDocument();
    expect(within(box).queryByRole('button', { name: 'Praha 2+kk' })).toBeNull();
  });

  it('writes a menu move as the whole layout, painting it before the answer', async () => {
    vi.mocked(layoutFilterPresets).mockReturnValue(new Promise(() => {}));
    renderBar({ presets: [preset, other], groups: [GROUP] });
    fireEvent.click(screen.getByRole('button', { name: 'Preset options: Praha 2+kk' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Move to Praha' }));
    await waitFor(() =>
      expect(layoutFilterPresets).toHaveBeenCalledWith({
        groups: ['g1'],
        items: [
          { id: 'p2', group_id: 'g1' },
          { id: 'p1', group_id: 'g1' },
        ],
      }),
    );
    const box = screen.getByRole('group', { name: 'Praha' });
    await waitFor(() => expect(within(box).getByRole('button', { name: 'Praha 2+kk' })).toBeInTheDocument());
  });

  it('creates a group, and deletes one behind a confirm', async () => {
    vi.mocked(createFilterPresetGroup).mockResolvedValue({ ...GROUP, id: 'g2', name: 'Ostrava' });
    vi.mocked(deleteFilterPresetGroup).mockResolvedValue({ deleted: true });
    renderBar({ presets: [preset, other], groups: [GROUP] });
    fireEvent.click(screen.getByRole('button', { name: '+ New group' }));
    const dialog = screen.getByRole('dialog', { name: 'New preset group' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Group name' }), { target: { value: 'Ostrava' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create group' }));
    await waitFor(() => expect(createFilterPresetGroup).toHaveBeenCalledWith({ name: 'Ostrava', color: null }));

    fireEvent.click(screen.getByRole('button', { name: 'Group options: Praha' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete group…' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete group, keep presets' }));
    await waitFor(() => expect(deleteFilterPresetGroup).toHaveBeenCalledWith('g1'));
  });
});
