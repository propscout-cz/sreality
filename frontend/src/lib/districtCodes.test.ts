/* The SPA half of the ONE place predicate.
 *
 * The chip sets come from `tests/fixtures/district_chip_plan.json` — the SAME
 * file `tests/test_one_place_predicate.py` compiles with `api/location_filter`
 * and checks the two RPC bodies against. A chip set that plans differently here
 * than there is exactly how Browse and the Watchdog came to answer one saved
 * filter two ways; one table read by both languages makes that a red test. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  DISTRICT_LEVEL_ORDER,
  districtCodePlan,
  districtChipBadge,
  districtChipKey,
  districtsFilterClause,
  isDistrictChip,
  isLocationLevel,
  matchesDistricts,
  type DistrictMatchRow,
} from './districtCodes';
import type { DistrictChip } from './filters';

interface Case {
  name: string;
  chips: DistrictChip[];
  plan: { include: Record<string, number[]>; exclude: Record<string, number[]> };
  postgrest: string | null;
  rows: Array<{ row: Partial<DistrictMatchRow>; matches: boolean }>;
}

const TABLE = JSON.parse(
  readFileSync(
    join(process.cwd(), '..', 'tests', 'fixtures', 'district_chip_plan.json'),
    'utf-8',
  ),
) as { cases: Case[] };

const row = (over: Partial<DistrictMatchRow>): DistrictMatchRow => ({
  obec_id: null,
  okres_id: null,
  region_id: null,
  cast_obce_id: null,
  ulice_id: null,
  ...over,
});

/* Object key order is not part of the plan; compare level→codes as entries. */
const normalise = (side: Partial<Record<string, number[]>>): Record<string, number[]> =>
  Object.fromEntries(
    DISTRICT_LEVEL_ORDER.flatMap((l) => (side[l]?.length ? [[l, side[l]!]] : [])),
  );

describe('the one place predicate — the shared table', () => {
  it('holds only chips with a level and a positive code', () => {
    for (const c of TABLE.cases) {
      for (const chip of c.chips) {
        expect(isLocationLevel(chip.level)).toBe(true);
        expect(Number.isInteger(chip.id) && chip.id > 0).toBe(true);
      }
    }
  });

  for (const c of TABLE.cases) {
    it(`plans "${c.name}" the way the API does`, () => {
      const plan = districtCodePlan(c.chips);
      expect(normalise(plan.include)).toEqual(normalise(c.plan.include));
      expect(normalise(plan.exclude)).toEqual(normalise(c.plan.exclude));
    });

    it(`compiles "${c.name}" to the pinned PostgREST predicate`, () => {
      expect(districtsFilterClause(c.chips)).toBe(c.postgrest);
    });

    it(`filters rows for "${c.name}" the same way`, () => {
      for (const r of c.rows) {
        expect(matchesDistricts(row(r.row), c.chips)).toBe(r.matches);
      }
    });
  }
});

/* The street case, spelled out here too: a street chip filters at its street,
 * never at its town (ulice 666742 in obec 555134 Pardubice). */
describe('a street chip is its street', () => {
  const STREET: DistrictChip = {
    name: 'Na Rybníčkách', context: 'Pardubice', level: 'ulice', id: 666742,
  };

  it('compiles to the street column alone', () => {
    expect(districtsFilterClause([STREET])).toBe('and(or(ulice_id.in.(666742)))');
  });

  it('matches the street, not the rest of its town', () => {
    expect(matchesDistricts(row({ obec_id: 555134, ulice_id: 666742 }), [STREET])).toBe(true);
    expect(matchesDistricts(row({ obec_id: 555134 }), [STREET])).toBe(false);
    expect(matchesDistricts(row({ obec_id: 555134, ulice_id: 1 }), [STREET])).toBe(false);
  });

  it('subtracts only the street when excluded; a row with no street survives', () => {
    const excluded = { ...STREET, excluded: true };
    expect(districtsFilterClause([excluded]))
      .toBe('and(or(ulice_id.is.null,ulice_id.not.in.(666742)))');
    expect(matchesDistricts(row({ obec_id: 555134 }), [excluded])).toBe(true);
    expect(matchesDistricts(row({ obec_id: 555134, ulice_id: 666742 }), [excluded])).toBe(false);
  });
});

describe('the compiled predicate reads no text column', () => {
  it('never emits an ilike or a place_search_text arm', () => {
    for (const c of TABLE.cases) {
      const clause = districtsFilterClause(c.chips) ?? '';
      expect(clause).not.toContain('ilike');
      expect(clause).not.toContain('place_search_text');
      expect(clause).not.toContain('district.');
    }
  });
});

describe('chip identity and badge', () => {
  it('keys a chip on its unit, not its spelling', () => {
    const a: DistrictChip = { name: 'Praha', context: null, level: 'obec', id: 554782 };
    const b: DistrictChip = {
      name: 'Praha, hlavní město', context: 'Praha', level: 'obec', id: 554782,
    };
    expect(districtChipKey(a)).toBe(districtChipKey(b));
    expect(districtChipKey(a)).toBe('obec:554782');
  });

  it('tells an okres from the obec of the same name', () => {
    const okres: DistrictChip = { name: 'Jihlava', context: null, level: 'okres', id: 3707 };
    const obec: DistrictChip = { name: 'Jihlava', context: null, level: 'obec', id: 586846 };
    expect(districtChipKey(okres)).not.toBe(districtChipKey(obec));
    expect(districtChipBadge(okres)).toBe('Okres');
    expect(districtChipBadge(obec)).toBe('Obec');
  });

  it('badges the two finer levels', () => {
    expect(
      districtChipBadge({ name: 'Žižkov', context: 'Praha', level: 'cast_obce', id: 490067 }),
    ).toBe('Část obce');
    expect(
      districtChipBadge({ name: 'Na Rybníčkách', context: 'Pardubice', level: 'ulice', id: 666742 }),
    ).toBe('Ulice');
  });

  it('guards a chip from outside: a known level, a positive integer code, the right field types', () => {
    const ok = { name: 'Jihlava', context: null, level: 'obec', id: 586846 };
    expect(isDistrictChip(ok)).toBe(true);
    expect(isDistrictChip({ name: 'Žižkov', level: 'cast_obce', id: 490067, excluded: true })).toBe(true);
    for (const bad of [
      null, 'Jihlava', { name: 'Brno', context: null },
      { ...ok, level: 'stat' }, { ...ok, level: undefined },
      { ...ok, id: null }, { ...ok, id: 0 }, { ...ok, id: -1 }, { ...ok, id: 1.5 }, { ...ok, id: '586846' },
      { ...ok, name: 5 }, { ...ok, context: 5 }, { ...ok, excluded: 'yes' },
    ]) {
      expect(isDistrictChip(bad)).toBe(false);
    }
  });

  it('knows the five levels and nothing else', () => {
    expect(DISTRICT_LEVEL_ORDER).toEqual(['kraj', 'okres', 'obec', 'cast_obce', 'ulice']);
    expect(isLocationLevel('toString')).toBe(false);
    expect(isLocationLevel(null)).toBe(false);
  });
});
