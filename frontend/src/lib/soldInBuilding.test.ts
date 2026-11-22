import { describe, expect, it } from 'vitest';
import type { BuildingSale } from './api';
import { summarizeBuildingSales } from './soldInBuilding';

const sale = (ppm2: number | null, basis: string | null = 'sale_capital_czk_m2'): BuildingSale => ({
  source: 'reas', source_record_id: String(ppm2), sold_at: '2025-01-01', price_czk: 1, asking_last_czk: null,
  category_main: 'byt', disposition: null, area_m2: 60, unit_label: '1/1', source_url: null,
  price_per_m2: ppm2, price_per_m2_basis: ppm2 == null ? null : basis,
});

describe('summarizeBuildingSales', () => {
  it('counts every sale and takes the median of the measured figures', () => {
    expect(summarizeBuildingSales([sale(100), sale(300), sale(200)])).toEqual({ count: 3, medianPpm2: 200, basis: 'sale' });
    expect(summarizeBuildingSales([sale(100), sale(200)]).medianPpm2).toBe(150);
  });

  it('leaves a sale without a figure out of the median but in the count', () => {
    expect(summarizeBuildingSales([sale(100), sale(null)])).toEqual({ count: 2, medianPpm2: 100, basis: 'sale' });
  });

  it('gives no median over mixed bases or with no figure at all', () => {
    expect(summarizeBuildingSales([sale(100), sale(5, 'rent_monthly_czk_m2')]).medianPpm2).toBeNull();
    expect(summarizeBuildingSales([sale(null)])).toEqual({ count: 1, medianPpm2: null, basis: null });
    expect(summarizeBuildingSales([])).toEqual({ count: 0, medianPpm2: null, basis: null });
  });
});
