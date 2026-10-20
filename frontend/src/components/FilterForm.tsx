/* FilterForm — registry-driven filter renderer.
 *
 * Single component that any consumer (Watchdog, Browse, Settings preview)
 * can drop in to render the operator's filter controls. It reads
 * `FILTER_REGISTRY` from the generated TS file and dispatches on each
 * filter's `ui_control` to one of the shared widget primitives.
 *
 *   <FilterForm
 *     scope="watchdog"
 *     state={spec}
 *     onChange={(id, val) => setSpec({ ...spec, [id]: val })}
 *     visibility={schema?.filters}            // optional matrix override
 *     exclude={['location', 'tags']}          // optional: skip filters
 *   />
 *
 * The dispatcher deliberately does NOT own state. Consumers keep their
 * own (TS-typed) filter state and pass current values + an onChange.
 * State key is the registry id; value type matches the filter's
 * declared `type`.
 *
 * Composite filters (currently just `location`) and any unknown
 * ui_control are skipped. Browse and Watchdog continue to render
 * their existing spatial widgets directly — the LOCATION composite
 * lands in a follow-up commit once the map mode toggle is built.
 */

import {
  FILTER_REGISTRY,
  type Agenda,
  type FilterDef,
} from '@/lib/filterRegistry.generated';
import {
  ControlGroup,
  DecimalField,
  PickButton,
  Section,
  TriRow,
  type TriValue,
} from '@/components/controls';
import {
  MultiselectChips,
  RangeInputs,
  RangeSlider,
  SingleSelectDropdown,
  type EnumOptionLite,
} from '@/components/filter-controls';

export type FilterState = Record<string, unknown>;

/** One filter update emitted by the form. Multi-element arrays
 *  represent atomic batches — paired min/max range edits both fire
 *  in a single onChange call so the parent can apply them together
 *  via `applyRegistryUpdates`. Without this batching a non-functional
 *  setter (e.g. Browse's URL-writing setFilters) would see the second
 *  call with the same stale state as the first, clobbering the first
 *  change — which is exactly what broke slider drag + paired number
 *  inputs on Browse. */
export interface FilterUpdate {
  id: string;
  value: unknown;
}

export interface CustomFilterWidgetProps {
  value: unknown;
  onChange: (next: unknown) => void;
  /** The row's visible caption — the same string the wrapping <Section>
   *  renders. A group name is ancestor context, so any widget with an inner
   *  input must be handed the words to name it with. */
  label: string;
}

export type CustomFilterWidget = (
  props: CustomFilterWidgetProps,
) => React.ReactElement | null;

interface FilterFormProps {
  scope: Agenda;
  state: FilterState;
  /** Apply one or more filter updates atomically. Single-value
   *  changes ship as `[{id, value}]`; paired range edits ship the
   *  min and max sides together. The parent must always treat the
   *  array as one transaction against the current filter state. */
  onChange: (updates: ReadonlyArray<FilterUpdate>) => void;
  /** Optional visibility override. When supplied, only filters with
   *  `visibility[scope] === true` render. When omitted, every filter
   *  the registry declares for `scope` renders (the all-on default). */
  visibility?: ReadonlyArray<{ id: string; visibility: Record<string, boolean> }>;
  /** Ids to skip even if the registry declares them. Use sparingly —
   *  for now, only when the host page renders its own widget for that
   *  filter (e.g. Watchdog's spatial-center inputs). */
  exclude?: ReadonlyArray<string>;
  /** When set, render *only* the listed filter ids (after agenda /
   *  visibility filtering). Lets a host page slice the registry into
   *  its existing section layout. Min/max pairing still applies — pass
   *  either side of a pair and the matching sibling is auto-included. */
  includeOnly?: ReadonlyArray<string>;
  /** Override widget labels. Keyed by filter id; falls back to a
   *  prettified id. */
  labels?: Record<string, string>;
  /** Override the registry's static `unit` string, keyed by filter id.
   *  The registry declares ONE unit per filter, which is right for a
   *  filter whose unit is a constant (m², %, days) and wrong for one
   *  whose unit depends on the cohort: `min/max_price_per_m2` matches
   *  THE measure (migration 425), whose basis is capital Kč/m² on a sale
   *  cohort and monthly Kč/m²/měs on a rent one — a ~300x difference in
   *  what a typed bound means. A host that knows its cohort passes the
   *  resolved unit here rather than letting the static one through. */
  units?: Record<string, string>;
  /** When true, render filters as a flat row list without the
   *  per-category `<ControlGroup>` wrappers. Useful when the host page
   *  already provides its own group container. */
  flat?: boolean;
  /** Per-filter widget overrides. Keyed by registry filter id.
   *  When provided, the dispatcher renders the custom widget inside
   *  the standard `<Section label={...}>` wrapper instead of the
   *  built-in primitive. Use for rich widgets the controls library
   *  can't generically express (district typeahead, tag picker, …). */
  customWidgets?: Record<string, CustomFilterWidget>;
  /** Render each row without its visible caption: the host names the row
   *  itself (the phone's Filtry sheet heads every row with its label). The
   *  caption stays the row's accessible name. */
  bare?: boolean;
}

/** One row of the form: a filter, or a min/max pair folded into one (its max
 *  side in `maxDef`). */
export interface FormRow {
  def: FilterDef;
  maxDef: FilterDef | null;
}

/** The rows a <FilterForm> draws for this slice of the registry, in the order
 *  it draws them (registry declaration order, pairs folded). Exported so a host
 *  that lays the rows out one by one — the phone's Filtry sheet — orders and
 *  pairs them exactly as the form does. */
export function formRows(
  scope: Agenda,
  {
    visibility,
    exclude,
    includeOnly,
  }: Pick<FilterFormProps, 'visibility' | 'exclude' | 'includeOnly'> = {},
): FormRow[] {
  const excludeSet = new Set(exclude ?? []);
  const visibilityById = new Map(
    (visibility ?? []).map((v) => [v.id, v.visibility]),
  );

  let visibleFilters = FILTER_REGISTRY.filters.filter((f) => {
    if (!f.agendas.includes(scope)) return false;
    if (excludeSet.has(f.id)) return false;
    if (visibilityById.size > 0) {
      const cell = visibilityById.get(f.id);
      if (cell && cell[scope] === false) return false;
    }
    return true;
  });

  if (includeOnly) {
    // Auto-include the matching sibling of any min/max-style id in the
    // includeOnly set so paired rows render correctly even when the
    // caller only listed one half.
    const expanded = new Set(includeOnly);
    for (const id of includeOnly) {
      const allIds = new Set(visibleFilters.map((f) => f.id));
      const sibling = findMaxSibling(id, allIds) ?? findMinSibling(id, allIds);
      if (sibling) expanded.add(sibling);
    }
    visibleFilters = visibleFilters.filter((f) => expanded.has(f.id));
  }

  // Pair min/max sibling filters so the form renders one paired
  // RangeInputs / RangeSlider row per pair rather than two separate
  // single-number rows. See `findMaxSibling` for the matching rules.
  const visibleIds = new Set(visibleFilters.map((f) => f.id));
  const pairedAsMin = new Map<string, FilterDef>();   // min id → max def
  const skipAsMax = new Set<string>();
  for (const f of visibleFilters) {
    const sibling = findMaxSibling(f.id, visibleIds);
    if (sibling) {
      const maxDef = FILTER_REGISTRY.filters.find((g) => g.id === sibling);
      if (maxDef) {
        pairedAsMin.set(f.id, maxDef);
        skipAsMax.add(sibling);
      }
    }
  }
  return visibleFilters
    .filter((f) => !skipAsMax.has(f.id))
    .map((def) => ({ def, maxDef: pairedAsMin.get(def.id) ?? null }));
}

export function FilterForm({
  scope,
  state,
  onChange,
  visibility,
  exclude,
  includeOnly,
  labels,
  units,
  flat,
  customWidgets,
  bare,
}: FilterFormProps) {
  const rows = formRows(scope, { visibility, exclude, includeOnly });

  const renderRow = ({ def: f, maxDef }: FormRow) => {
    const custom = customWidgets?.[f.id];
    if (custom) {
      // Render the operator-supplied widget inside the standard
      // Section wrapper so it carries the same label spacing as the
      // built-in rows.
      const label = labels?.[f.id] ?? prettifyId(f.id);
      return (
        <Caption key={f.id} label={label} bare={bare}>
          {custom({
            value: state[f.id],
            onChange: (v) => onChange([{ id: f.id, value: v }]),
            label,
          })}
        </Caption>
      );
    }
    return (
      <FilterRow
        key={f.id}
        def={f}
        maxDef={maxDef}
        value={state[f.id]}
        maxValue={maxDef ? state[maxDef.id] : undefined}
        emit={onChange}
        label={labels?.[f.id] ?? prettifyPair(f.id, maxDef?.id)}
        unit={units?.[f.id] ?? f.unit ?? null}
        bare={bare}
      />
    );
  };

  if (flat) {
    // Preserve registry declaration order so paired rows land next to
    // their host group; the caller's wrapping `<ControlGroup>` provides
    // the visual heading.
    return <>{rows.map(renderRow)}</>;
  }

  const byCategory = new Map<string, FormRow[]>();
  for (const r of rows) {
    const list = byCategory.get(r.def.category) ?? [];
    list.push(r);
    byCategory.set(r.def.category, list);
  }

  return (
    <div className="space-y-6">
      {FILTER_REGISTRY.categories
        .filter((c) => byCategory.has(c))
        .map((category) => (
          <ControlGroup key={category} title={category}>
            {byCategory.get(category)!.map(renderRow)}
          </ControlGroup>
        ))}
    </div>
  );
}

/** Inverse of `findMaxSibling`: given a max-side id, return the min
 *  counterpart if present. Used so `includeOnly: ['max_price_czk']`
 *  still renders the paired row. */
function findMinSibling(id: string, present: Set<string>): string | null {
  if (id.startsWith('max_')) {
    const candidate = 'min_' + id.slice(4);
    return present.has(candidate) ? candidate : null;
  }
  if (id.endsWith('_max')) {
    const candidate = id.slice(0, -4) + '_min';
    return present.has(candidate) ? candidate : null;
  }
  const middle = id.match(/^(.+)_max_(.+)$/);
  if (middle) {
    const candidate = `${middle[1]}_min_${middle[2]}`;
    return present.has(candidate) ? candidate : null;
  }
  return null;
}

/** Given a filter id, return the id of its companion max-side, if any.
 *  Recognises three pairing patterns:
 *    - `min_X`     ↔ `max_X`     (min_price_czk ↔ max_price_czk)
 *    - `X_min`     ↔ `X_max`     (tom_days_min  ↔ tom_days_max)
 *    - `X_min_Y`   ↔ `X_max_Y`   (last_seen_min_days ↔ last_seen_max_days)
 *  Returns null when no companion is present in the visible set. */
function findMaxSibling(id: string, present: Set<string>): string | null {
  if (id.startsWith('min_')) {
    const candidate = 'max_' + id.slice(4);
    return present.has(candidate) ? candidate : null;
  }
  if (id.endsWith('_min')) {
    const candidate = id.slice(0, -4) + '_max';
    return present.has(candidate) ? candidate : null;
  }
  const middle = id.match(/^(.+)_min_(.+)$/);
  if (middle) {
    const candidate = `${middle[1]}_max_${middle[2]}`;
    return present.has(candidate) ? candidate : null;
  }
  return null;
}

/** Strip the min/max prefix/suffix and prettify what's left so a pair
 *  renders as one shared label. */
function prettifyPair(minId: string, _maxId: string | undefined): string {
  let core = minId;
  if (core.startsWith('min_')) core = core.slice(4);
  else if (core.endsWith('_min')) core = core.slice(0, -4);
  else {
    const middle = core.match(/^(.+)_min_(.+)$/);
    if (middle) core = `${middle[1]}_${middle[2]}`;
  }
  return prettifyId(core);
}

/* A row's caption over its control, or — `bare` — only the group it names. */
function Caption({
  label,
  bare,
  children,
}: {
  label: string;
  bare?: boolean;
  children: React.ReactNode;
}) {
  if (!bare) return <Section label={label}>{children}</Section>;
  return (
    <div role="group" aria-label={label}>
      {children}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Per-row dispatch                                                           */
/* -------------------------------------------------------------------------- */

function FilterRow({
  def,
  maxDef,
  value,
  maxValue,
  emit,
  label,
  unit,
  bare,
}: {
  def: FilterDef;
  maxDef: FilterDef | null;
  value: unknown;
  maxValue: unknown;
  emit: (updates: ReadonlyArray<FilterUpdate>) => void;
  label: string;
  /* The unit actually rendered — the host's cohort-resolved override when it
   * supplied one, else the registry's static string. Never read `def.unit`
   * directly below: that is how a rent cohort ended up labelled CZK/m². */
  unit: string | null;
  bare?: boolean;
}) {
  // Single-filter helper: most rows fire one update at a time. Paired
  // ranges below build a 2-element update array directly so both
  // sides apply atomically against the parent's filter state.
  const onChange = (v: unknown) => emit([{ id: def.id, value: v }]);
  // A number box emits only a changed value (DecimalField emits every parse).
  const num = (value as number | null) ?? null;
  const onNumber = (v: number | null) => v !== num && onChange(v);

  // When paired with a max-side, render the pair as either:
  //   - a dual-thumb RangeSlider when the registry declares a complete
  //     min + max + step bounds set AND its ui_control is `range_slider`
  //     (the default for bounded filters like garden area / yield), or
  //   - paired RangeInputs (open-ended number fields) when the filter
  //     opts into `range_inputs` OR its bounds aren't complete.
  //
  // `ui_control` is authoritative here: a filter can carry bounds (still
  // useful as validation/agent metadata) yet ask for plain inputs by
  // declaring `range_inputs` — that's how price / price-per-m² / area /
  // estate / usable render as inputs without dropping their bounds.
  if (maxDef) {
    const c = def.constraints ?? {};
    const hasFullBounds =
      typeof c.min === 'number' &&
      typeof c.max === 'number' &&
      typeof c.step === 'number';
    if (hasFullBounds && def.ui_control !== 'range_inputs') {
      return (
        <Caption label={label} bare={bare}>
          <RangeSlider
            bounds={{
              min: c.min as number,
              max: c.max as number,
              step: c.step as number,
            }}
            value={[
              (value as number | null) ?? null,
              (maxValue as number | null) ?? null,
            ]}
            onChange={([lo, hi]) =>
              emit([
                { id: def.id, value: lo },
                { id: maxDef.id, value: hi },
              ])
            }
            unit={unit ?? undefined}
            ariaLabel={label}
          />
        </Caption>
      );
    }
    return (
      <Caption label={label} bare={bare}>
        <RangeInputs
          minValue={(value as number | null) ?? null}
          maxValue={(maxValue as number | null) ?? null}
          coerce={def.type === 'int' ? 'int' : 'float'}
          onChange={(lo, hi) =>
            emit([
              { id: def.id, value: lo },
              { id: maxDef.id, value: hi },
            ])
          }
          ariaLabelMin={`${label} min`}
          ariaLabelMax={`${label} max`}
        />
        {unit ? <UnitHint unit={unit} /> : null}
      </Caption>
    );
  }

  switch (def.ui_control) {
    case 'pill_group':
      return (
        <Caption label={label} bare={bare}>
          <PillRow def={def} value={value as string | null} onChange={onChange} />
        </Caption>
      );

    case 'single_select':
      return (
        <Caption label={label} bare={bare}>
          <SingleSelectRow
            def={def}
            label={label}
            value={value as string | number | null}
            onChange={onChange}
          />
        </Caption>
      );

    case 'multiselect':
      return (
        <Caption label={label} bare={bare}>
          <MultiselectRow
            def={def}
            value={(value as Array<string | number> | null) ?? []}
            onChange={onChange}
          />
        </Caption>
      );

    case 'tristate': {
      const tri: TriValue =
        value == null ? 'any' : value ? 'yes' : 'no';
      return (
        <TriRow
          label={label}
          value={tri}
          onChange={(next) => {
            if (next === 'any') onChange(null);
            else onChange(next === 'yes');
          }}
        />
      );
    }

    case 'range_inputs':
    case 'range_slider':
      // Unpaired range filter (no sibling found). Render as a single
      // number input — happens for `min_parking_lots` and any future
      // min-only field. Paired ranges are caught above.
      return (
        <Caption label={label} bare={bare}>
          <DecimalField
            cell
            label={label}
            value={num}
            integer={def.type === 'int'}
            placeholder={unit ? `value ${unit}` : 'value'}
            onChange={onNumber}
          />
        </Caption>
      );

    case 'number_input':
      return (
        <Caption label={label} bare={bare}>
          <DecimalField
            cell
            label={label}
            value={num}
            integer={def.type === 'int'}
            placeholder="—"
            onChange={onNumber}
          />
          {unit ? <UnitHint unit={unit} /> : null}
        </Caption>
      );

    case 'boolean':
      return (
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm text-[var(--color-ink-2)]">{label}</span>
          <PickButton
            on={value === true}
            onClick={() => onChange(value === true ? false : true)}
          >
            {value === true ? 'on' : 'off'}
          </PickButton>
        </div>
      );

    case 'location':
      // Composite filter — host page renders its own location widget
      // until the integrated map dot/radius control is built.
      return null;

    default:
      return null;
  }
}

/* -------------------------------------------------------------------------- */
/* Widget adapters                                                            */
/* -------------------------------------------------------------------------- */

function optionsFor(def: FilterDef): EnumOptionLite<string | number>[] {
  if (def.enum_values) {
    return def.enum_values.map((o) => ({
      value: o.value as string | number,
      label: o.label_cs,
    }));
  }
  return [];
}

function PillRow({
  def,
  value,
  onChange,
}: {
  def: FilterDef;
  value: string | null;
  onChange: (v: string | null) => void;
}) {
  const opts = optionsFor(def);
  if (opts.length === 0) return null;
  /* No "Vše" pill (operator, 2026-10-20): a nullable group's "no constraint"
   * state (FilterDef.nullable — `category_type` via the Pipeline chip, the
   * phone feed's Vše scope) lights no pill, and clicking the selected pill
   * returns to it. */
  // Auto-fit grid instead of a fixed column count: each pill claims a
  // ~4rem minimum and the browser packs as many per row as the group's
  // current width allows, then stretches them to fill. So as the
  // (resizable) sidebar widens, a trailing pill like "Ostatní" rejoins
  // the row above instead of being stranded on its own line under a
  // hardcoded 4-column cap. At the default narrow width this still lands
  // ~4 across — unchanged from before.
  return (
    <div className="grid gap-1 [grid-template-columns:repeat(auto-fit,minmax(4rem,1fr))]">
      {opts.map((opt) => (
        <PickButton
          key={String(opt.value)}
          on={value === opt.value}
          onClick={() => onChange(value === opt.value ? null : String(opt.value))}
          variant="solid"
        >
          {opt.label}
        </PickButton>
      ))}
    </div>
  );
}

function SingleSelectRow({
  def,
  label,
  value,
  onChange,
}: {
  def: FilterDef;
  label: string;
  value: string | number | null;
  onChange: (v: string | number | null) => void;
}) {
  const opts = optionsFor(def);
  return (
    <SingleSelectDropdown
      label={label}
      value={value}
      options={opts}
      onChange={onChange}
    />
  );
}

function MultiselectRow({
  def,
  value,
  onChange,
}: {
  def: FilterDef;
  value: ReadonlyArray<string | number>;
  onChange: (v: Array<string | number> | null) => void;
}) {
  return (
    <MultiselectChips
      value={value}
      options={optionsFor(def)}
      onChange={(next) => onChange(next.length === 0 ? null : next)}
    />
  );
}

function UnitHint({ unit }: { unit: string }) {
  return (
    <span className="ml-1 text-[0.7rem] text-[var(--color-ink-4)]">
      {unit}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function prettifyId(id: string): string {
  // "min_price_czk" → "Min price czk"; "has_balcony" → "Has balcony".
  return id
    .split('_')
    .map((w, i) => (i === 0 ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}
