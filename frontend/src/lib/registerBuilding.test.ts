import { describe, expect, it } from 'vitest';
import type { RegisterBuilding } from './api';
import { completionFact, registerFacts } from './registerBuilding';

const code = (c: number, label: string | null, unknown = false) => ({ code: c, label, unknown });
const BUILDING: RegisterBuilding = {
  code: 22948236, iskn_building_id: 470801805, house_numbers: [564, 565], house_number_kind: 'č.p.',
  completed: { on: '1990-12-31', from: 1981, to: 1990 }, floors: 8, dwellings: 46, entrances: 2,
  built_up_area_m2: 1612, floor_area_m2: null, enclosed_volume_m3: null, valid_from: '2023-12-18',
  source_date: '2026-10-31', usage: code(6, 'bytový dům'), construction: code(4, 'Stěnové panely'),
  lift: code(1, 'S výtahem'), heating: code(2, 'Centrální dálkové'), water: code(1, 'S vodovodem'),
  sewer: code(1, 'Přípoj'), gas: code(9, 'Nezjištěno', true),
};
const row = (label: string, ad = { has_lift: null as boolean | null }, b: RegisterBuilding = BUILDING) =>
  registerFacts(b, ad).find((f) => f.label === label)!;

describe('registerFacts', () => {
  it('marks a period as derived and names the register date it comes from', () => {
    const period = completionFact({ on: '1990-12-31', from: 1981, to: 1990 });
    expect(period.value).toBe('1981–1990');
    expect(period.derived).toBe('období odvozené z data v registru (31. 12. 1990)');
    expect(completionFact({ on: '1919-12-31', from: null, to: 1919 }).value).toBe('do 1919');
  });

  it('shows a real date as the register gives it, underived, and a silent register as a gap', () => {
    const real = completionFact({ on: '2012-06-30', from: 2012, to: 2012 });
    expect(real.derived).toBeUndefined();
    expect(real.value).toBe('30. 6. 2012');
    expect(completionFact(null).value).toBeNull();
  });

  it('keeps what the register does not know as a gap', () => {
    expect(row('Plyn').value).toBeNull();
    expect(row('Podlahová plocha').value).toBeNull();
  });

  it('shows a code our labels do not hold as its number, not as a gap', () => {
    expect(row('Vodovod', { has_lift: null }, { ...BUILDING, water: code(3, null) }).value).toBe('kód 3');
  });

  it('names the basis of the floor count and never flags it against the ad', () => {
    const floors = row('Podlaží včetně podzemních');
    expect(floors).toEqual({ label: 'Podlaží včetně podzemních', value: '8' });
  });

  it('shows the ad beside a differing lift and says nothing when they agree or the register is silent', () => {
    expect(row('Výtah', { has_lift: false }).note).toBe('inzerát uvádí bez výtahu');
    expect(row('Výtah', { has_lift: true }).note).toBeUndefined();
    expect(row('Výtah', { has_lift: true }, { ...BUILDING, lift: code(2, 'Bez výtahu') }).note).toBe('inzerát uvádí výtah');
    expect(row('Výtah', { has_lift: true }, { ...BUILDING, lift: code(9, 'Nezjištěno', true) }))
      .toEqual({ label: 'Výtah', value: null });
  });

  it('serves no protection row until its code list is seeded', () => {
    expect(registerFacts(BUILDING, { has_lift: null }).some((f) => /chran/i.test(f.label))).toBe(false);
  });
});
