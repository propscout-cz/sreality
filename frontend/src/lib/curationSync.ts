/* THE table→read map of curation (docs/design/curation-sync.md § 3.5). Pure.
 *
 * One row per curation TABLE: its family (the unit the curation clock counts),
 * the reads that select it — or a view or policy that joins it — and the lens of
 * Browse it moves. A key sits under a table iff its read selects that table, so a
 * local write names the tables it writes (`revalidateTables`) and a remote change
 * re-reads its family's tables: both take their keys from here, and the two
 * cannot drift. lib/revalidate pays every read named here.
 *
 * `reads` follow the writer's identity: the caller's own keys, or under the
 * owner's acting segment (lib/acting) for a write made as the owner. `ownReads`
 * exist for the caller's own identity only — no acting reader holds them, and
 * Browse never acts (lib/curationSettle `browseReads`). */

import type { QueryClient, QueryKey } from '@tanstack/react-query';
import { hashKey } from '@tanstack/react-query';

import { actingKey } from '@/lib/acting';
import { lensTargets, type CurationLens } from '@/lib/curationSettle';
import { curationKeys, dismissalKeys, pipelineKeys, pipelineShareKeys } from '@/lib/queries';
import { revalidate, type Hold, type ReadMode, type Revalidation } from '@/lib/revalidate';

export const CURATION_FAMILIES = ['board', 'dismissals', 'collections', 'tags', 'notes', 'links', 'shares'] as const;
export type CurationFamily = (typeof CURATION_FAMILIES)[number];

interface SyncEntry {
  family: CurationFamily;
  reads: readonly QueryKey[];
  ownReads?: readonly QueryKey[];
  lens?: CurationLens;
}

/* Where each key's read comes from (migrations 202, 316, 536, 610, 613, 641, 660):
 * property_pipeline_public joins pipeline_stages and pipeline_board_public reads
 * it; GET /collections counts collection_properties and lists collection_shares;
 * GET /tags counts property_tags; the collection_properties, property_notes and
 * property_attachments shared-read policies join collection_shares (and the
 * notes' one collection_properties), invisible while acting (`not acting()`). */
export const SYNC_TABLES = {
  property_pipeline: { family: 'board', reads: [pipelineKeys.board, pipelineKeys.members], lens: 'pipeline' },
  pipeline_stages: {
    family: 'board',
    reads: [pipelineKeys.stages, pipelineKeys.board, pipelineKeys.members],
    lens: 'pipeline',
  },
  property_dismissals: { family: 'dismissals', reads: [dismissalKeys.all], lens: 'dismissed' },
  collections: {
    family: 'collections',
    reads: [curationKeys.collections],
    ownReads: [curationKeys.anyCollection],
    lens: 'collections',
  },
  collection_properties: {
    family: 'collections',
    reads: [curationKeys.propertyCollectionMembers, curationKeys.collections],
    ownReads: [curationKeys.anyCollection, curationKeys.allPropertyNotes, curationKeys.noteCounts],
    lens: 'collections',
  },
  tags: { family: 'tags', reads: [curationKeys.tags], lens: 'tags' },
  property_tags: { family: 'tags', reads: [curationKeys.propertyTagMembers, curationKeys.tags], lens: 'tags' },
  property_notes: { family: 'notes', reads: [curationKeys.allPropertyNotes], ownReads: [curationKeys.noteCounts] },
  property_attachments: { family: 'notes', reads: [curationKeys.allPropertyNotes] },
  property_artifact_links: { family: 'links', reads: [curationKeys.artifactLinks] },
  pipeline_shares: { family: 'shares', reads: [], ownReads: [pipelineShareKeys.all] },
  collection_shares: {
    family: 'shares',
    reads: [],
    ownReads: [
      curationKeys.collections,
      curationKeys.anyCollection,
      curationKeys.propertyCollectionMembers,
      curationKeys.allPropertyNotes,
      curationKeys.noteCounts,
    ],
  },
} as const satisfies Record<string, SyncEntry>;

export type SyncTable = keyof typeof SYNC_TABLES;

const entry = (table: SyncTable): SyncEntry => SYNC_TABLES[table];

const TABLES = Object.keys(SYNC_TABLES) as SyncTable[];

/** Ruling Q3(a), 2026-10-31: a REMOTE lens change leaves every Browse, Feed and
 * collection list, count, map and stat as it is until the next mount; only the
 * per-card marks (the member reads) follow at once. A local write keeps ruling 9's
 * per-property judge (lib/curationSettle). */
export const LENS_REREAD = { lists: 'mount', aggregates: 'mount' } as const satisfies Record<string, ReadMode>;

/* Own reads that are themselves a list — an open collection page's rows: a
 * change made elsewhere re-reads them as LENS_REREAD.lists says (ruling Q3(a)). */
const LIST_READS = new Set([hashKey(curationKeys.anyCollection)]);

/** The roots every synced read sits under; none is lib/hydration's (hydration.test.ts). */
export const SYNC_ROOTS: readonly QueryKey[] = [['pipeline'], ['curation'], ['dismissals'], ['pipeline-shares'], ['acting']];

/** No reconnect refetch under SYNC_ROOTS, so a hidden tab stays at zero requests.
 * main.tsx calls it only beside the curation clock's read, which then is the one
 * reconnect path for curation; before that read exists it stays uncalled. */
export function applySyncQueryDefaults(qc: QueryClient): void {
  for (const root of SYNC_ROOTS) qc.setQueryDefaults(root, { refetchOnReconnect: false });
}

/** The reads a write to `tables` moves, deduplicated: the caller's own, or the
 * owner's under `actAs` (never an own-only read then). */
export function tableReads(tables: readonly SyncTable[], actAs: string | null): QueryKey[] {
  const out = new Map<string, QueryKey>();
  const add = (key: QueryKey) => out.set(hashKey(key), key);
  for (const table of tables) {
    const e = entry(table);
    for (const key of e.reads) add(actingKey(actAs, key));
    if (!actAs) for (const key of e.ownReads ?? []) add(key);
  }
  return [...out.values()];
}

/** A local write to `tables` landed (or failed): re-read what they feed. */
export function revalidateTables(
  qc: QueryClient,
  tables: readonly SyncTable[],
  actAs: string | null = null,
  opts: { release?: Hold } = {},
): Promise<void> {
  return revalidate(qc, tableReads(tables, actAs), opts);
}

/** What a change of `family` made elsewhere re-reads here: for an `owner` I follow
 * (a pipeline shared with me) that owner's acting reads only — never an own read
 * and never a Browse target; for my own accounts every read of the family and the
 * Browse cohorts its lenses move, lists among them as LENS_REREAD rules. */
export function familyTargets(qc: QueryClient, family: CurationFamily, owner: string | null): Revalidation[] {
  const tables = TABLES.filter((t) => entry(t).family === family);
  if (owner != null) return tableReads(tables, owner).map((queryKey) => ({ queryKey }));
  const reads: Revalidation[] = tableReads(tables, null).map((queryKey) =>
    LIST_READS.has(hashKey(queryKey)) ? { queryKey, mode: LENS_REREAD.lists } : { queryKey },
  );
  const lenses = new Set(tables.flatMap((t) => entry(t).lens ?? []));
  for (const lens of lenses) reads.push(...lensTargets(qc, lens, LENS_REREAD));
  return reads;
}

/** One row of the curation clock (curation_clock_public, W3). */
export interface ClockRow {
  account_id: string;
  family: CurationFamily;
  counter: number;
  mine: boolean;
  age_ms: number;
}

export const clockKey = (row: Pick<ClockRow, 'account_id' | 'family'>): string => `${row.account_id}:${row.family}`;

/** The rows of `next` that moved since `prev`: a changed counter, a row that
 * appeared, and — as the row `prev` held — one that disappeared (a revoke or a
 * downgrade). The first answer (`prev` null) has nothing to compare with: a row
 * counts iff it changed within `firstWindowMs` (the time since the window
 * started reading, plus a margin — Decision 14b). */
export function clockChanges(
  prev: ReadonlyMap<string, ClockRow> | null,
  next: readonly ClockRow[],
  firstWindowMs: number,
): ClockRow[] {
  if (prev == null) return next.filter((row) => row.age_ms <= firstWindowMs);
  const seen = new Set<string>();
  const out: ClockRow[] = [];
  for (const row of next) {
    const key = clockKey(row);
    seen.add(key);
    if (prev.get(key)?.counter !== row.counter) out.push(row);
  }
  for (const [key, row] of prev) if (!seen.has(key)) out.push(row);
  return out;
}

/** Members of the four curation key factories no table feeds, each with why;
 * the key-coverage census (curationSync.test.ts) fails on a member in neither. */
export const NOT_SYNCED: Readonly<Record<string, string>> = {};
