import { describe, it, expect } from 'vitest';
import {
  BUILDING_GRANULARITY_RANK,
  isPinExact,
  pinPrecisionLabel,
  pinPrecisionShort,
  type PinPrecision,
} from './pinPrecision';

/* The ranks are migration 380's seed. They are spelled out here rather than
 * imported so a change to the lookup table has to be made twice, deliberately —
 * which rung counts as exact is a product decision, not a mirror of whatever
 * the table happens to say. */
const RANK = {
  obec: 40,
  cast_obce_or_quarter: 50,
  street: 60,
  parcel: 80,
  building: 90,
  address_point: 100,
} as const;

describe('isPinExact', () => {
  it('is exact at building level and above, approximate below', () => {
    expect(isPinExact({ granularity_rank: RANK.address_point })).toBe(true);
    expect(isPinExact({ granularity_rank: RANK.building })).toBe(true);
    expect(isPinExact({ granularity_rank: RANK.parcel })).toBe(false);
    expect(isPinExact({ granularity_rank: RANK.obec })).toBe(false);
  });

  it('pins the boundary to the building rung', () => {
    expect(BUILDING_GRANULARITY_RANK).toBe(RANK.building);
    expect(isPinExact({ granularity_rank: BUILDING_GRANULARITY_RANK - 1 })).toBe(false);
  });

  it('never calls an unresolved pin exact -- nobody claimed "exactly here"', () => {
    expect(isPinExact({ granularity_rank: null })).toBe(false);
    // Through the map's vector-tile round trip a NULL property arrives absent.
    expect(isPinExact({} as PinPrecision)).toBe(false);
  });
});

/* Ruling 2026-11-01 retired the radius: the tooltip names the rung and never a
 * metre figure, even for a row that still carries the stored one. */
describe('pinPrecisionLabel', () => {
  it('names the rung of an approximate pin and no radius', () => {
    const street = { granularity_rank: RANK.street, uncertainty_radius_m: 300 };
    expect(pinPrecisionLabel(street)).toBe('Přibližná poloha: ulice');
    expect(pinPrecisionLabel({ granularity_rank: RANK.obec })).toBe('Přibližná poloha: obec');
    expect(pinPrecisionLabel({ granularity_rank: RANK.cast_obce_or_quarter }))
      .toBe('Přibližná poloha: část obce');
    expect(pinPrecisionLabel({ granularity_rank: 30 })).toBe('Přibližná poloha: okres');
  });

  it('says exact for the building rungs', () => {
    expect(pinPrecisionLabel({ granularity_rank: RANK.address_point }))
      .toBe('Přesná poloha (adresní bod)');
    expect(pinPrecisionLabel({ granularity_rank: RANK.building })).toBe('Přesná poloha (budova)');
  });

  it('reads a rung inserted between two seeds as the coarser neighbour', () => {
    expect(pinPrecisionLabel({ granularity_rank: 45 })).toBe('Přibližná poloha: obec');
  });

  it('says so when there is no rung', () => {
    expect(pinPrecisionLabel({ granularity_rank: null })).toBe('Přesnost polohy neznámá');
  });
});

/* The popup's one line leads with these words; pinPrecisionLabel is its tooltip (P13). */
describe('pinPrecisionShort', () => {
  it('says exact or approximate, without the rung', () => {
    expect(pinPrecisionShort({ granularity_rank: RANK.address_point })).toBe('Přesná poloha');
    expect(pinPrecisionShort({ granularity_rank: RANK.building })).toBe('Přesná poloha');
    expect(pinPrecisionShort({ granularity_rank: RANK.street })).toBe('Přibližná poloha');
    expect(pinPrecisionShort({ granularity_rank: RANK.obec })).toBe('Přibližná poloha');
  });

  it('says so when there is no rung', () => {
    expect(pinPrecisionShort({ granularity_rank: null })).toBe('Přesnost polohy neznámá');
  });
});
