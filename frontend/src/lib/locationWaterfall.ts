/* The row shape of a NEW DEDUP candidate run's `stats.waterfall`, as the
 * candidates funnel reads it; a run's rows are as-of the run. The hourly audit
 * relation that shared this shape was dropped in W1 (migration 719).
 *
 * NO ARITHMETIC HAPPENS HERE. Every count, every loss and every share is
 * computed by the lane, from the ONE rule Browse and the resolver use
 * (`SERVED_LOCATION_PREDICATE` in location_data/claims_common.py: a listing is
 * served once the store has a point for it, or has determined it is abroad). A
 * client that recomputed a step would be a second definition of the same
 * question — so this module only orders and nests.
 *
 * TWO KINDS OF ROW:
 *   'chain'     the funnel itself — `lost` is the previous chain step's count
 *               minus this one's, written by the lane, never derived here;
 *   'split'     a sub-row of its parent (`parent_key`).
 *
 * The WORDING of a step is in `lib/locationSteps.ts`.
 */

export type WaterfallKind = 'chain' | 'split';

export interface WaterfallRow {
  step_key: string;
  step_no: number;
  /* 0 = the step itself; 1..n = its sub-rows. */
  sub_no: number;
  kind: WaterfallKind;
  /* The step a sub-row belongs under; NULL on a chain step. */
  parent_key: string | null;
  n: number;
  /* Chain rows only. NULL where "lost" is not a truthful word for the row. */
  lost: number | null;
  /* Share of every listing the run looked at. */
  share_pct: number;
}

export interface WaterfallStep {
  row: WaterfallRow;
  splits: WaterfallRow[];
}

/* The steps in order, each carrying the sub-rows that partition it. Nesting is
 * by `parent_key` and not by `step_no`, so a sub-row can only ever hang off a
 * step that actually exists. */
export const groupWaterfall = (
  rows: ReadonlyArray<WaterfallRow>,
): WaterfallStep[] => {
  const steps = rows
    .filter((r) => r.kind !== 'split')
    .slice()
    .sort((a, b) => a.step_no - b.step_no || a.sub_no - b.sub_no);
  const splits = new Map<string, WaterfallRow[]>();
  for (const r of rows) {
    if (r.kind !== 'split' || r.parent_key == null) continue;
    const bucket = splits.get(r.parent_key);
    if (bucket) bucket.push(r);
    else splits.set(r.parent_key, [r]);
  }
  for (const bucket of splits.values()) bucket.sort((a, b) => a.sub_no - b.sub_no);
  return steps.map((row) => ({ row, splits: splits.get(row.step_key) ?? [] }));
};
