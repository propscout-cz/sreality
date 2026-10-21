/* Browse's saved filter presets (migration 151) — the preset instance of THE
 * saved-set bar (components/SavedSetBar). A preset is a named filter set + sort
 * order: a chip click restores both (Browse owns the URL write, `onLoad`), and
 * editing either marks it dirty. A preset never fires a notification, unlike a
 * Watchdog. Without the API only the Pipeline chip that leads the row is drawn. */

import { useMemo } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { createFilterPreset, deleteFilterPreset, listFilterPresets, reorderFilterPresets, updateFilterPreset } from '@/lib/api';
import { filterPresetKeys, DEFAULT_SORT, sortToParam, type SortSpec } from '@/lib/queries';
import { filtersEqualForPreset, filtersForPreset, readPresetSpec, type ListingFilters } from '@/lib/filters';
import { normalizeScope, scopeIncludes } from '@/lib/setScope';
import type { FilterPreset } from '@/lib/types';
import SavedSetBar, { chipBase, useSavedSet, type SavedSetCopy } from '@/components/SavedSetBar';
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

export const PRESET_COPY: SavedSetCopy = {
  kind: 'Filter preset',
  empty: 'No saved presets yet.',
  save: '+ Save preset',
  saveHint: 'Save the current filters as a new preset',
  updateHint: 'Save the current filter changes back into this preset',
  dialog: { save: 'Save filters as a preset', update: 'Update preset', edit: 'Edit preset' },
  submit: { save: 'Save preset', update: 'Update preset', edit: 'Save' },
  nameLabel: 'Preset name',
  namePlaceholder: 'e.g. 2+kk Praha pod 6M',
  color: 'Color',
  cancel: 'Cancel',
  saving: 'Saving…',
  edit: 'Edit…',
  delete: { label: 'Delete…', confirm: { label: 'Confirm delete', busy: 'Deleting…' } },
  load: 'Load preset',
  reorder: 'Reorder preset',
  dragHint: 'Drag to reorder',
  options: 'Preset options',
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
  const qc = useQueryClient();
  const { enabled, bar } = useSavedSet(filterPresetKeys.all, listFilterPresets, reorderFilterPresets);
  const sortParam = sortToParam(sort);
  const active = bar.items.find((p) => p.id === activePresetId);
  /* Dirty when either the filters OR the sort drift from the loaded preset. */
  const dirty = useMemo(() => {
    if (active == null) return false;
    const spec = readPresetSpec(active.filter_spec);
    return !filtersEqualForPreset(filters, spec.filters) || (spec.sort ?? sortToParam(DEFAULT_SORT)) !== sortParam;
  }, [active, filters, sortParam]);

  const invalidate = () => qc.invalidateQueries({ queryKey: filterPresetKeys.all });
  // query-core passes a 2nd context argument: the writes take the variables alone.
  const create = useMutation({
    mutationFn: (input: Parameters<typeof createFilterPreset>[0]) => createFilterPreset(input),
    onSuccess: (created) => {
      invalidate();
      onActivePresetIdChange(created.id);
    },
  });
  const update = useMutation({
    mutationFn: ({ id, ...patch }: { id: string } & Parameters<typeof updateFilterPreset>[1]) =>
      updateFilterPreset(id, patch),
    onSuccess: () => invalidate(),
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteFilterPreset(id),
    onSuccess: (_res, id) => {
      invalidate();
      if (id === activePresetId) onActivePresetIdChange(null);
    },
  });

  /* Save / Update capture the current filters AND the current sort; Edit only
   * touches metadata (name + colour), leaving the stored filters intact. */
  const specOf = (includeMapArea: boolean) => ({ filters: filtersForPreset(filters, includeMapArea), sort: sortParam });

  /* The Pipeline chip — "show me my deals" (rule #22) — leads the row, and
   * renders even without the preset service. It is a VIEW, like every chip here:
   * ON loads the pipeline over a NEUTRAL cohort (pipelineViewFilters) instead of
   * ANDing itself onto the filters set — as a modifier it showed 1 of 45 deals,
   * the rest hidden by Browse's byt + pronájem default. It is still NOT a preset:
   * nothing is saved or updated; `pipeline` is outside preset identity
   * (PRESET_EXCLUDED_KEYS), so it never marks a preset dirty; loading it
   * deselects the active preset. OFF is a filter edit (drop the scope, keep the
   * cohort since built); Back undoes the load. Per-stage narrowing lives in the
   * sidebar's Curation → Pipeline control. */
  const scope = filters.pipeline;
  const pipelineOn = scopeIncludes(scope);
  const pipelineChip = (
    <button
      type="button"
      onClick={() =>
        pipelineOn
          ? // Off clears the in side the chip shows; an excluded stage stays.
            onFiltersChange({ ...filters, pipeline: normalizeScope({ set: null, in: [], out: scope?.out ?? [] }) })
          : onLoadPipelineView()
      }
      aria-pressed={pipelineOn}
      title={
        pipelineOn
          ? 'Zobrazují se jen nemovitosti v pipeline — kliknutím zrušíte'
          : 'Zobrazit celou pipeline (zruší ostatní filtry; Zpět je vrátí)'
      }
      className={[
        chipBase,
        'gap-1.5 px-2.5 py-1',
        pipelineOn
          ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] text-[var(--color-copper)]'
          : 'border-[var(--color-rule)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)]',
      ].join(' ')}
    >
      <PipelineMark filled={pipelineOn} />
      <span>Pipeline</span>
      {scope?.in.length ? (
        <span className="font-mono tabular-nums text-[0.7rem] opacity-70">{scope.in.length}</span>
      ) : null}
    </button>
  );
  if (!enabled) return <div className="flex items-center gap-2 flex-wrap">{pipelineChip}</div>;

  return (
    <SavedSetBar
      {...bar}
      activeId={activePresetId}
      dirty={dirty}
      copy={PRESET_COPY}
      leading={pipelineChip}
      onLoad={onLoad}
      onSaveNew={(name, withMapArea, color) => create.mutateAsync({ name, filter_spec: specOf(withMapArea), color })}
      onUpdate={(p, name, withMapArea, color) =>
        update.mutateAsync({ id: p.id, name, filter_spec: specOf(withMapArea), color })
      }
      onRename={(p, name, color) => update.mutateAsync({ id: p.id, name, color })}
      onDelete={(p) => remove.mutateAsync(p.id)}
      checkbox={
        filters.bounds != null
          ? {
              label: 'Include the current map area in this preset',
              initial: (p) => p != null && readPresetSpec(p.filter_spec).filters.bounds != null,
            }
          : undefined
      }
    />
  );
}
