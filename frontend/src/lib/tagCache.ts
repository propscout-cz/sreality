/* A landed tag attach / detach. Which reads any other tag write moves is
 * lib/curationSync's map (`revalidateTables(qc, ['tags'], actAs)`; a delete
 * writes `deletedTables('tags')`, its cascade included). The hand-typed list that
 * lived here once re-read a `listing-tags` key migration 202 had retired, so a
 * deleted tag stayed on the property page until the query went stale. */

import type { QueryClient } from '@tanstack/react-query';

import { settleMemberWrite } from '@/lib/curationSettle';
import { tableReads } from '@/lib/curationSync';

/** One tag attached to / detached from one property, landed: settled in the
 * Browse reads (lib/curationSettle, ruling 9) and the member map, with the tag
 * reads (`property_tags`) owed in the same hold. `actAs`: the owner's entries
 * (lib/acting), never the caller's. */
export const settleTagWrite = (
  qc: QueryClient,
  property_id: number,
  tag_id: number,
  member: boolean,
  actAs: string | null = null,
): void =>
  settleMemberWrite(qc, 'tags', property_id, tag_id, member, { actAs, reads: tableReads(['property_tags'], actAs) });
