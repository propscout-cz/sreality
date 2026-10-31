/* lib/curationSync — the table→read map. What a write to a table re-reads, for
 * the caller's own identity and for an owner it acts as; what a change of a
 * family made elsewhere re-reads; and the census that keeps every curation key
 * under some table. */

import { describe, expect, it, vi } from 'vitest';
import { hashKey, InfiniteQueryObserver, QueryClient, QueryObserver, type QueryKey } from '@tanstack/react-query';

import { actingKey } from './acting';
import { browseKeys } from './browseKeys';
import {
  clockChanges,
  clockKey,
  CURATION_FAMILIES,
  deletedTables,
  familyTargets,
  LENS_REREAD,
  NOT_SYNCED,
  SYNC_ROOTS,
  SYNC_TABLES,
  tableReads,
  applySyncQueryDefaults,
  type ClockRow,
  type SyncTable,
} from './curationSync';
import { DEFAULT_FILTERS } from './filters';
import { curationKeys, DEFAULT_SORT, dismissalKeys, pipelineKeys, pipelineShareKeys } from './queries';
import { isOwed, revalidate } from './revalidate';

const TABLES = Object.keys(SYNC_TABLES) as SyncTable[];
const OWNER = 'owner-1';
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const hashes = (keys: readonly QueryKey[]) => new Set(keys.map((k) => hashKey(k)));
const startsWith = (key: QueryKey, prefix: QueryKey) =>
  prefix.every((p, i) => JSON.stringify(p) === JSON.stringify(key[i]));
const allKeys = TABLES.flatMap((t) => {
  const e = SYNC_TABLES[t] as { reads: readonly QueryKey[]; ownReads?: readonly QueryKey[] };
  return [...e.reads, ...(e.ownReads ?? [])];
});

describe('tableReads', () => {
  it.each(TABLES)('%s: its reads and own reads as myself, only its reads under the owner as them', (table) => {
    const e = SYNC_TABLES[table] as { reads: readonly QueryKey[]; ownReads?: readonly QueryKey[] };
    expect(hashes(tableReads([table], null))).toEqual(hashes([...e.reads, ...(e.ownReads ?? [])]));
    const theirs = tableReads([table], OWNER);
    expect(hashes(theirs)).toEqual(hashes(e.reads.map((k) => actingKey(OWNER, k))));
    for (const key of theirs) expect(key.slice(0, 2)).toEqual(['acting', OWNER]);
  });

  it('a card write (property_pipeline + property_dismissals) re-reads members, board and dismissals', () => {
    expect(hashes(tableReads(['property_pipeline', 'property_dismissals'], null))).toEqual(
      hashes([pipelineKeys.members, pipelineKeys.board, dismissalKeys.all]),
    );
  });

  /* D2: every funnel badge renders the stage's label, colour and code from the
   * members map (property_pipeline_public joins the stages). */
  it('a stage edit reaches the members map', () => {
    expect(hashes(tableReads(['pipeline_stages'], null))).toEqual(
      hashes([pipelineKeys.stages, pipelineKeys.board, pipelineKeys.members]),
    );
    expect(tableReads(['pipeline_stages'], OWNER)).toContainEqual(actingKey(OWNER, pipelineKeys.members));
  });

  it('deduplicates across tables', () => {
    const keys = tableReads(['property_notes', 'property_attachments', 'collection_properties'], null);
    expect(keys.length).toBe(hashes(keys).size);
  });

  it('never names a Browse read: Browse is the settle\'s and LENS_REREAD\'s', () => {
    const browse = browseKeys.all.map(([root]) => root as unknown);
    for (const key of tableReads(TABLES, null)) expect(browse).not.toContain(key[0]);
  });
});

describe('deletedTables', () => {
  it('a collection delete writes its memberships and shares, a tag delete its memberships', () => {
    expect(deletedTables('collections')).toEqual(['collections', 'collection_properties', 'collection_shares']);
    expect(deletedTables('tags')).toEqual(['tags', 'property_tags']);
    expect(deletedTables('property_notes')).toEqual(['property_notes']);
  });

  it('the shares a collection delete takes add no read its memberships do not', () => {
    expect(hashes(tableReads(deletedTables('collections'), null))).toEqual(
      hashes(tableReads(['collections', 'collection_properties'], null)),
    );
  });
});

describe('familyTargets', () => {
  const f = { ...DEFAULT_FILTERS, pipeline: { set: 'out' as const, in: [], out: [] } };
  const unscoped = DEFAULT_FILTERS;

  function cached() {
    const qc = new QueryClient();
    for (const filters of [f, { ...unscoped, showDismissed: true }]) {
      qc.setQueryData(browseKeys.cards(filters, DEFAULT_SORT), { pages: [], pageParams: [] });
      qc.setQueryData(browseKeys.count(filters), 1);
    }
    return qc;
  }

  it('an owner\'s change re-reads that owner\'s acting keys only — no own read, no Browse', () => {
    const qc = cached();
    for (const family of CURATION_FAMILIES) {
      for (const target of familyTargets(qc, family, OWNER)) {
        expect(target.queryKey.slice(0, 2)).toEqual(['acting', OWNER]);
        expect(target.mode ?? 'now').toBe('now');
      }
    }
    expect(familyTargets(qc, 'shares', OWNER)).toEqual([]);
  });

  it('my own change re-reads the family\'s tables, and the Browse cohorts its lens moves as LENS_REREAD rules', () => {
    const qc = cached();
    const targets = familyTargets(qc, 'board', null);
    const reads = targets.filter((t) => t.mode == null).map((t) => t.queryKey);
    expect(hashes(reads)).toEqual(hashes(tableReads(['property_pipeline', 'pipeline_stages'], null)));
    const browse = targets.filter((t) => t.mode != null);
    expect(browse).toContainEqual({ queryKey: ['cards', f], mode: LENS_REREAD.lists });
    for (const [root] of browseKeys.aggregates) {
      expect(browse).toContainEqual({ queryKey: [root, f], mode: LENS_REREAD.aggregates });
    }
    // A cohort that does not engage the pipeline lens is left alone.
    expect(browse.some((t) => t.queryKey[1] !== f)).toBe(false);
  });

  it('a dismissal elsewhere reaches the cohorts that hide dismissed properties', () => {
    const qc = new QueryClient();
    qc.setQueryData(browseKeys.count(unscoped), 1);
    qc.setQueryData(browseKeys.count({ ...unscoped, showDismissed: true }), 1);
    const browse = familyTargets(qc, 'dismissals', null).filter((t) => t.mode != null);
    expect(browse.length).toBeGreaterThan(0);
    expect(browse.every((t) => t.queryKey[1] === unscoped)).toBe(true);
  });

  it('a collections change elsewhere leaves an open collection page to its next mount; the marks follow at once', () => {
    const targets = familyTargets(new QueryClient(), 'collections', null);
    expect(targets).toContainEqual({ queryKey: curationKeys.anyCollection, mode: LENS_REREAD.lists });
    expect(targets).toContainEqual({ queryKey: curationKeys.propertyCollectionMembers });
    expect(targets).toContainEqual({ queryKey: curationKeys.collections });
  });

  /* Ruling Q3(a), 2026-10-31: a remote lens change leaves lists, counts, map and
   * stats until navigation; only the marks follow at once. */
  it('LENS_REREAD leaves every Browse read to its next mount', () => {
    expect(LENS_REREAD).toEqual({ lists: 'mount', aggregates: 'mount' });
  });

  it('a remote lens change reads nothing of a mounted cohort until it mounts again; the marks follow at once', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const cards = browseKeys.cards(f, DEFAULT_SORT);
    const count = browseKeys.count(f);
    qc.setQueryData(cards, { pages: [{ rows: [] }], pageParams: [0] });
    qc.setQueryData(count, 1);
    qc.setQueryData(pipelineKeys.members, new Map());
    const list = vi.fn(async () => ({ rows: [] }));
    const total = vi.fn(async () => 1);
    const marks = vi.fn(async () => new Map());
    const mountCohort = () => [
      new InfiniteQueryObserver(qc, {
        queryKey: cards,
        queryFn: list,
        initialPageParam: 0,
        getNextPageParam: () => undefined,
        staleTime: Infinity,
      }).subscribe(() => {}),
      new QueryObserver(qc, { queryKey: count, queryFn: total, staleTime: Infinity }).subscribe(() => {}),
    ];
    const cohort = mountCohort();
    new QueryObserver(qc, { queryKey: pipelineKeys.members, queryFn: marks, staleTime: Infinity }).subscribe(() => {});

    void revalidate(qc, familyTargets(qc, 'board', null));
    await flush();
    expect(marks).toHaveBeenCalledTimes(1);
    expect(list).not.toHaveBeenCalled();
    expect(total).not.toHaveBeenCalled();
    expect(isOwed(qc, cards) && isOwed(qc, count)).toBe(true);

    for (const unmount of cohort) unmount();
    mountCohort();
    await flush();
    expect(list).toHaveBeenCalledTimes(1);
    expect(total).toHaveBeenCalledTimes(1);
    expect(isOwed(qc, cards) || isOwed(qc, count)).toBe(false);
  });

  it('every family has a table, and every table a family', () => {
    for (const family of CURATION_FAMILIES) {
      expect(TABLES.some((t) => SYNC_TABLES[t].family === family), family).toBe(true);
    }
    for (const t of TABLES) expect(CURATION_FAMILIES).toContain(SYNC_TABLES[t].family);
  });
});

describe('clockChanges', () => {
  const row = (family: ClockRow['family'], counter: number, over: Partial<ClockRow> = {}): ClockRow => ({
    account_id: 'me', family, counter, mine: true, age_ms: 60_000, ...over,
  });
  const prev = (rows: ClockRow[]) => new Map(rows.map((r) => [clockKey(r), r]));

  it('names a changed counter, an appeared row and a disappeared one, never an unchanged one', () => {
    const before = prev([row('board', 1), row('tags', 4), row('board', 2, { account_id: OWNER, mine: false })]);
    const next = [row('board', 2), row('tags', 4), row('notes', 1)];
    expect(clockChanges(before, next, 0)).toEqual([
      row('board', 2),
      row('notes', 1),
      row('board', 2, { account_id: OWNER, mine: false }),
    ]);
  });

  it('on the first answer counts a row iff it changed within the window', () => {
    const next = [row('board', 9, { age_ms: 4_000 }), row('tags', 9, { age_ms: 50_001 }), row('notes', 9, { age_ms: 50_000 })];
    expect(clockChanges(null, next, 50_000).map((r) => r.family)).toEqual(['board', 'notes']);
  });
});

/* Every member of the four curation key factories sits under a key some table
 * feeds, or is listed in NOT_SYNCED with its reason: a new read cannot be added
 * outside the map without this failing. */
describe('the key census', () => {
  const factories = { curationKeys, pipelineKeys, dismissalKeys, pipelineShareKeys };
  const members = Object.entries(factories).flatMap(([factory, keys]) =>
    Object.entries(keys).map(([name, k]) => ({
      name: `${factory}.${name}`,
      key: (typeof k === 'function' ? (k as (id: number) => QueryKey)(1) : k) as QueryKey,
    })),
  );

  it.each(members)('$name is fed by a table or listed in NOT_SYNCED', ({ name, key }) => {
    const covered = allKeys.some((prefix) => startsWith(key, prefix));
    expect(covered || name in NOT_SYNCED, `${name} ${JSON.stringify(key)}`).toBe(true);
  });

  it('NOT_SYNCED names real members, each with its reason', () => {
    const names = new Set(members.map((m) => m.name));
    for (const [name, reason] of Object.entries(NOT_SYNCED)) {
      expect(names.has(name), name).toBe(true);
      expect(reason.trim()).not.toBe('');
    }
  });

  /* A write that names keys instead of tables names keys inside a root of a
   * table it writes, so its own re-read and a remote one cannot drift. */
  it.each([
    { write: 'useDismissal (dismiss, restore)', tables: ['property_dismissals'], keys: [dismissalKeys.state(1), dismissalKeys.count] },
    { write: 'CurationBlock notes key', tables: ['property_notes'], keys: [curationKeys.propertyNotes(1), curationKeys.viewings] },
    { write: 'PropertyPhotos files key', tables: ['property_attachments'], keys: [curationKeys.propertyFiles(1)] },
  ] as { write: string; tables: SyncTable[]; keys: QueryKey[] }[])('$write is covered by its tables', ({ tables, keys }) => {
    for (const actAs of [null, OWNER]) {
      const roots = tableReads(tables, actAs);
      for (const key of keys) {
        const k = actingKey(actAs, key);
        expect(roots.some((r) => startsWith(k, r)), JSON.stringify(k)).toBe(true);
      }
    }
  });

  it('every synced key, own or acting, sits under SYNC_ROOTS', () => {
    for (const key of [...tableReads(TABLES, null), ...tableReads(TABLES, OWNER)]) {
      expect(SYNC_ROOTS.some((root) => startsWith(key, root)), JSON.stringify(key)).toBe(true);
    }
  });
});

describe('applySyncQueryDefaults', () => {
  it('turns the reconnect refetch off under SYNC_ROOTS only', () => {
    const qc = new QueryClient();
    applySyncQueryDefaults(qc);
    expect(qc.getQueryDefaults(pipelineKeys.board).refetchOnReconnect).toBe(false);
    expect(qc.getQueryDefaults(actingKey(OWNER, curationKeys.tags)).refetchOnReconnect).toBe(false);
    expect(qc.getQueryDefaults(pipelineShareKeys.all).refetchOnReconnect).toBe(false);
    expect(qc.getQueryDefaults(browseKeys.count(DEFAULT_FILTERS)).refetchOnReconnect).toBeUndefined();
  });
});
