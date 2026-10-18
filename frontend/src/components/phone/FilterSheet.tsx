/* Filtry — Browse's whole filter set on the phone's Nové feed, in one tall
 * sheet (a lazy chunk of its own, loaded on first open).
 *
 * NOTHING HERE DECIDES WHAT A FILTER MEANS (rule 16). The rows are the desktop
 * sidebar's layout (lib/filterLayout, unrolled by ./filterRows) under the
 * sidebar's own words; each row opens onto the sidebar's own control — the
 * registry form row, the place typeahead, the two-sided tag and portal chips —
 * and every edit applies through the sidebar's `applySidebarUpdates`. The sheet
 * holds a DRAFT: "Zobrazit N nemovitostí" (N is Browse's count of the draft)
 * applies it to the feed's URL; "Zavřít" lets it go. The map-only group stays
 * out: the phone has no map. */

import { useEffect, useId, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';

import { FilterForm } from '@/components/FilterForm';
import {
  CityPopulationHint,
  IncludeNoPriceToggle,
  SIDEBAR_WIDGETS,
  ShowDismissedToggle,
  sidebarUnits,
} from '@/components/Filters';
import MarketGrowthFilter from '@/components/MarketGrowthFilter';
import { Segmented } from '@/components/controls';
import { LocationTypeahead, MultiselectChips } from '@/components/filter-controls';
import { PortalFilterControl } from '@/components/filter-controls/PortalFilterControl';
import { AccountTagFilter } from '@/components/filter-controls/TagFilterControl';
import { browseKeys } from '@/lib/browseKeys';
import { isNewestFirst, sortLabel, type FeedView } from '@/lib/feed';
import { subtypeOptions } from '@/lib/filterLayout';
import {
  applySidebarUpdates,
  listingFiltersToRegistryView,
  type ListingFilters,
} from '@/lib/filters';
import { czPlural } from '@/lib/format';
import {
  CARD_SORTS,
  DEFAULT_SORT,
  fetchBrowseCount,
  parseSort,
  sortToParam,
  type SortSpec,
} from '@/lib/queries';
import type { FilterPreset } from '@/lib/types';

import BottomSheet from './BottomSheet';
import DayRule from './DayRule';
import { NO_FOCUS_ZOOM } from './chips';
import { filtersBeyond, sheetBands, type SheetGroup, type SheetRow } from './filterRows';
import { rowWords } from './filterSummary';
import { ChevronDownIcon, ChevronIcon } from './phoneIcons';

export interface FilterSheetProps {
  /* What the feed shows now: the draft starts here. */
  filters: ListingFilters;
  sort: SortSpec;
  /* The chosen scope's own filters ("Vše" or the preset), what Vymazat returns to. */
  base: ListingFilters;
  preset: FilterPreset | null;
  onApply: (view: FeedView) => void;
  onClose: () => void;
}

/* The count follows the draft once typing pauses, not on every keystroke. */
const COUNT_DEBOUNCE_MS = 350;

const SORT_OPTIONS = CARD_SORTS.map((o) => ({ value: sortToParam(o.spec), label: o.label }));

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return settled;
}

export default function FilterSheet({ filters, sort, base, preset, onApply, onClose }: FilterSheetProps) {
  const [draft, setDraft] = useState(filters);
  const [draftSort, setDraftSort] = useState(sort);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const [placeOpen, setPlaceOpen] = useState(false);

  const settled = useDebounced(draft, COUNT_DEBOUNCE_MS);
  const countQ = useQuery({
    queryKey: browseKeys.count(settled),
    queryFn: ({ signal }) => fetchBrowseCount(settled, { signal }),
    staleTime: 60_000,
  });
  const counting = settled !== draft || countQ.isPending;

  const view = listingFiltersToRegistryView(draft);
  const units = sidebarUnits(draft);
  const update = (updates: ReadonlyArray<{ id: string; value: unknown }>) =>
    setDraft((d) => applySidebarUpdates(d, updates));
  const merge = (patch: Partial<ListingFilters>) => setDraft((d) => ({ ...d, ...patch }));
  const toggle = (key: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const clearable = filtersBeyond(draft, base) > 0 || !isNewestFirst(draftSort);
  const apply = () => {
    onApply({ filters: draft, sort: draftSort, presetId: preset?.id ?? null });
    onClose();
  };

  const body = (row: SheetRow): ReactNode => {
    if (row.kind === 'form') {
      return (
        <FilterForm
          scope="browse"
          state={view}
          onChange={update}
          includeOnly={[row.key]}
          labels={{ [row.key]: row.label }}
          units={units}
          customWidgets={SIDEBAR_WIDGETS}
          flat
          bare={!row.inline}
        />
      );
    }
    switch (row.block.key) {
      case 'subtype':
        return (
          <MultiselectChips
            value={draft.subtype}
            options={subtypeOptions(draft.categoryMain)}
            onChange={(next) => update([{ id: 'subtype', value: next }])}
          />
        );
      case 'include_no_price':
        return (
          <IncludeNoPriceToggle
            filters={settled}
            onChange={(next) => merge({ includeNoPrice: next.includeNoPrice })}
          />
        );
      case 'tags':
        return <AccountTagFilter value={draft} onChange={merge} />;
      case 'show_dismissed':
        /* Its hint counts the cohort twice; it reads the settled draft, so a
         * typed bound does not fire two counts per keystroke. */
        return (
          <ShowDismissedToggle
            filters={settled}
            onChange={(next) => merge({ showDismissed: next.showDismissed })}
          />
        );
      case 'market_growth':
        return <MarketGrowthFilter value={draft.priceGrowthRules} onChange={(next) => merge({ priceGrowthRules: next })} />;
      case 'portals':
        return <PortalFilterControl value={draft} onChange={merge} />;
      default:
        return null;
    }
  };

  const n = countQ.data;
  const showLabel = counting
    ? 'Zobrazit … nemovitostí'
    : n == null
      ? 'Zobrazit nemovitosti'
      : `Zobrazit ${n.toLocaleString('cs-CZ')} ${czPlural(n, 'nemovitost', 'nemovitosti', 'nemovitostí')}`;

  const footer = (
    <div className="flex items-center gap-2 px-4">
      <button
        type="button"
        onClick={() => {
          setDraft(base);
          setDraftSort(DEFAULT_SORT);
        }}
        disabled={!clearable}
        className="h-11 shrink-0 px-1 text-[15px] text-[var(--color-ink-2)] disabled:text-[var(--color-ink-4)]"
      >
        Vymazat
      </button>
      <button
        type="button"
        onClick={() => apply()}
        aria-busy={counting || undefined}
        className="h-11 min-w-0 flex-1 truncate rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 text-[15px] font-medium text-white active:bg-[var(--color-copper-2)]"
      >
        {showLabel}
      </button>
    </div>
  );

  return (
    <BottomSheet label="Filtry" closeLabel="Zavřít" onClose={onClose} footer={footer}>
      <div className={`pb-6 ${NO_FOCUS_ZOOM}`}>
        <Row
          rowKey="sort"
          label="Řazení"
          words={sortLabel(draftSort)}
          open={open.has('sort')}
          onToggle={() => toggle('sort')}
        >
          <Segmented
            label="Řazení"
            variant="solid"
            options={SORT_OPTIONS}
            value={sortToParam(draftSort)}
            onChange={(v) => setDraftSort(parseSort(v))}
          />
        </Row>
        {sheetBands(draft).map((band) => (
          <section key={band.title} aria-label={band.title}>
            <DayRule label={band.title} />
            {band.groups.map((g) => (
              <div key={g.title} role="group" aria-label={g.title}>
                {captioned(g) && (
                  <p className="px-4 pb-1 pt-2 text-[12px] text-[var(--color-ink-3)]">{g.title}</p>
                )}
                {g.rows.map((row) =>
                  row.kind === 'custom' && row.block.key === 'districts' ? (
                    <Row
                      key={row.key}
                      rowKey={row.key}
                      ids={row.ids}
                      label={row.label}
                      words={rowWords(row, draft, view, units)}
                      onOpenSheet={() => setPlaceOpen(true)}
                    />
                  ) : (
                    <Row
                      key={row.key}
                      rowKey={row.key}
                      ids={row.ids}
                      label={row.label}
                      words={rowWords(row, draft, view, units)}
                      inline={row.inline}
                      open={open.has(row.key)}
                      onToggle={() => toggle(row.key)}
                    >
                      {body(row)}
                    </Row>
                  ),
                )}
                {g.notes.map((note) => (
                  <div key={note.key} className="px-4 pb-2">
                    <CityPopulationHint />
                  </div>
                ))}
              </div>
            ))}
          </section>
        ))}
      </div>

      {/* The place typeahead in a tall sheet of its own, field at the top, so
        * its suggestions open above the keyboard (the brokers' place sheet). */}
      {placeOpen && (
        <BottomSheet label="District" onClose={() => setPlaceOpen(false)}>
          <div className={`min-h-[70dvh] px-4 pt-3 ${NO_FOCUS_ZOOM}`}>
            <LocationTypeahead
              label="District"
              value={draft.districts}
              onChange={(next) => merge({ districts: next ?? [] })}
            />
          </div>
        </BottomSheet>
      )}
    </BottomSheet>
  );
}

/* A group's own caption, unless its one row already says the same words. */
const captioned = (g: SheetGroup): boolean => !(g.rows.length === 1 && g.rows[0].label === g.title);

function Row({
  rowKey,
  ids,
  label,
  words,
  inline = false,
  open = false,
  onToggle,
  onOpenSheet,
  children,
}: {
  rowKey: string;
  ids?: readonly string[];
  label: string;
  /* The value in words; null = unset, read "Vše". */
  words: string | null;
  inline?: boolean;
  open?: boolean;
  onToggle?: () => void;
  onOpenSheet?: () => void;
  children?: ReactNode;
}) {
  const panelId = useId();
  const marks = { 'data-filter-row': rowKey, 'data-filter-ids': ids?.join(' ') };
  if (inline) {
    return (
      <div {...marks} className="border-t border-[var(--color-rule-soft)] px-4 py-2.5">
        {children}
      </div>
    );
  }
  return (
    <div {...marks} className="border-t border-[var(--color-rule-soft)]">
      <button
        type="button"
        onClick={onOpenSheet ?? onToggle}
        aria-expanded={onOpenSheet ? undefined : open}
        aria-controls={onOpenSheet || !open ? undefined : panelId}
        aria-haspopup={onOpenSheet ? 'dialog' : undefined}
        className="flex min-h-12 w-full items-center gap-3 px-4 py-2 text-left active:bg-[var(--color-paper-2)]"
      >
        <span className="min-w-0 flex-1 text-[15px] leading-snug text-[var(--color-ink)]">{label}</span>
        <span
          className={`max-w-[55%] shrink-0 truncate text-right text-[13px] ${words ? 'text-[var(--color-ink-2)]' : 'text-[var(--color-ink-4)]'}`}
        >
          {words ?? 'Vše'}
        </span>
        {onOpenSheet ? (
          <ChevronIcon className="h-4 w-4 shrink-0 text-[var(--color-ink-4)]" />
        ) : (
          <ChevronDownIcon
            className={`h-4 w-4 shrink-0 text-[var(--color-ink-4)] transition-transform ${open ? 'rotate-180' : ''}`}
          />
        )}
      </button>
      {open && !onOpenSheet && (
        <div id={panelId} className="px-4 pb-4 pt-1">
          {children}
        </div>
      )}
    </div>
  );
}
