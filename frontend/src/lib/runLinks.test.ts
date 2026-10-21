/* One run, one surface.
 *
 * runSurfaceUrl decides where an estimation run "lives": a run of an ad we hold
 * lives on that ad's page, addressed by the ad's natural key (?run= selects it,
 * #estimations scrolls to it), whichever portal it is on; a run that never had
 * an ad keeps the standalone /estimation/:id surface. Several callers used to
 * build `/estimation/${id}` directly, so the SAME run resolved to two different
 * surfaces depending on which page you clicked from. These pin the contract
 * those callers now share. */
import { describe, expect, it } from 'vitest';
import { hasListingSurface, runSurfaceUrl } from './runLinks';

const orphan = (id: number) => ({ id, subject_source: null, subject_source_id_native: null });

describe('runSurfaceUrl', () => {
  it('sends a run of a sreality ad to its listing alias', () => {
    expect(
      runSurfaceUrl({ id: 99, subject_source: 'sreality', subject_source_id_native: '12345' }),
    ).toBe('/listing/sreality/12345?run=99#estimations');
  });

  /* The defect this fixes: a bound idnes / bezrealitky run has no sreality id,
   * so it used to land on the standalone "not in our database" page. */
  it('sends a run of a non-sreality ad to its listing alias, not the orphan page', () => {
    const run = { id: 41, subject_source: 'idnes', subject_source_id_native: '6a147cfde222cf687509e018' };
    expect(runSurfaceUrl(run)).toBe('/listing/idnes/6a147cfde222cf687509e018?run=41#estimations');
    expect(hasListingSurface(run)).toBe(true);
  });

  it('encodes a free-form native id rather than reshaping the route', () => {
    expect(
      runSurfaceUrl({ id: 5, subject_source: 'remax', subject_source_id_native: 'a/b?c' }),
    ).toBe('/listing/remax/a%2Fb%3Fc?run=5#estimations');
  });

  it('falls back to the standalone surface only for a run that never had an ad', () => {
    // The default hash is '#estimations', and it is DROPPED here: the
    // standalone page has no such anchor, so keeping it would scroll nowhere.
    expect(runSurfaceUrl(orphan(99))).toBe('/estimation/99');
    expect(hasListingSurface(orphan(99))).toBe(false);
  });

  /* The standalone page renders an id="feedback" anchor but has NO
   * #estimations target, so the hash that cannot resolve is dropped rather than
   * left to scroll nowhere. Deleting this branch is a behaviour change, not a
   * cleanup. */
  it('drops #estimations on the standalone surface but keeps #feedback', () => {
    expect(runSurfaceUrl(orphan(7), '#feedback')).toBe('/estimation/7#feedback');
    expect(runSurfaceUrl(orphan(7), '#estimations')).toBe('/estimation/7');
  });

  it('keeps both hashes on the listing surface, which anchors both', () => {
    expect(
      runSurfaceUrl({ id: 7, subject_source: 'sreality', subject_source_id_native: '1' }, '#feedback'),
    ).toBe('/listing/sreality/1?run=7#feedback');
  });
});
