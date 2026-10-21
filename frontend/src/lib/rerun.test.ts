/* A re-run keeps the subject's property out of its own cohort (decision 13):
 * a spec re-run resolves no subject, so it carries the parent's. Any re-run of a
 * run with an ad is of that ad (adjusted: its edits as spec_overrides), so the new
 * run stays on its page. */

import { describe, expect, it } from 'vitest';

import { buildRerunPayload, canRerun } from './rerun';
import type { EstimationRun, TargetSpecIn } from './types';

const SPEC: TargetSpecIn = { lat: 50.1, lng: 14.4, area_m2: 60, disposition: '2+kk', floor: 3 };

const run = (over: Partial<EstimationRun>): EstimationRun =>
  ({
    id: 7,
    input_url: null,
    input_listing_id: null,
    input_spec: null,
    estimate_kind: 'rent',
    input_purchase_price_czk: null,
    special_instructions: null,
    contextual_text: null,
    ...over,
  }) as EstimationRun;

describe('buildRerunPayload', () => {
  it('an adjusted re-run of a spec keeps its exclusion', () => {
    const parent = run({ input_spec: { ...SPEC, exclude_listing_ids: [555] } });
    const body = buildRerunPayload(parent, { spec: { ...SPEC, area_m2: 62 } });
    expect(body.spec).toEqual({ ...SPEC, area_m2: 62, exclude_listing_ids: [555] });
    expect(body.parent_run_id).toBe(7);
  });

  it('an adjusted re-run of an ad stays on the ad, its edits as spec_overrides', () => {
    const parent = run({ input_listing_id: 555, input_spec: { ...SPEC, exclude_listing_ids: [555, 600] } });
    const body = buildRerunPayload(parent, { spec: { ...SPEC, area_m2: 62, exclude_listing_ids: [555] } });
    expect(body.listing_id).toBe(555);
    expect(body.spec).toBeUndefined();
    /* The API excludes the subject itself; only the parent's other exclusions ride along. */
    expect(body.spec_overrides).toEqual({ ...SPEC, area_m2: 62, exclude_listing_ids: [600] });
    expect(buildRerunPayload(run({ input_listing_id: 555, input_spec: SPEC }), { spec: SPEC }).spec_overrides)
      .toEqual(SPEC);
  });

  it('the same-inputs re-run of an ad is of the ad', () => {
    const parent = run({ input_url: 'https://www.sreality.cz/detail/x/1', input_listing_id: 901, input_spec: SPEC });
    const same = buildRerunPayload(parent);
    expect(same.listing_id).toBe(901);
    expect(same.spec).toBeUndefined();
    expect('url' in same).toBe(false);
  });

  it('a run with no ad re-runs its spec; a subjectless spec carries no exclusion', () => {
    expect(buildRerunPayload(run({ input_spec: SPEC })).spec).toEqual(SPEC);
    expect(buildRerunPayload(run({ input_spec: SPEC })).listing_id).toBeUndefined();
  });

  it('a historic pasted-URL run with no ad and no spec cannot be re-run', () => {
    expect(canRerun(run({ input_url: 'https://example.cz/inzerat/1' }))).toBe(false);
    expect(canRerun(run({ input_listing_id: 3 }))).toBe(true);
    expect(canRerun(run({ input_spec: SPEC }))).toBe(true);
  });
});
