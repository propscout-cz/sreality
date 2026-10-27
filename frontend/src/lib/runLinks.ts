import type { EstimationRun } from './types';
import { advertPath } from './listingUrl';
import { ROUTES, withHash, withQuery, type RoutePath } from './routes';

/* The one place that decides where an estimation run "lives" in the UI.
 *
 * A run of an ad we hold (its subject's natural key in the run projection)
 * lives on that ad's page, `/listing/{source}/{native}`: the alias lands on the
 * property page, whose estimations section selects the run via ?run= and
 * #estimations scrolls it into view. Only a run that never had an ad (the
 * historic pasted-URL and spec runs) keeps the standalone /estimation/:id
 * surface. Every cross-link to a run — the estimations list, the post-create
 * navigation, re-run redirects, and the /estimation/:id route itself — routes
 * through this helper so the two surfaces can never disagree. */
type RunSubject = Pick<EstimationRun, 'id' | 'subject_source' | 'subject_source_id_native'>;

const subjectPath = (run: RunSubject): RoutePath | null =>
  advertPath({ source: run.subject_source, source_id_native: run.subject_source_id_native });

export const hasListingSurface = (run: RunSubject): boolean => subjectPath(run) != null;

export function runSurfaceUrl(
  run: RunSubject,
  hash: '#estimations' | '#feedback' = '#estimations',
): RoutePath {
  const listing = subjectPath(run);
  if (listing != null) return withHash(withQuery(listing, { run: run.id }), hash);
  // The standalone page renders an `id="feedback"` anchor (RunPanel) but has no
  // `#estimations` target, so the hash that cannot resolve is dropped rather
  // than left to scroll nowhere. Not a redundancy — deleting it is a behaviour
  // change.
  return withHash(ROUTES.estimationDetail.build({ id: run.id }), hash === '#feedback' ? '#feedback' : '');
}
