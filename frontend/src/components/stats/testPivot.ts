import { normalizePivot, type PivotPayload, type StatsCell, type StatsPivot } from '@/lib/stats';

/* Shared fixtures for the stats component tests. */
export const cell = (
  keys: string[],
  n: number,
  median: number | null,
  col: string | null = null,
  spread = 10,
): StatsCell => ({
  keys,
  col,
  n,
  mean: median,
  median,
  p10: median == null ? null : median - 2 * spread,
  p25: median == null ? null : median - spread,
  p75: median == null ? null : median + spread,
  p90: median == null ? null : median + 2 * spread,
});

/* A full payload as the RPC sends it, read through the one normaliser. */
export const pivotOf = (over: Partial<PivotPayload> = {}): StatsPivot => {
  const spec = over.spec ?? { rows: [], cols: [], measure: 'area' };
  return normalizePivot(
    {
      spec,
      filtered: 100,
      sample: 80,
      excluded: 20,
      category_main: 'byt',
      category_type: 'prodej',
      status: 'active',
      basis: 'sale_capital_czk_m2',
      min_n: 10,
      refused: null,
      places: [],
      cells: [],
      ...over,
    },
    spec,
  );
};

/* The stub a `bad_spec` refusal answers with: no counts, no cohort facts. */
export const badSpecPivot = (): StatsPivot =>
  normalizePivot({ refused: 'bad_spec', reason: 'unknown measure', cells: [] }, { rows: [], cols: [], measure: 'area' });
