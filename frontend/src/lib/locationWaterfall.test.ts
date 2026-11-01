/* The waterfall's grouping, as the dedup candidates funnel reads a run's rows.
 *
 * Two properties, and each is a way the page could quietly mislead the operator:
 *   - the steps come back in the lane's order, with their sub-rows nested under
 *     the parent they actually name;
 *   - nothing here recomputes a number: the lane's `lost` is asserted on the
 *     payload rather than re-derived from it.
 */

import { describe, expect, it } from 'vitest';

import { groupWaterfall, type WaterfallRow } from './locationWaterfall';

const row = (
  step_no: number,
  sub_no: number,
  step_key: string,
  kind: WaterfallRow['kind'],
  parent_key: string | null,
  n: number,
  lost: number | null,
): WaterfallRow => ({
  step_key,
  step_no,
  sub_no,
  kind,
  parent_key,
  n,
  lost,
  share_pct: Math.round((n / 841428) * 100_000) / 1000,
});

/* A run's chain (location_steps.RUN_STEPS). Deliberately NOT in step order: the
 * reader must impose the order, not inherit whatever arrived. */
const ROWS: WaterfallRow[] = [
  row(3, 2, 'located_no_town', 'split', 'located', 892, null),
  row(1, 0, 'all_listings', 'chain', null, 841428, 0),
  row(3, 1, 'located_foreign', 'split', 'located', 45582, null),
  row(2, 0, 'with_verdict', 'chain', null, 841414, 14),
  row(3, 0, 'located', 'chain', null, 741604, 99810),
  row(4, 0, 'located_town', 'chain', null, 695130, 46474),
];

describe('groupWaterfall', () => {
  it('returns the chain in step order', () => {
    expect(groupWaterfall(ROWS).map((s) => s.row.step_key)).toEqual([
      'all_listings',
      'with_verdict',
      'located',
      'located_town',
    ]);
  });

  it('nests every sub-row under the step it names', () => {
    const byKey = new Map(groupWaterfall(ROWS).map((s) => [s.row.step_key, s]));
    expect(byKey.get('located')!.splits.map((r) => r.step_key)).toEqual([
      'located_foreign',
      'located_no_town',
    ]);
    /* A chain step with no split gets an empty list, never undefined. */
    expect(byKey.get('with_verdict')!.splits).toEqual([]);
  });

  it('carries the arithmetic the lane wrote: lost = previous n − n', () => {
    const chain = groupWaterfall(ROWS)
      .map((s) => s.row)
      .filter((r) => r.kind === 'chain');
    expect(chain[0].lost).toBe(0);
    chain.slice(1).forEach((r, i) => {
      expect(r.lost).toBe(chain[i].n - r.n);
    });
  });
});
