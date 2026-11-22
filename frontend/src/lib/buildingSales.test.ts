import { describe, expect, it } from 'vitest';
import type { BuildingSale, CadastreDeed } from './api';
import { saleRows } from './buildingSales';

const SALE: BuildingSale = {
  source: 'reas', source_record_id: '1_unit_22906291-565-54', sold_at: '2023-09-12', price_czk: 5990000,
  asking_last_czk: null, category_main: 'byt', disposition: '3+kk', area_m2: 61, unit_label: '565/54',
  source_url: 'https://example.test/x', price_per_m2: 98197, price_per_m2_basis: 'sale_capital_czk_m2',
};
const deed = (over: Partial<CadastreDeed>): CadastreDeed => ({
  proceeding: 'V-1/2025-101', kind: 'Smlouva kupní', price: 6450000, currency: 'CZK', signed_on: '2025-03-01',
  effective_on: '2025-03-04', entered_on: '2025-03-28',
  properties: [{ kind: 'jednotka', iskn_id: 1, label: '565/14' }], ...over,
});

describe('saleRows', () => {
  it('lists held sales and bought deeds together, newest first, each with its source', () => {
    const rows = saleRows([SALE], [deed({})]);
    expect(rows.map((r) => [r.source, r.unit, r.date])).toEqual([
      ['katastr', '565/14', '2025-03-04'], ['reas', '565/54', '2023-09-12']]);
  });

  it('never gives a deed an area or a per-m² figure', () => {
    const [row] = saleRows([], [deed({})]);
    expect([row.area_m2, row.price_per_m2, row.price_per_m2_basis]).toEqual([null, null, null]);
  });

  it('says what a deed transfers besides its units: one price covers all of it', () => {
    const [row] = saleRows([], [deed({ properties: [
      { kind: 'jednotka', iskn_id: 1, label: '565/31' }, { kind: 'jednotka', iskn_id: 2, label: '565/101' },
      { kind: 'parcela', iskn_id: 3, label: 'St. 115' }, { kind: 'parcela', iskn_id: 4, label: '3/1' },
    ] })]);
    expect(row.unit).toBe('565/31 + 565/101');
    expect(row.also).toBe('+ 2 pozemky');
  });

  it('reads a deed over a building alone as the whole building', () => {
    const [row] = saleRows([], [deed({ properties: [{ kind: 'stavba', iskn_id: 9, label: 'č.p. 88' }] })]);
    expect([row.unit, row.also]).toEqual([null, null]);
  });

  it('dates a deed by its legal effect, else by its signing', () => {
    expect(saleRows([], [deed({ effective_on: null })])[0].date).toBe('2025-03-01');
  });
});
