/* What a Browse card says about the sales in its building: how many we hold and the median of their measured per-m²
 * figures. The figures and their basis come from the server's measure and are only ranked here, never re-derived;
 * sales on more than one basis have no common unit, so their median is a gap (rule 23). Building-level: the card
 * never claims a sale is this property's own flat. */
import type { BuildingSale } from './api';
import { ppm2BasisFromToken, type Ppm2Basis } from './measure';

export interface BuildingSalesSummary {
  count: number;
  medianPpm2: number | null;
  basis: Ppm2Basis | null;
}

export function summarizeBuildingSales(sales: readonly BuildingSale[]): BuildingSalesSummary {
  const priced = sales.filter((s) => s.price_per_m2 != null && s.price_per_m2_basis != null);
  const tokens = new Set(priced.map((s) => s.price_per_m2_basis));
  if (priced.length === 0 || tokens.size !== 1) return { count: sales.length, medianPpm2: null, basis: null };
  const values = priced.map((s) => s.price_per_m2 as number).sort((a, b) => a - b);
  const mid = Math.floor(values.length / 2);
  return {
    count: sales.length,
    medianPpm2: values.length % 2 === 1 ? values[mid] : (values[mid - 1] + values[mid]) / 2,
    basis: ppm2BasisFromToken([...tokens][0]),
  };
}
