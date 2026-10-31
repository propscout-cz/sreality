/* The location waterfall's row shape (`location_audit_waterfall`, migrations
 * 523/524). W1 deleted the audit page's reader of the hourly relation: only the
 * dedup candidates funnel reads these rows now, from a run's `stats.waterfall`.
 *
 * The page used to state ONE number — the listings no consumer can see — with
 * nothing to read it against. This relation is the whole chain, from every
 * listing in the database down to that set, so "skryté" is read against the
 * database and not against itself.
 *
 * NO ARITHMETIC HAPPENS HERE. Every count, every loss and every share is
 * computed in the store, in one statement, from the ONE rule Browse and the
 * resolver use (`SERVED_LOCATION_PREDICATE` in location_data/claims_common.py:
 * a listing is served once the store has a point for it, or has determined it is
 * abroad). A client that recomputed a step would be a second definition of the
 * same question, which is the one thing this wave exists to prevent — so this
 * module only orders and nests.
 *
 * THREE KINDS OF ROW:
 *   'chain'     the funnel itself — `lost` is the previous chain step's count
 *               minus this one's, written by the store, never derived here;
 *   'deduction' a set carved OUT of the chain (the hidden set: every listing
 *               that fails the rule) — it does not narrow the next step, so it
 *               carries no loss;
 *   'split'     a sub-row partitioning its parent (`parent_key`) exactly.
 *
 * ONE ROW SHAPE, TWO PRODUCERS (W16). The hourly audit relation writes these
 * rows; a NEW DEDUP candidate run stamps the SAME keys, kinds and columns into
 * its own `stats.waterfall`; a run's rows are as-of the run. The WORDING of a
 * step is in `lib/locationSteps.ts`; `label_cs` left this table in migration
 * 526 so a label could not differ by surface.
 */

export type WaterfallKind = 'chain' | 'deduction' | 'split';

export interface WaterfallRow {
  step_key: string;
  step_no: number;
  /* 0 = the step itself; 1..n = its sub-rows. */
  sub_no: number;
  kind: WaterfallKind;
  /* The step a sub-row (or a deduction) belongs under; NULL on a chain step. */
  parent_key: string | null;
  n: number;
  /* Chain rows only. NULL where "lost" is not a truthful word for the row. */
  lost: number | null;
  /* Share of EVERY listing ever collected — the point of the wave. */
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
