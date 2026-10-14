/* The single source of truth for "which caches a tag write must refresh" — the
 * tag analogue of lib/collectionCache, for the same reason: the key list was
 * hand-typed per writer and drifted. The rename/recolour/delete popover
 * invalidated a `listing-tags` key that stopped existing when migration 202
 * re-keyed curation to properties, so a deleted tag stayed on the property page
 * until the query went stale on its own. Import and call this instead. */

import type { QueryClient } from '@tanstack/react-query';

import { curationKeys } from '@/lib/queries';

/** Re-read tags after ANY tag write: an attach / detach moves the member map,
 * a create / rename / recolour / delete changes the index (a delete cascades
 * its memberships away too, migration 202), and a merge re-points
 * `property_tags` onto the survivor (rule #18). */
export function revalidateTags(qc: QueryClient): void {
  qc.invalidateQueries({ queryKey: curationKeys.propertyTagMembers });
  qc.invalidateQueries({ queryKey: curationKeys.tags });
}
