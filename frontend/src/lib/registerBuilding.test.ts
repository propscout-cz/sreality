import { describe, expect, it } from 'vitest';
import type { RegisterBuilding } from './api';
import { completionLabel, registerFacts } from './registerBuilding';

const code = (c: number, label: string, unknown = false) => ({ code: c, label, unknown });
const BUILDING: RegisterBuilding = {
  code: 22948236, iskn_building_id: 470801805, house_numbers: [564, 565], house_number_kind: 'č.p.',
  completed: { from: 1981, to: 1990 }, floors: 8, dwellings: 46, entrances: 2, built_up_area_m2: 1612,
  floor_area_m2: null, enclosed_volume_m3: null, protected: false, valid_from: '2023-12-18', source_date: '2026-10-31',
  usage: code(6, 'bytový dům'), construction: code(4, 'Stěnové panely'), lift: code(1, 'S výtahem'),
  heating: code(2, 'Centrální dálkové'), water: code(1, 'S vodovodem'), sewer: code(1, 'Přípoj'),
  gas: code(9, 'Nezjištěno', true),
};
const row = (label: string, ad = { total_floors: null as number | null, has_lift: null as boolean | null },
  b: RegisterBuilding = BUILDING) => registerFacts(b, ad).find((f) => f.label === label)!;

describe('registerFacts', () => {
  it('reads a completion placeholder as a period, a real date as a year, an open start as "do"', () => {
    expect(completionLabel({ from: 1981, to: 1990 })).toBe('1981–1990');
    expect(completionLabel({ from: 2012, to: 2012 })).toBe('2012');
    expect(completionLabel({ from: null, to: 1919 })).toBe('do 1919');
    expect(completionLabel(null)).toBeNull();
  });

  it('keeps what the register does not know as a gap', () => {
    expect(row('Plyn').value).toBeNull();
    expect(row('Podlahová plocha').value).toBeNull();
    expect(registerFacts(BUILDING, { total_floors: null, has_lift: null })).toHaveLength(14);
  });

  it('shows the ad beside a differing register value and says nothing when they agree', () => {
    expect(row('Podlaží', { total_floors: 7, has_lift: null })).toMatchObject({ value: '8', note: 'inzerát uvádí 7' });
    expect(row('Podlaží', { total_floors: 8, has_lift: null }).note).toBeUndefined();
    expect(row('Výtah', { total_floors: null, has_lift: false }).note).toBe('inzerát uvádí bez výtahu');
    expect(row('Výtah', { total_floors: null, has_lift: true }).note).toBeUndefined();
    expect(row('Výtah', { total_floors: null, has_lift: true }, { ...BUILDING, lift: code(2, 'Bez výtahu') }).note)
      .toBe('inzerát uvádí výtah');
  });

  it('never calls an unknown lift a disagreement', () => {
    expect(row('Výtah', { total_floors: null, has_lift: true }, { ...BUILDING, lift: code(9, 'Nezjištěno', true) }))
      .toEqual({ label: 'Výtah', value: null });
  });
});
