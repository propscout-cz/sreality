import type {
  CreateEstimationIn,
  EstimationProvider,
  EstimationRun,
  Lifecycle,
  TargetSpecIn,
} from '@/lib/types';

/* Re-run helpers shared between the Estimations list and detail pages.
 * Detail page owns the "Adjust & re-run" panel; the list page exposes
 * a minimal "Re-run with same inputs" affordance on failed rows. Both
 * paths build their POST /estimations body through buildRerunPayload
 * so the new row links to its parent via parent_run_id and the audit
 * trail stays consistent. */

export type RerunOverrides = {
  spec?: TargetSpecIn;
  estimate_kind?: 'rent' | 'sale';
  provider?: EstimationProvider;
  lifecycle?: Lifecycle;
  purchase_price_czk?: number | null;
  expected_monthly_rent_czk?: number | null;
};

export interface RerunInput {
  run: EstimationRun;
  overrides?: RerunOverrides;
}

export function canRerun(run: EstimationRun): boolean {
  return run.input_listing_id != null || run.input_spec != null;
}

export function buildRerunPayload(
  run: EstimationRun,
  overrides?: RerunOverrides,
): CreateEstimationIn {
  const estimateKind =
    overrides?.estimate_kind ?? run.estimate_kind ?? 'rent';
  const mode = estimateKind === 'rent' ? 'agent' : 'deterministic';
  const purchasePrice =
    overrides?.purchase_price_czk !== undefined
      ? overrides.purchase_price_czk
      : run.input_purchase_price_czk;
  const expectedRent =
    overrides?.expected_monthly_rent_czk !== undefined
      ? overrides.expected_monthly_rent_czk
      : null;

  const base: CreateEstimationIn = {
    source: 'ui',
    mode,
    estimate_kind: estimateKind,
    parent_run_id: run.id,
    rerun_reason: overrides ? 'adjust' : 'manual',
    purchase_price_czk: purchasePrice,
    expected_monthly_rent_czk: expectedRent,
    /* Carry operator inputs forward on a re-run. The new row stores its
     * own copy; the original stays untouched (audit invariant). */
    special_instructions: run.special_instructions ?? null,
    contextual_text: run.contextual_text ?? null,
    ...(overrides?.provider ? { provider: overrides.provider } : {}),
    ...(overrides?.lifecycle ? { lifecycle: overrides.lifecycle } : {}),
  };

  /* The parent's exclusions ride along (decision 13). A re-run of an ad names the
   * ad, and the API keeps the ad itself out of the cohort, so it is never listed. */
  const carried = (run.input_spec?.exclude_listing_ids ?? []).filter(
    (id) => id !== run.input_listing_id,
  );
  const withCarried = <S extends Partial<TargetSpecIn>>(spec: S): S =>
    carried.length > 0 ? { ...spec, exclude_listing_ids: carried } : spec;

  if (run.input_listing_id != null) {
    /* Any re-run of an ad stays on the ad's page: same inputs re-read the ad (its
     * current price and facts); an adjusted one sends its edits as spec_overrides. */
    if (!overrides?.spec) return { ...base, listing_id: run.input_listing_id };
    const edits: Partial<TargetSpecIn> = { ...overrides.spec };
    delete edits.exclude_listing_ids;
    return { ...base, listing_id: run.input_listing_id, spec_overrides: withCarried(edits) };
  }
  const spec = overrides?.spec ?? run.input_spec;
  return { ...base, spec: spec ? withCarried(spec) : undefined };
}
