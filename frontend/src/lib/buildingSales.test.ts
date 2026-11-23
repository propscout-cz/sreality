import { describe, expect, it } from 'vitest';
import type { BuildingSale, CadastreDeed } from './api';
import { saleRows, showsCadastre } from './buildingSales';

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

  it('shows a deed matched to a held sale as that sale, once, with the proceeding beside it', () => {
    const rows = saleRows([SALE], [deed({ match: {
      source: 'reas', source_record_id: SALE.source_record_id, property_id: null, area_m2: null, disposition: null,
      price_per_m2: null, price_per_m2_basis: null } })]);
    expect(rows.map((r) => [r.source, r.unit, r.proceeding, r.area_m2])).toEqual([['reas', '565/54', 'V-1/2025-101', 61]]);
    expect(showsCadastre(rows)).toBe(true);
    expect(showsCadastre(saleRows([SALE], []))).toBe(false);
  });

  it('keeps what the deed transfers besides the unit on the held sale that stands for it', () => {
    const [row] = saleRows([SALE], [deed({
      properties: [{ kind: 'jednotka', iskn_id: 1, label: '565/54' }, { kind: 'parcela', iskn_id: 2, label: 'St. 1' }],
      match: { source: 'reas', source_record_id: SALE.source_record_id, property_id: null, area_m2: null,
        disposition: null, price_per_m2: null, price_per_m2_basis: null } })]);
    expect([row.source, row.also]).toEqual(['reas', '+ pozemek']);
  });

  it('keeps a deed whose matched sale is not in the list', () => {
    const rows = saleRows([], [deed({ match: {
      source: 'reas', source_record_id: 'gone', property_id: null, area_m2: null, disposition: null,
      price_per_m2: null, price_per_m2_basis: null } })]);
    expect(rows.map((r) => r.source)).toEqual(['katastr']);
  });

  it('lends a deed the area of the advertisement that asked its price, and names the property', () => {
    const [row] = saleRows([], [deed({ match: {
      source: 'ad', source_record_id: null, property_id: 71257, area_m2: 18, disposition: '1+kk',
      price_per_m2: 358333, price_per_m2_basis: 'sale_capital_czk_m2' } })]);
    expect([row.area_m2, row.disposition, row.price_per_m2, row.price_per_m2_basis, row.area_property_id])
      .toEqual([18, '1+kk', 358333, 'sale_capital_czk_m2', 71257]);
  });

  it('names a deed over many units as one price for all of them', () => {
    const properties = [1, 2, 3, 4].map((n) => ({ kind: 'jednotka' as const, iskn_id: n, label: `597/${n}` }));
    const [row] = saleRows([], [deed({ properties })]);
    expect([row.unit, row.scope]).toEqual([null, '4 jednotky za jednu cenu']);
  });

  it('gives an unmatched deed no area and no per-m² figure', () => {
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

  it('never calls a deed over several buildings, or over land alone, "the whole building"', () => {
    const scope = (properties: CadastreDeed['properties']) => saleRows([], [deed({ properties })])[0];
    const one = scope([{ kind: 'stavba', iskn_id: 1, label: 'č.p. 565' }, { kind: 'parcela', iskn_id: 2, label: 'St. 1' }]);
    expect([one.unit, one.scope, one.also]).toEqual([null, null, '+ pozemek']);
    const two = scope([{ kind: 'stavba', iskn_id: 1, label: 'č.p. 565' }, { kind: 'stavba', iskn_id: 3, label: 'garáž' }]);
    expect([two.unit, two.scope]).toEqual([null, '2 stavby společně']);
    expect(scope([{ kind: 'parcela', iskn_id: 2, label: 'St. 1' }]).scope).toBe('bez stavby');
  });
});
