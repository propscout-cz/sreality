/* Browse's saved filter presets (migration 151) — the preset instance of THE
 * saved-set bar (components/SavedSetBar). A preset is a named filter set + sort
 * order: a chip click restores both (Browse owns the URL write, `onLoad`), and
 * editing either marks it dirty. A preset never fires a notification, unlike a
 * Watchdog. A drag PUTs the new order (migration 198) through
 * lib/useOptimisticWrite: a failure snaps the chips back, says why, re-reads.
 * Without the API only the Pipeline chip that leads the row is drawn. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createFilterPreset,
  deleteFilterPreset,
  isApiConfigured,
  listFilterPresets,
  reorderFilterPresets,
  updateFilterPreset,
} from '@/lib/api';
import {
  filterPresetKeys,
  DEFAULT_SORT,
  sortToParam,
  type SortSpec,
} from '@/lib/queries';
import {
  filtersEqualForPreset,
  filtersForPreset,
  readPresetSpec,
  type ListingFilters,
  type PresetSpec,
} from '@/lib/filters';
import { normalizeScope, scopeIncludes } from '@/lib/setScope';
import type { FilterPreset, TagColor } from '@/lib/types';
import { cachePatch, useOptimisticWrite } from '@/lib/useOptimisticWrite';
import SavedSetBar, { chipBase, type SavedSetCopy } from '@/components/SavedSetBar';
import PipelineMark from '@/components/PipelineMark';

export interface FilterPresetBarProps {
  filters: ListingFilters;
  sort: SortSpec;
  activePresetId: string | null;
  onLoad: (preset: FilterPreset) => void;
  onActivePresetIdChange: (id: string | null) => void;
  /* The Pipeline chip turning OFF is a plain filter edit… */
  onFiltersChange: (next: ListingFilters) => void;
  /* …turning ON loads a whole view (neutral cohort + scope, preset deselected)
   * — one atomic write, see browseState.loadPipelineView. */
  onLoadPipelineView: () => void;
}

type PresetsResponse = { data: FilterPreset[]; total: number };

export const PRESET_COPY: SavedSetCopy = {
  kind: 'Filter preset',
  empty: 'No saved presets yet.',
  save: '+ Save preset',
  saveHint: 'Save the current filters as a new preset',
  update: 'Update preset',
  updateHint: 'Save the current filter changes back into this preset',
  dialog: { save: 'Save filters as a preset', update: 'Update preset', edit: 'Edit preset' },
  submit: { save: 'Save preset', update: 'Update preset', edit: 'Save' },
  nameLabel: 'Preset name',
  namePlaceholder: 'e.g. 2+kk Praha pod 6M',
  color: 'Color',
  cancel: 'Cancel',
  saving: 'Saving…',
  edit: 'Edit…',
  delete: 'Delete…',
  confirmDelete: 'Confirm delete',
  deleting: 'Deleting…',
  load: (name) => `Load preset: ${name}`,
  reorder: (name) => `Reorder preset: ${name}`,
  dragHint: 'Drag to reorder',
  options: (name) => `Preset options: ${name}`,
  optionsHint: 'Preset options',
  edited: 'Edited since loaded',
  editedHint: 'Edited since loaded — use Update to save changes',
  failed: 'Something went wrong.',
};

export default function FilterPresetBar({
  filters,
  sort,
  activePresetId,
  onLoad,
  onActivePresetIdChange,
  onFiltersChange,
  onLoadPipelineView,
}: FilterPresetBarProps) {
  const enabled = isApiConfigured();
  const qc = useQueryClient();

  const presetsQ = useQuery({
    queryKey: filterPresetKeys.all,
    queryFn: listFilterPresets,
    enabled,
  });
  const presets = presetsQ.data?.data ?? [];

  const sortParam = sortToParam(sort);
  const defaultSortParam = sortToParam(DEFAULT_SORT);

  const active =
    activePresetId != null
      ? presets.find((p) => p.id === activePresetId) ?? null
      : null;
  const activeSpec = active ? readPresetSpec(active.filter_spec) : null;
  /* Dirty when either the filters OR the sort drift from the loaded preset. */
  const dirty = activeSpec
    ? !filtersEqualForPreset(filters, activeSpec.filters) ||
      (activeSpec.sort ?? defaultSortParam) !== sortParam
    : false;
  const hasMapArea = filters.bounds != null;

  const invalidate = () => qc.invalidateQueries({ queryKey: filterPresetKeys.all });

  const createMut = useMutation({
    mutationFn: (input: { name: string; filter_spec: PresetSpec; color: TagColor | null }) =>
      createFilterPreset(input),
    onSuccess: (created) => {
      invalidate();
      onActivePresetIdChange(created.id);
    },
  });

  const updateMut = useMutation({
    mutationFn: (input: {
      id: string;
      name?: string;
      filter_spec?: PresetSpec;
      color?: TagColor | null;
    }) =>
      updateFilterPreset(input.id, {
        name: input.name,
        filter_spec: input.filter_spec,
        color: input.color,
      }),
    onSuccess: () => invalidate(),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteFilterPreset(id),
    onSuccess: (_res, id) => {
      invalidate();
      if (id === activePresetId) onActivePresetIdChange(null);
    },
  });

  /* Persist a new order: the chips move on the drop, the server's canonical
   * list is adopted on success, and a failure rolls back from `onSettled`. */
  const reorderMut = useOptimisticWrite({
    mutationKey: ['write', 'filter-presets', 'reorder'],
    mutationFn: (reordered: FilterPreset[]) => reorderFilterPresets(reordered.map((p) => p.id)),
    patch: (reordered) => [
      cachePatch<PresetsResponse>({ queryKey: filterPresetKeys.all, exact: true }, (prev) =>
        prev && { ...prev, data: reordered },
      ),
    ],
    // The PUT answers with the canonical list, so only a failure needs a re-read.
    revalidate: (_reordered, failed) => (failed ? [filterPresetKeys.all] : []),
    onSuccess: (res) => qc.setQueryData(filterPresetKeys.all, res),
  });

  /* Save / Update capture the current filters AND the current sort; Edit only
   * touches metadata (name + colour), leaving the stored filters intact. */
  const specOf = (includeMapArea: boolean): PresetSpec => ({
    filters: filtersForPreset(filters, includeMapArea),
    sort: sortParam,
  });

  /* The Pipeline scope leads the row. It is NOT a preset: it toggles one
   * filter field, it is never dirty, and it composes with whichever preset is
   * loaded (see PRESET_EXCLUDED_KEYS) — so it renders even when the preset
   * service isn't configured and the saved chips are gone. */
  const pipelineChip = (
    <PipelineScopeChip
      filters={filters}
      onFiltersChange={onFiltersChange}
      onLoadView={onLoadPipelineView}
    />
  );

  if (!enabled) {
    return <div className="flex items-center gap-2 flex-wrap">{pipelineChip}</div>;
  }

  return (
    <SavedSetBar
      items={presets}
      loading={presetsQ.isLoading}
      activeId={activePresetId}
      dirty={dirty}
      copy={PRESET_COPY}
      leading={pipelineChip}
      onLoad={onLoad}
      onSaveNew={(name, includeMapArea, color) =>
        createMut.mutateAsync({ name, filter_spec: specOf(includeMapArea), color })
      }
      onUpdate={(preset, name, includeMapArea, color) =>
        updateMut.mutateAsync({ id: preset.id, name, filter_spec: specOf(includeMapArea), color })
      }
      onRename={(preset, name, color) => updateMut.mutateAsync({ id: preset.id, name, color })}
      onDelete={(preset) => deleteMut.mutateAsync(preset.id)}
      onReorder={(next) => reorderMut.mutate(next)}
      extra={
        hasMapArea
          ? {
              label: 'Include the current map area in this preset',
              initial: (preset) =>
                preset != null && readPresetSpec(preset.filter_spec).filters.bounds != null,
            }
          : undefined
      }
    />
  );
}

/* The Pipeline chip — "show me my deals" (rule #22).
 *
 * It is a VIEW, like every other chip in this row: clicking it loads the
 * pipeline over a NEUTRAL cohort (see pipelineViewFilters) instead of ANDing
 * itself onto the filters that happen to be set. It shipped as a modifier and
 * that was wrong: Browse's default cohort is byt + pronájem, so the chip showed
 * 1 of 45 deals — every sale flat, house and commercial unit on the board was
 * hidden by a default the operator never chose for this purpose.
 *
 * It is still NOT a preset:
 *   * nothing is saved, and there is nothing to update;
 *   * `pipeline` is outside preset identity (PRESET_EXCLUDED_KEYS), so it never
 *     marks a loaded preset dirty and never pops "Update preset";
 *   * loading it deselects the active preset — you have left that view.
 *
 * Turning it OFF is just a filter edit (drop the scope, keep the cohort the
 * operator has since built). Back undoes the load. Per-stage narrowing and the
 * compose-with-current-filters behaviour live in the sidebar's Curation →
 * Pipeline control. */
function PipelineScopeChip({
  filters,
  onFiltersChange,
  onLoadView,
}: {
  filters: ListingFilters;
  onFiltersChange: (next: ListingFilters) => void;
  onLoadView: () => void;
}) {
  const scope = filters.pipeline;
  const on = scopeIncludes(scope);
  const stageCount = scope?.in.length ?? 0;

  return (
    <button
      type="button"
      onClick={() =>
        on
          ? // Off clears the in side the chip shows; an excluded stage stays.
            onFiltersChange({ ...filters, pipeline: normalizeScope({ set: null, in: [], out: scope?.out ?? [] }) })
          : onLoadView()
      }
      aria-pressed={on}
      title={
        on
          ? 'Zobrazují se jen nemovitosti v pipeline — kliknutím zrušíte'
          : 'Zobrazit celou pipeline (zruší ostatní filtry; Zpět je vrátí)'
      }
      className={[
        chipBase,
        'gap-1.5 px-2.5 py-1',
        on
          ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] text-[var(--color-copper)]'
          : 'border-[var(--color-rule)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)]',
      ].join(' ')}
    >
      <PipelineMark filled={on} />
      <span>Pipeline</span>
      {stageCount > 0 ? (
        <span className="font-mono tabular-nums text-[0.7rem] opacity-70">
          {stageCount}
        </span>
      ) : null}
    </button>
  );
}
