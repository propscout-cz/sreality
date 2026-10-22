/* The ONE place predicate, browser side.
 *
 * A location chip is a LEVEL plus a RÚIAN CODE, and every surface compiles it
 * the same way:
 *
 *     <level>_id = any(<codes at that level>)
 *
 * plain equality per level, nothing else. This module owns that compilation for
 * the SPA; `api/location_filter.py` owns it for the API and the Watchdog; the
 * two RPC bodies (latest: migration 605) own it in SQL. All three produce the
 * same PLAN for the same chips — `districtCodePlan` here and
 * `district_code_plan` there return the same object, and
 * `tests/fixtures/district_chip_plan.json` is the table both are tested against.
 * `browse_list.obec_id` / `okres_id` / `region_id` ARE RÚIAN codes
 * (`admin_boundaries.id` IS the code); `cast_obce_id` (migration 503) and
 * `ulice_id` (migration 584) are the two finer levels. */

import type { DistrictChip } from './filters';

/* THE level list, coarsest first, and DETERMINISTIC: the compiled predicate has
 * to be byte-stable so the SPA, the API and the RPC bodies can be diffed. */
export const DISTRICT_LEVEL_ORDER = ['kraj', 'okres', 'obec', 'cast_obce', 'ulice'] as const;
export type LocationLevel = (typeof DISTRICT_LEVEL_ORDER)[number];

/* level → the code column, on every place-filterable relation: `browse_list`
 * (the list, the map pins and the tile counts), `properties_public` (the
 * Watchdog) and `pipeline_board_public` (the board). */
const DISTRICT_LEVEL_COLUMN = {
  kraj: 'region_id',
  okres: 'okres_id',
  obec: 'obec_id',
  cast_obce: 'cast_obce_id',
  ulice: 'ulice_id',
} as const satisfies Record<LocationLevel, string>;

export const isLocationLevel = (level: unknown): level is LocationLevel =>
  (DISTRICT_LEVEL_ORDER as ReadonlyArray<unknown>).includes(level);

/* THE chip guard, for every chip that arrives from outside — a URL, a stored
 * preset, a resolve answer: one of the five levels and a positive integer code.
 * Anything else names no unit to filter on and is never a chip. */
export const isDistrictChip = (x: unknown): x is DistrictChip => {
  if (x == null || typeof x !== 'object') return false;
  const c = x as Record<string, unknown>;
  return typeof c.name === 'string'
    && (c.context == null || typeof c.context === 'string')
    && (c.excluded === undefined || typeof c.excluded === 'boolean')
    && isLocationLevel(c.level)
    && Number.isInteger(c.id) && (c.id as number) > 0;
};

type CodesByLevel = Partial<Record<LocationLevel, number[]>>;

export const districtCodePlan = (
  chips: ReadonlyArray<DistrictChip>,
): { include: CodesByLevel; exclude: CodesByLevel } => {
  const include: CodesByLevel = {};
  const exclude: CodesByLevel = {};
  for (const chip of chips) {
    const codes = ((chip.excluded === true ? exclude : include)[chip.level] ??= []);
    if (!codes.includes(chip.id)) codes.push(chip.id);
  }
  return { include, exclude };
};

const levelsWithCodes = (side: CodesByLevel): LocationLevel[] =>
  DISTRICT_LEVEL_ORDER.filter((l) => (side[l]?.length ?? 0) > 0);

const includeArms = (side: CodesByLevel): string[] =>
  levelsWithCodes(side).map(
    (level) => `${DISTRICT_LEVEL_COLUMN[level]}.in.(${side[level]!.join(',')})`,
  );

/* An EXCLUDE subtracts only what it MATCHES, so a row whose code at that level
 * is NULL — an unresolved listing — must SURVIVE it. PostgREST's `not.in.(…)`
 * is SQL `NOT (col IN (…))`, which is NULL (i.e. drops the row) for a NULL col,
 * so each level is spelled `or(col.is.null,col.not.in.(…))` and the levels AND
 * together. That is De Morgan's law over the include arms with NULL read as
 * "did not match" — exactly what the RPC's `not exists(... case ...)` and the
 * in-memory `!hits` already do. Without it "not Prague" silently also meant
 * "and located", and the four renderings of one predicate disagreed. */
const excludeArms = (side: CodesByLevel): string[] =>
  levelsWithCodes(side).map((level) => {
    const col = DISTRICT_LEVEL_COLUMN[level];
    return `or(${col}.is.null,${col}.not.in.(${side[level]!.join(',')}))`;
  });

/* PostgREST `or=(...)` predicate for the location chips, or null when no chips
 * are set. INCLUDE chips OR together, then AND with the exclude arms so an
 * excluded place is subtracted from the cohort. Combined into one `and(...)`
 * tree so PostgREST AND's the groups. */
export const districtsFilterClause = (
  districts: ReadonlyArray<DistrictChip>,
): string | null => {
  if (!districts.length) return null;
  const plan = districtCodePlan(districts);
  const groups: string[] = [];
  const inc = includeArms(plan.include);
  if (inc.length) groups.push(`or(${inc.join(',')})`);
  groups.push(...excludeArms(plan.exclude));
  return groups.length ? `and(${groups.join(',')})` : null;
};

/* The in-memory counterpart, for the surface that loads its rows whole and
 * filters locally (the pipeline board, rule 22). Same plan, same levels, same
 * include/exclude split — there is no second definition to keep in lockstep any
 * more, only a second RENDERING of the one plan. */
export interface DistrictMatchRow {
  obec_id: number | null;
  okres_id: number | null;
  region_id: number | null;
  cast_obce_id: number | null;
  ulice_id: number | null;
}

export const matchesDistricts = (
  row: DistrictMatchRow,
  districts: ReadonlyArray<DistrictChip>,
): boolean => {
  if (!districts.length) return true;
  const plan = districtCodePlan(districts);
  const hits = (side: CodesByLevel): boolean =>
    DISTRICT_LEVEL_ORDER.some((level) => {
      const codes = side[level];
      if (!codes || !codes.length) return false;
      const value = row[DISTRICT_LEVEL_COLUMN[level]];
      return value != null && codes.includes(value);
    });
  const anyInclude = DISTRICT_LEVEL_ORDER.some((l) => (plan.include[l]?.length ?? 0) > 0);
  const included = !anyInclude || hits(plan.include);
  return included && !hits(plan.exclude);
};

/* Czech level names for the chip badge — a chip shows WHAT it selected, not
 * just a string, so okres "Jihlava" reads differently from obec "Jihlava". */
const DISTRICT_LEVEL_LABEL: Record<LocationLevel, string> = {
  kraj: 'Kraj',
  okres: 'Okres',
  obec: 'Obec',
  cast_obce: 'Část obce',
  ulice: 'Ulice',
};

export const districtChipBadge = (chip: DistrictChip): string =>
  DISTRICT_LEVEL_LABEL[chip.level];

/* Chip identity IS its (level, code) — two picks of the same unit are the same
 * chip however Mapy spelled them. Used for dedupe, remove and the React key. */
export const districtChipKey = (chip: DistrictChip): string =>
  `${chip.level}:${chip.id}`;
