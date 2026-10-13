/* The registered-sales cohort's shared rules (reas.cz via migration 545's
 * `sold_comparables`): the radii, the two categories the source covers, and the
 * one median the block states — read from the rows' server measure and basis
 * token, never re-derived. Shared by the property page's SoldCompsBlock and the
 * phone layout's compact list (#1767). */
import { ppm2BasisFromToken, type Ppm2Basis } from './measure';
import type { SoldComparable } from './types';

/* 5 km is also W2's cell margin — the obec envelope is expanded by exactly this
 * much before it is fetched, so the largest radius the operator can ask for is
 * fully covered by construction rather than by luck. */
export const RADII_M = [1000, 3000, 5000] as const;
export type RadiusM = (typeof RADII_M)[number];

/* reas.cz's sold catalogue is byty + domy only — measured, and the reason the
 * parser refuses any other `type` (docs/architecture.md § Data sources). */
export const COVERED_CATEGORIES = new Set(['byt', 'dum']);

/* A median over four sales is a number pretending to be a statistic. */
export const SUMMARY_MIN_ROWS = 5;

/* Sales under this area are held OUT of the median (never out of the table:
 * each row is an honest fact). Their Kč/m² is a denominator defect, not a
 * market fact — a small transfer routinely bundles a cellar or a parking share,
 * or states the unit's own floor area rather than the area transferred. It is
 * measured, not assumed: Prague flats 0–30 m² median 239,600 Kč/m² against
 * 144,506 in the 60–80 m² band (design/verify-reas-field-mapping.md). */
export const SMALL_UNIT_M2 = 30;

export type SoldSummary = {
  /* The sales the median is OF — not the cohort's size. */
  n: number;
  /* Held out for being under SMALL_UNIT_M2, and said out loud. */
  heldOut: number;
  medianPpm2: number | null;
  basis: Ppm2Basis | null;
  /* Flats and houses sell at different Kč/m² (measured: 114,519 against
   * 50,000), so one median over both is not a statistic about either. */
  mixedKinds: boolean;
};

/* The cohort's per-m² basis is READ from the rows' published basis token, never
 * re-derived: one token means that basis, more than one means 'mixed', which
 * `fmtMeasuredPricePerM2` refuses to put a unit on. */
export function summarizeSold(rows: readonly SoldComparable[]): SoldSummary | null {
  const kept = rows.filter((r) => r.area_m2 == null || r.area_m2 >= SMALL_UNIT_M2);
  if (kept.length < SUMMARY_MIN_ROWS) return null;
  const tokens = new Set(kept.map((r) => r.price_per_m2_basis));
  const basis =
    tokens.size === 1
      ? ppm2BasisFromToken([...tokens][0])
      : ('mixed' as Ppm2Basis);
  const shape = {
    n: kept.length,
    heldOut: rows.length - kept.length,
    basis,
    mixedKinds: new Set(rows.map((r) => r.category_main)).size > 1,
  };
  const values = kept
    .map((r) => r.price_per_m2)
    .filter((v): v is number => v != null)
    .sort((a, b) => a - b);
  if (values.length === 0) return { ...shape, medianPpm2: null };
  const mid = Math.floor(values.length / 2);
  return {
    ...shape,
    n: values.length,
    medianPpm2:
      values.length % 2 === 1 ? values[mid] : (values[mid - 1] + values[mid]) / 2,
  };
}
