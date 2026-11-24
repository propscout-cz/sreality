import { describe, expect, it } from 'vitest';
import type { RegisterBuilding } from './api';
import { completionFact, registerDifference, registerFacts } from './registerBuilding';

const code = (c: number, label: string | null, unknown = false) => ({ code: c, label, unknown });
const BUILDING: RegisterBuilding = {
  code: 22948236, iskn_building_id: 470801805, house_numbers: [564, 565], house_number_kind: 'č.p.',
  completed: { on: '1990-12-31', from: 1981, to: 1990 }, floors: 8, dwellings: 46, entrances: 2,
  built_up_area_m2: 1612, floor_area_m2: null, enclosed_volume_m3: null, valid_from: '2023-12-18',
  source_date: '2026-10-31', usage: code(6, 'bytový dům'), construction: code(4, 'Stěnové panely'),
  lift: code(1, 'S výtahem'), heating: code(2, 'Centrální dálkové'), water: code(1, 'S vodovodem'),
  sewer: code(1, 'Přípoj'), gas: code(9, 'Nezjištěno', true), protection: [],
};
const AD = 31;
const verdicts = (over: Partial<NonNullable<RegisterBuilding['ads']>[string]>) => ({
  ads: { [AD]: { lift: null, floors: null, construction: null, ...over } },
});
const row = (label: string, b: RegisterBuilding = BUILDING) => registerFacts(b, AD).find((f) => f.label === label)!;

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
    expect(row('Vodovod', { ...BUILDING, water: code(3, null) }).value).toBe('kód 3');
  });

  it('words the ad beside each row the API says differs, and only there', () => {
    const b = { ...BUILDING, ...verdicts({
      lift: { differs: true, ad: false },
      floors: { differs: true, ad: { floor: 8, total_floors: 9 } },
      construction: { differs: true, ad: 'cihla' },
    }) };
    expect(row('Výtah', b).note).toBe('inzerát uvádí bez výtahu');
    expect(row('Podlaží včetně podzemních', b)).toEqual({
      label: 'Podlaží včetně podzemních', value: '8', note: 'inzerát uvádí 9. podlaží z 9' });
    expect(row('Konstrukce', b).note).toBe('inzerát uvádí: Cihla');
    expect(row('Využití', b).note).toBeUndefined();
  });

  it('words an ad that states only a storey count', () => {
    const b = { ...BUILDING, ...verdicts({ floors: { differs: true, ad: { floor: null, total_floors: 9 } } }) };
    expect(row('Podlaží včetně podzemních', b).note).toBe('inzerát uvádí 9 podlaží');
  });

  it('says nothing where the ad agrees, has no verdict, is another ad, or the answer holds no verdicts', () => {
    const agreeing = { ...BUILDING, ...verdicts({ lift: { differs: false, ad: true } }) };
    expect(row('Výtah', agreeing)).toEqual({ label: 'Výtah', value: 'S výtahem' });
    expect(row('Konstrukce', agreeing).note).toBeUndefined();
    const other = { ...BUILDING, ads: { 99: { lift: { differs: true, ad: false }, floors: null, construction: null } } };
    expect(row('Výtah', other).note).toBeUndefined();
    expect(row('Výtah').note).toBeUndefined();
  });

  it('names the register side for a mark beside the ad value', () => {
    const b = { ...BUILDING, ...verdicts({ floors: { differs: true, ad: { floor: 8, total_floors: null } },
      lift: { differs: false, ad: true } }) };
    expect(registerDifference(b, AD, 'floors')).toBe('Liší se od RÚIAN. Podlaží včetně podzemních: 8');
    expect(registerDifference(b, AD, 'lift')).toBeNull();
    expect(registerDifference(b, AD, 'construction')).toBeNull();
  });

  it('names every protection the register lists, under a row that names no kind', () => {
    const b = { ...BUILDING, protection: [{ code: 18, label: 'první' }, { code: 99, label: null }] };
    expect(row('Ochrana', b).value).toBe('první; kód 99');
  });

  it('keeps two protections two when a label holds a comma', () => {
    const b = { ...BUILDING, protection: [
      { code: 17, label: 'pam. zóna - budova, pozemek v památkové zóně' }, { code: 18, label: 'nemovitá kulturní památka' }] };
    expect(row('Ochrana', b).value!.split('; ')).toHaveLength(2);
  });

  it('shows a gap, and does not throw, on an answer from an API that serves no list yet', () => {
    const { protection: _, ...older } = BUILDING;
    expect(row('Ochrana', older)).toEqual({ label: 'Ochrana', value: null });
  });

  it('says the register states none, which is not a gap in what it knows', () => {
    expect(row('Ochrana').value).toBe('registr žádnou neuvádí');
  });
});
