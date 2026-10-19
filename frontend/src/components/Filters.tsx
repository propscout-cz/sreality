import { Fragment, Suspense, useRef, type ReactNode } from 'react';
import { lazyChunk } from '@/lib/lazyChunk';
import {
  type CenterRadius,
  type ListingFilters,
  type LocationMode,
  DEFAULT_FILTERS,
  isDefault,
  listingFiltersToRegistryView,
  applySidebarUpdates,
} from '@/lib/filters';
import {
  BROWSE_FILTER_BANDS,
  subtypeOptions,
  type CustomBlock,
  type FilterBlock,
} from '@/lib/filterLayout';
import type { MapySuggestion } from '@/lib/maps';
import { CollapsibleGroup, ControlGroup, PickButton, Section } from '@/components/controls';
import { useQuery } from '@tanstack/react-query';
import { supabase } from '@/lib/supabase';
import { pgRead } from '@/lib/pgRead';
import {
  dismissalKeys,
  fetchBrowseCount,
  fetchDismissedCount,
  fetchNoPriceCount,
} from '@/lib/queries';
import ReadFailedMark, { readFailed } from '@/components/ReadFailedMark';
import { collectionLabel, useCollections } from '@/lib/collectionCache';
import { browseKeys } from '@/lib/browseKeys';
import { FilterForm, type CustomFilterWidget } from '@/components/FilterForm';
import { PortalFilterControl } from '@/components/filter-controls/PortalFilterControl';
import { PPM2_UNIT, ppm2BasisOfCohort } from '@/lib/measure';
import CityIndexRulesPicker from '@/components/CityIndexRulesPicker';
import MarketGrowthFilter from '@/components/MarketGrowthFilter';
import {
  LocationTypeahead,
  MultiselectChips,
  PipelineScopePicker,
} from '@/components/filter-controls';
import { AccountTagFilter } from '@/components/filter-controls/TagFilterControl';
import { normalizeScope, type SetScope } from '@/lib/setScope';
/* Lazy: this is the only control here that draws a map, it renders only in
 * `center_radius` mode, and it carries maplibre-gl (~800 kB). Loading it with the
 * sidebar made every Browse first-visit pay for a map engine most sessions never open. */
const LocationControl = lazyChunk(() =>
  import('@/components/filter-controls/LocationControl').then((m) => ({
    default: m.LocationControl,
  })),
);

interface SidebarProps {
  filters: ListingFilters;
  onChange: (next: ListingFilters) => void;
  /* Browse passes this so picking a place in the District typeahead
   * also re-centres the main map. The widget still updates the chip
   * filter via the normal onChange path; this is a side channel for
   * the map navigation. Optional so other consumers (Watchdog edit
   * form, etc.) can mount the sidebar without it. */
  onLocationPick?: (s: MapySuggestion) => void;
  /* Operator-resizable width in px (Browse persists it). Optional so
   * other consumers keep the default 320px sidebar. */
  width?: number;
  /* 'page' (default): sticky, pinned under the top bar, full-viewport height.
   * 'modal': plain full-height scroll column inside a dialog (no sticky/top
   * offset, which would mis-position it within a modal panel). */
  layout?: 'page' | 'modal';
  /* Browse passes this to show a "hide filters" button in the header; the
   * caller swaps in <FilterSidebarRail> while folded. Optional so the other
   * consumers (Watchdog edit form, etc.) get a sidebar that cannot fold. */
  onCollapse?: () => void;
}

const bandActive = (
  f: ListingFilters,
  keys: ReadonlyArray<keyof ListingFilters>,
): boolean =>
  keys.some((k) => {
    const value = f[k];
    const def = DEFAULT_FILTERS[k];
    // Array fields are "active" when they differ from their default array
    // (order-insensitive). Every array default is [] except categoryMain
    // (['byt']) — for [] this reduces to the old "non-empty" check.
    if (Array.isArray(def)) {
      if (!Array.isArray(value)) return false;
      return (
        value.length !== def.length
        || !def.every((d) => (value as unknown[]).includes(d))
      );
    }
    if (def === null) return value !== null && value !== undefined;
    return value !== def;
  });

/* Price-section "include listings without a price" toggle. No-price listings
 * (price_czk IS NULL — RK / dohodou placeholders the scraper normalises to NULL)
 * are silently dropped by SQL the moment a min/max price bound is set; this
 * re-includes them. Disabled (greyed) until a bound is set, because with no
 * bound they're already shown — the toggle would be a no-op. The count of
 * affected listings is fetched lazily and degrades to nothing on error. */
export function IncludeNoPriceToggle({
  filters,
  onChange,
}: {
  filters: ListingFilters;
  onChange: (next: ListingFilters) => void;
}) {
  const hasBound = filters.priceMin != null || filters.priceMax != null;
  const on = filters.includeNoPrice;
  /* The count is independent of the bound itself (it's "no-price rows in the
   * rest of the cohort"), so key on the cohort MINUS price — dragging the
   * price range never refetches it. Only fetched once a bound is set. */
  const countQuery = useQuery({
    queryKey: browseKeys.noPriceCount({
      ...filters,
      priceMin: null,
      priceMax: null,
      includeNoPrice: false,
    }),
    queryFn: ({ signal }) => fetchNoPriceCount(filters, { signal }),
    enabled: hasBound,
    placeholderData: (prev) => prev,
    staleTime: 60_000,
  });
  const count = hasBound ? countQuery.data ?? null : null;
  const fmt = (n: number) => n.toLocaleString('cs-CZ');
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span
          className={`text-sm ${hasBound ? 'text-[var(--color-ink-2)]' : 'text-[var(--color-ink-3)]'}`}
        >
          Include listings without a price
        </span>
        <PickButton
          on={hasBound && on}
          disabled={!hasBound}
          ariaLabel="Include listings without a price"
          onClick={() => onChange({ ...filters, includeNoPrice: !on })}
        >
          {hasBound && on ? 'on' : 'off'}
        </PickButton>
      </div>
      <p className="text-[11px] leading-snug text-[var(--color-ink-3)]">
        {!hasBound
          ? 'Shown by default — set a price range to choose whether to keep them.'
          : count == null
            ? ' '
            : on
              ? `Keeping ${fmt(count)} listing${count === 1 ? '' : 's'} with no listed price.`
              : `${fmt(count)} listing${count === 1 ? '' : 's'} with no listed price ${count === 1 ? 'is' : 'are'} hidden.`}
      </p>
    </div>
  );
}

/* Collection scope (rule #18, lib/setScope): the sidebar picks from the ONE
 * collections list. A failed read is a retry, never "No collections yet" (MS16).
 * Collections shared with the caller sit in their own group over the SAME scope
 * (one `collections` URL parameter); a picked id the list no longer holds (revoked,
 * deleted) stays as a clearable `#id` chip rather than filtering invisibly. */
export function CollectionsPicker({
  value,
  onChange,
}: {
  value: SetScope | null;
  onChange: (next: SetScope | null) => void;
}) {
  const { collections, mine, shared, byId, query: q } = useCollections();

  if (q.isLoading) {
    return (
      <p className="text-[0.75rem] text-[var(--color-ink-4)]">Loading…</p>
    );
  }

  if (readFailed(q)) {
    return <ReadFailedMark what="Kolekce" variant="header" onRetry={() => void q.refetch()} />;
  }

  const picked = value?.in ?? [];
  const missing = picked.filter((id) => !byId.has(id));

  if (collections.length === 0 && missing.length === 0) {
    return (
      <p className="text-[0.75rem] text-[var(--color-ink-4)]">
        No collections yet. Create one from the Collections page.
      </p>
    );
  }

  const set = (next: number[]) =>
    onChange(normalizeScope({ set: null, out: [], ...value, in: next }));
  const chips = (items: typeof collections) =>
    items.map((c) => ({ value: c.id, label: collectionLabel(c) }));
  const grouped = shared.length > 0;
  return (
    <div className="flex flex-col gap-2">
      {mine.length > 0 && (
        <PickerGroup label={grouped ? 'Mine' : null}>
          <MultiselectChips value={picked} options={chips(mine)} onChange={set} />
        </PickerGroup>
      )}
      {grouped && (
        <PickerGroup label="Shared with me">
          <MultiselectChips value={picked} options={chips(shared)} onChange={set} />
        </PickerGroup>
      )}
      {missing.length > 0 && (
        <PickerGroup label="No longer available">
          <MultiselectChips
            value={picked}
            options={missing.map((id) => ({ value: id, label: `#${id}` }))}
            onChange={set}
          />
        </PickerGroup>
      )}
    </div>
  );
}

function PickerGroup({ label, children }: { label: string | null; children: ReactNode }) {
  if (label == null) return <>{children}</>;
  return (
    <div role="group" aria-label={label} className="flex flex-col gap-1">
      {/* The sidebar's sub-group caption (as PipelineScopePicker's "Fáze"). */}
      <p className="text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)]">{label}</p>
      {children}
    </div>
  );
}

/* Curation "show dismissed" toggle (migration 537). Dismissed properties are
 * hidden by default; the hint says how many the CURRENT cohort is hiding — the
 * difference of the two cohort totals, one of which is the header's own cached
 * count. Nothing extra is fetched until the caller has dismissed something, and
 * an approximate total yields no number rather than a wrong one. */
export function ShowDismissedToggle({
  filters,
  onChange,
}: {
  filters: ListingFilters;
  onChange: (next: ListingFilters) => void;
}) {
  const on = filters.showDismissed;
  const anyQ = useQuery({
    queryKey: dismissalKeys.count,
    queryFn: fetchDismissedCount,
    staleTime: 60_000,
  });
  const hasAny = (anyQ.data ?? 0) > 0;
  const totalWith = (showDismissed: boolean) => {
    const f = { ...filters, showDismissed };
    return {
      queryKey: browseKeys.count(f),
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchBrowseCount(f, { signal }),
      enabled: hasAny,
      placeholderData: <T,>(prev: T) => prev,
      staleTime: 60_000,
    };
  };
  const hiddenQ = useQuery(totalWith(false));
  const shownQ = useQuery(totalWith(true));
  const count = hiddenQ.data != null && shownQ.data != null ? shownQ.data - hiddenQ.data : null;
  const fmt = (n: number) => n.toLocaleString('cs-CZ');
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm text-[var(--color-ink-2)]">Show dismissed properties</span>
        <PickButton
          on={on}
          ariaLabel="Show dismissed properties"
          onClick={() => onChange({ ...filters, showDismissed: !on })}
        >
          {on ? 'on' : 'off'}
        </PickButton>
      </div>
      <p className="text-[11px] leading-snug text-[var(--color-ink-3)]">
        {!hasAny
          ? 'Nothing dismissed yet.'
          : count == null
            ? ' '
            : on
              ? `Showing ${fmt(count)} dismissed ${count === 1 ? 'property' : 'properties'}.`
              : `${fmt(count)} dismissed ${count === 1 ? 'property is' : 'properties are'} hidden.`}
      </p>
    </div>
  );
}

/* The unit a Kč/m² bound is authored IN, resolved from the cohort the filters
 * pin (rule 23). The registry declares "CZK/m²" — right on a sale cohort, wrong
 * by ~300x on the DEFAULT Browse cohort (byt + pronájem), where the measure is a
 * monthly rent per m² running ~319: typing 90 000 there returns nothing. A
 * cohort spanning bases has no single unit, so the hint says so rather than
 * picking one. Every surface that authors or summarises the bound reads this. */
export const ppm2BoundUnit = (f: Pick<ListingFilters, 'categoryMain' | 'categoryType'>): string => {
  const basis = ppm2BasisOfCohort({ categoryMain: f.categoryMain, categoryType: f.categoryType });
  return basis == null || basis === 'mixed' ? 'per m² (smíšený základ)' : PPM2_UNIT[basis];
};

/* The registry form's per-row unit overrides: the per-m² pair follows the cohort. */
export const sidebarUnits = (f: ListingFilters): Record<string, string> => {
  const unit = ppm2BoundUnit(f);
  return { min_price_per_m2: unit, max_price_per_m2: unit };
};

/* Rich widgets the registry form cannot express generically, keyed by registry
 * id; each owns its own reads, the form wires value / onChange through. */
export const SIDEBAR_WIDGETS: Record<string, CustomFilterWidget> = {
  collections: CollectionsPicker as never,
  city_index_rules: CityIndexRulesPicker as never,
  pipeline: PipelineScopePicker as never,
};

/* The folded sidebar: a slim full-height strip, one click anywhere on it brings
 * the filters back. The dot says a filter is still narrowing the results while
 * the controls are out of sight. */
export function FilterSidebarRail({
  active,
  onExpand,
  layout = 'page',
}: {
  active: boolean;
  onExpand: () => void;
  layout?: 'page' | 'modal';
}) {
  return (
    <aside
      className={
        layout === 'modal'
          ? 'shrink-0 w-10 border-r border-[var(--color-rule)] h-full'
          : 'shrink-0 w-10 border-r border-[var(--color-rule)] sticky top-14 self-start h-[calc(100dvh-3.5rem)]'
      }
    >
      <button
        type="button"
        onClick={onExpand}
        aria-label="Show filters"
        title="Show filters"
        className="w-full h-full flex flex-col items-center gap-3 py-4 text-[var(--color-ink-3)] hover:text-[var(--color-copper)] hover:bg-[var(--color-paper-2)] transition-colors"
      >
        <ChevronGlyph direction="right" />
        <span className="[writing-mode:vertical-rl] text-[0.7rem] tracking-[0.18em] uppercase">
          Filters
        </span>
        {active && (
          <span
            role="img"
            aria-label="Filters active"
            className="size-1.5 rounded-full bg-[var(--color-copper)]"
          />
        )}
      </button>
    </aside>
  );
}

function ChevronGlyph({ direction }: { direction: 'left' | 'right' }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={direction === 'left' ? 'M10 3 5 8l5 5' : 'M6 3l5 5-5 5'} />
    </svg>
  );
}

export function FilterSidebar({ filters, onChange, onLocationPick, width = 320, layout = 'page', onCollapse }: SidebarProps) {
  // <FilterForm> reads snake_case registry ids; Browse keeps the
  // camelCase `ListingFilters` shape its queries / URL serialisation
  // already use. The adapter in lib/filters bridges both directions
  // (tri-state amenities pivot bool|null ↔ 'any'|'yes'|'no' inside it).
  const registryView = listingFiltersToRegistryView(filters);
  // A location pick lands only after `/maps/resolve` answers, so it merges into
  // the LATEST filters, never the ones the click closed over.
  const latestFilters = useRef(filters);
  latestFilters.current = filters;
  const units = sidebarUnits(filters);
  // Batched apply: every <FilterForm> emission ships an array of
  // updates, so paired range edits (min + max in one slider drag)
  // apply atomically. Without this, sequential id/value callbacks
  // would each start from the same stale `filters` closure and the
  // second update would overwrite the first — visible as a slider
  // that refuses to move and a number input that swallows keystrokes.
  const handleRegistryChange = (
    updates: ReadonlyArray<{ id: string; value: unknown }>,
  ) => onChange(applySidebarUpdates(filters, updates));

  const renderCustom = (b: CustomBlock): ReactNode => {
    switch (b.key) {
      case 'subtype':
        return (
          <MultiselectChips
            value={filters.subtype}
            options={subtypeOptions(filters.categoryMain)}
            onChange={(next) => handleRegistryChange([{ id: 'subtype', value: next }])}
          />
        );
      case 'districts':
        return (
          <LocationTypeahead
            label={b.label}
            value={filters.districts}
            onChange={(next) => {
              latestFilters.current = { ...latestFilters.current, districts: next ?? [] };
              onChange(latestFilters.current);
            }}
            onPick={onLocationPick}
          />
        );
      case 'include_no_price':
        return <IncludeNoPriceToggle filters={filters} onChange={onChange} />;
      case 'tags':
        return (
          <AccountTagFilter value={filters.tags} onChange={(tags) => onChange({ ...filters, tags })} />
        );
      case 'show_dismissed':
        return <ShowDismissedToggle filters={filters} onChange={onChange} />;
      case 'city_population_hint':
        return <CityPopulationHint />;
      case 'market_growth':
        return (
          <MarketGrowthFilter
            value={filters.priceGrowthRules}
            onChange={(next) => onChange({ ...filters, priceGrowthRules: next })}
          />
        );
      case 'portals':
        return (
          <PortalFilterControl value={filters} onChange={(next) => onChange({ ...filters, ...next })} />
        );
      case 'location_mode':
        return (
          <LocationModeSection
            mode={filters.locationMode}
            centerRadius={filters.centerRadius}
            onModeChange={(mode) => onChange({ ...filters, locationMode: mode })}
            onCenterRadiusChange={(cr) => onChange({ ...filters, centerRadius: cr })}
          />
        );
    }
  };

  const renderBlock = (b: FilterBlock, i: number): ReactNode => {
    if (b.kind === 'form') {
      return (
        <FilterForm
          key={i}
          scope="browse"
          state={registryView}
          onChange={handleRegistryChange}
          includeOnly={b.ids}
          labels={b.labels}
          units={units}
          customWidgets={SIDEBAR_WIDGETS}
          flat
        />
      );
    }
    const body = b.caption ? <Section label={b.label}>{renderCustom(b)}</Section> : renderCustom(b);
    // A spanning block takes a row of its own in a grid group, never a cramped cell.
    return b.span ? <div key={i} className="[grid-column:1/-1]">{body}</div> : <Fragment key={i}>{body}</Fragment>;
  };

  return (
    <aside
      style={{ width }}
      className={
        layout === 'modal'
          ? 'shrink-0 border-r border-[var(--color-rule)] h-full overflow-y-auto'
          : 'shrink-0 border-r border-[var(--color-rule)] sticky top-14 self-start max-h-[calc(100dvh-3.5rem)] overflow-y-auto'
      }
    >
      <div className="px-5 py-4 flex items-center justify-between border-b border-[var(--color-rule-soft)]">
        <h2 className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
          Filters
        </h2>
        <div className="flex items-center gap-3">
          {!isDefault(filters) && (
            <button
              type="button"
              onClick={() => onChange(DEFAULT_FILTERS)}
              className="text-[0.7rem] tracking-wide uppercase text-[var(--color-ink-3)] hover:text-[var(--color-copper)] transition-colors"
            >
              Reset
            </button>
          )}
          {onCollapse && (
            <button
              type="button"
              onClick={onCollapse}
              aria-label="Hide filters"
              title="Hide filters"
              className="-mr-1 p-1 rounded-[var(--radius-xs)] text-[var(--color-ink-3)] hover:text-[var(--color-copper)] transition-colors"
            >
              <ChevronGlyph direction="left" />
            </button>
          )}
        </div>
      </div>

      {/* Three tiers from one table (lib/filterLayout, shared with the phone's
          Filtry sheet): collapsible bands → ControlGroups (bordered={false},
          the band owns separation) → rows. The registry decides each form
          row's widget, so a `toolkit/filter_registry.py` edit flows through. */}
      <div className="px-5">
        {BROWSE_FILTER_BANDS.map((band) => (
          <CollapsibleGroup
            key={band.title}
            title={band.title}
            defaultOpen={band.defaultOpen}
            active={bandActive(filters, band.keys)}
          >
            {band.groups
              .filter((g) => !g.when || g.when(filters))
              .map((g) => (
                <ControlGroup key={g.title} title={g.title} bordered={false} layout={g.layout}>
                  {g.blocks.map(renderBlock)}
                </ControlGroup>
              ))}
          </CollapsibleGroup>
        ))}
      </div>
    </aside>
  );
}

/* -------------------------------------------------------------------------- */
/* Phase QUAL — population-data status hint                                   */
/*                                                                            */
/* The `min_city_population` / `max_city_population` filters compare against  */
/* the latest `city_population.population` reading. When `city_population` is */
/* empty (the seed loaded the indexes but the population CSV was missing /    */
/* the Wikidata fetcher hasn't run yet), every city's `c.population` resolves */
/* to NULL, so a `min ≥ 1` predicate excludes everything — counter-intuitive  */
/* enough that the operator filed it as a bug. This banner detects the empty- */
/* population state via a one-row count query and tells the operator how to  */
/* unstick it.                                                                */
/* -------------------------------------------------------------------------- */

export function CityPopulationHint() {
  const { data } = useQuery<{ withPop: number; total: number }, Error>({
    queryKey: ['curated_cities_population_status'],
    /* A hint, never an error: a failed read leaves `data` empty and the
     * banner unrendered (`if (!data)` below). */
    queryFn: async ({ signal }) => {
      const head = () =>
        supabase.from('curated_cities_public').select('*', { count: 'exact', head: true });
      const [total, withPop] = await Promise.all([
        pgRead(head(), { signal }),
        pgRead(head().not('population', 'is', null), { signal }),
      ]);
      return {
        withPop: withPop.count ?? 0,
        total:   total.count   ?? 0,
      };
    },
    staleTime: 60_000,
    gcTime: Infinity,
  });

  if (!data) return null;
  if (data.total === 0) return null;

  /* Hide once at least half the curated cities have populations —
   * the workflow ran, the filter works as expected, no banner needed. */
  if (data.withPop >= data.total / 2) {
    return (
      <p className="mt-2 text-[0.65rem] leading-snug text-[var(--color-ink-3)]">
        Pop. údaje: {data.withPop} / {data.total} měst.
      </p>
    );
  }

  return (
    <div className="mt-2 p-2 rounded-[var(--radius-sm)] bg-[var(--color-copper-soft)] border border-[var(--color-copper)]/30">
      <p className="text-[0.7rem] leading-snug text-[var(--color-ink-2)]">
        <strong className="text-[var(--color-copper)]">Pop. data nejsou nahraná</strong>
        {' '}({data.withPop}/{data.total} měst). Filtr min/max populace bude
        vždy 0 výsledků. Spusť workflow{' '}
        <em className="font-mono not-italic">Refresh city populations from Wikidata</em>
        {' '}a poté{' '}
        <em className="font-mono not-italic">Seed curated cities</em>.
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Location mode: viewport vs centre+radius                                   */
/*                                                                            */
/* The pill toggles which spatial predicate drives the cohort. In `viewport`  */
/* mode (default) the main map's pan/zoom emits the bbox; in `center_radius`  */
/* mode an in-sidebar small map widget lets the operator drop a dot and       */
/* dial a radius — the cohort filters to that circle (approximated as a       */
/* bbox client-side; the main map still draws the precise circle overlay).    */
/* -------------------------------------------------------------------------- */

function LocationModeSection({
  mode,
  centerRadius,
  onModeChange,
  onCenterRadiusChange,
}: {
  mode: LocationMode;
  centerRadius: CenterRadius | null;
  onModeChange: (next: LocationMode) => void;
  onCenterRadiusChange: (next: CenterRadius | null) => void;
}) {
  return (
    <Section label="Mode">
      <div className="inline-flex items-center gap-0.5 p-0.5 rounded-[var(--radius-sm)] bg-[var(--color-paper-2)] border border-[var(--color-rule)]">
        {(['viewport', 'center_radius'] as const).map((m) => {
          const on = mode === m;
          return (
            <button
              key={m}
              type="button"
              onClick={() => onModeChange(m)}
              aria-pressed={on}
              className={[
                'px-2.5 py-1 text-[0.7rem] rounded-[var(--radius-xs)] transition-colors',
                on
                  ? 'bg-[var(--color-copper)] text-white'
                  : 'text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]',
              ].join(' ')}
            >
              {m === 'viewport' ? 'Map viewport' : 'Centre + radius'}
            </button>
          );
        })}
      </div>
      {mode === 'center_radius' ? (
        <div className="mt-3">
          {/* Reserve the control's own height so switching to centre+radius doesn't
            * jump the sidebar while the map chunk arrives. */}
          <Suspense
            fallback={
              <div
                className="h-[13.5rem] rounded-[var(--radius-sm)] bg-[var(--color-inset)]"
                aria-busy="true"
              />
            }
          >
            <LocationControl
              value={centerRadius}
              onChange={onCenterRadiusChange}
              hint={
                'The cohort filters to listings inside the dashed circle. ' +
                'Click the small map to set the centre or drag the marker. ' +
                'The full-page map still shows the circle for context.'
              }
            />
          </Suspense>
        </div>
      ) : (
        <p className="mt-2 text-[0.7rem] text-[var(--color-ink-4)]">
          Filtering by whatever the main map shows.
        </p>
      )}
    </Section>
  );
}

