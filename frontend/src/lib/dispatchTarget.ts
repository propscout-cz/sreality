import { propertyPath } from './listingUrl';
import { ROUTES, withQuery, type RoutePath } from './routes';
import type { WatchdogDispatch } from './types';

/* Where a notification opens: the ONE answer per dispatch row, for the feed
 * and every later surface that opens a notification. A watchdog or
 * collection-monitor row opens its property by `property_id` (a merge
 * re-points it, so a merged property lands on the survivor); a share opens what
 * was shared, by `share_kind`. A row with neither stays on the feed, which the
 * feed renders as no link. */
export function dispatchTarget(
  d: Pick<WatchdogDispatch, 'source_kind' | 'property_id' | 'collection_id' | 'share_kind' | 'share_ref'>,
): RoutePath {
  if (d.source_kind === 'watchdog' || d.source_kind === 'collection_monitor') {
    if (d.property_id != null) return propertyPath(d.property_id);
  } else if (d.source_kind === 'share') {
    if (d.share_kind === 'collection' && d.collection_id != null) {
      return ROUTES.collectionDetail.build({ id: d.collection_id });
    }
    if (d.share_kind === 'dashboard' && d.share_ref) {
      // Browse › Stats loads the dashboard from the URL (stats/DashboardBar).
      return withQuery(ROUTES.browse.build(), { tab: 'stats', dashboard: d.share_ref });
    }
    // The shared board is then picked in the Pipeline tab's switcher.
    if (d.share_kind === 'pipeline') return ROUTES.pipeline.build();
  }
  return ROUTES.notifications.build();
}
