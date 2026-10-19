/* The curation settle (ruling 9): after a write moves one property in or out of
 * a lens, every cached Browse read that engages the lens is judged again for
 * that property — a row it no longer belongs to drops IN PLACE (no page is
 * re-read), the exact count drops one, map and stats re-read. */

import { QueryClient, type InfiniteData, type QueryKey } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { browseKeys } from './browseKeys';
import {
  markMember,
  settleCurationWrite,
  settlePatches,
  settleRereads,
} from './curationSettle';
import { DEFAULT_FILTERS, type ListingFilters } from './filters';
import { curationKeys, DEFAULT_SORT, dismissalKeys, pipelineKeys, type PipelineMembers } from './queries';
import type { SetScope } from './setScope';

type Page = { rows: { property_id: number }[] };
const P = 42;

const scope = (s: Partial<SetScope>): SetScope => ({ set: null, in: [], out: [], ...s });
const filters = (over: Partial<ListingFilters> = {}): ListingFilters => ({ ...DEFAULT_FILTERS, ...over });
const pages = (...ids: number[][]): InfiniteData<Page> => ({
  pages: ids.map((rows) => ({ rows: rows.map((property_id) => ({ property_id })) })),
  pageParams: ids.map((_, i) => i),
});
const rowsOf = (qc: QueryClient, key: QueryKey) =>
  qc.getQueryData<InfiniteData<Page>>(key)!.pages.map((p) => p.rows.map((r) => r.property_id));

const members = (entries: Array<[number, number]>): PipelineMembers =>
  new Map(entries.map(([property_id, stage_id]) => [property_id, {
    property_id, stage_id, stage_label: `${stage_id}`, stage_color: null,
    stage_code: null, stage_position: stage_id, is_terminal: false,
  }]));

/* A Browse cohort with its cards, table and count cached, as Browse leaves them. */
function seed(qc: QueryClient, f: ListingFilters, cards: number[][], count = 50) {
  qc.setQueryData(browseKeys.cards(f, DEFAULT_SORT), pages(...cards));
  qc.setQueryData(browseKeys.table(f, DEFAULT_SORT), pages(cards.flat()));
  qc.setQueryData(browseKeys.count(f), count);
  qc.setQueryData(browseKeys.map(f, 200), { points: [] });
  qc.setQueryData(browseKeys.stats(f, { rows: [], cols: [], measure: 'ppm2' }), { cells: [] });
}

const invalidated = (spy: { mock: { calls: ReadonlyArray<ReadonlyArray<unknown>> } }) =>
  spy.mock.calls.map(([arg]) => (arg as { queryKey: QueryKey }).queryKey);

describe('settleCurationWrite', () => {
  it('drops a funnelled property from every loaded page of a "Mimo pipeline" cohort, in place', async () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ set: 'out' }) });
    seed(qc, f, [[1, 2], [P, 3], [4]]);
    qc.setQueryData(pipelineKeys.members, members([[P, 5]])); // the add, already painted
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    await settleCurationWrite(qc, P, 'pipeline');

    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[1, 2], [3], [4]]);
    expect(rowsOf(qc, browseKeys.table(f, DEFAULT_SORT))).toEqual([[1, 2, 3, 4]]);
    expect(qc.getQueryData(browseKeys.count(f))).toBe(49);
    const keys = invalidated(invalidate);
    expect(keys).toContainEqual(['map', f]);
    expect(keys).toContainEqual(['stats', f]);
    expect(keys).toContainEqual(['browse-count', f]);
    // The lists are never re-read for a drop.
    expect(keys.some((k) => k[0] === 'cards' || k[0] === 'table')).toBe(false);
  });

  it('leaves a cohort the property still belongs to untouched', async () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ set: 'in' }) });
    seed(qc, f, [[1, P]]);
    qc.setQueryData(pipelineKeys.members, members([[P, 6]])); // a move between stages
    const before = qc.getQueryData(browseKeys.cards(f, DEFAULT_SORT));
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    await settleCurationWrite(qc, P, 'pipeline');

    expect(qc.getQueryData(browseKeys.cards(f, DEFAULT_SORT))).toBe(before);
    expect(qc.getQueryData(browseKeys.count(f))).toBe(50);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('judges only the cohorts that engage the lens the write moved', async () => {
    const qc = new QueryClient();
    const unscoped = filters();
    const byTag = filters({ tags: scope({ in: [9] }) });
    seed(qc, unscoped, [[P]]);
    seed(qc, byTag, [[P]]);
    qc.setQueryData(pipelineKeys.members, members([[P, 5]]));
    qc.setQueryData(curationKeys.propertyTagMembers, new Map<number, number[]>()); // P carries no tag 9 (stale page)
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    await settleCurationWrite(qc, P, 'pipeline');

    expect(rowsOf(qc, browseKeys.cards(unscoped, DEFAULT_SORT))).toEqual([[P]]);
    expect(rowsOf(qc, browseKeys.cards(byTag, DEFAULT_SORT))).toEqual([[P]]);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('a stage exclusion drops the card moved into that stage', async () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ out: [7] }) });
    seed(qc, f, [[P, 1]]);
    qc.setQueryData(pipelineKeys.members, members([[P, 7]]));
    await settleCurationWrite(qc, P, 'pipeline');
    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[1]]);
  });

  it('tags keep AND: detaching one of the required tags drops the card', async () => {
    const qc = new QueryClient();
    const f = filters({ tags: scope({ in: [1, 2] }) });
    seed(qc, f, [[P, 3]]);
    qc.setQueryData(curationKeys.propertyTagMembers, new Map([[P, [1, 2]]]));

    markMember(qc, 'tags', P, 2, false);
    await settleCurationWrite(qc, P, 'tags');

    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[3]]);
    expect(qc.getQueryData(browseKeys.count(f))).toBe(49);
  });

  it('"V žádné kolekci": a save drops the card; the member map shows the save', async () => {
    const qc = new QueryClient();
    const f = filters({ collections: scope({ set: 'out' }) });
    seed(qc, f, [[P, 3]]);
    qc.setQueryData(curationKeys.propertyCollectionMembers, new Map<number, number[]>());

    markMember(qc, 'collections', P, 4, true);
    await settleCurationWrite(qc, P, 'collections');

    expect(qc.getQueryData(curationKeys.propertyCollectionMembers)).toEqual(new Map([[P, [4]]]));
    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[3]]);
  });

  it('cannot judge without the member map: that cohort re-reads whole, nothing drops', async () => {
    const qc = new QueryClient();
    const f = filters({ collections: scope({ set: 'out' }) });
    seed(qc, f, [[P]]);
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    await settleCurationWrite(qc, P, 'collections');

    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[P]]);
    expect(invalidated(invalidate)).toEqual(browseKeys.all.map(([root]) => [root, f]));
  });

  it('a property that newly matches a cohort it is not loaded in re-reads that cohort', async () => {
    const qc = new QueryClient();
    const f = filters({ collections: scope({ in: [4] }) });
    seed(qc, f, [[1, 2]]);
    qc.setQueryData(curationKeys.propertyCollectionMembers, new Map<number, number[]>());
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    markMember(qc, 'collections', P, 4, true);
    await settleCurationWrite(qc, P, 'collections');

    expect(invalidated(invalidate)).toEqual(browseKeys.all.map(([root]) => [root, f]));
  });
});

describe('the dismissal lens', () => {
  it('drops a hidden property from the lists that hide dismissed ones only', async () => {
    const qc = new QueryClient();
    const hiding = filters();
    const showing = filters({ showDismissed: true });
    seed(qc, hiding, [[P, 1]]);
    seed(qc, showing, [[P, 1]]);
    qc.setQueryData(dismissalKeys.state(P), true);

    await settleCurationWrite(qc, P, 'dismissed');

    expect(rowsOf(qc, browseKeys.cards(hiding, DEFAULT_SORT))).toEqual([[1]]);
    expect(qc.getQueryData(browseKeys.count(hiding))).toBe(49);
    expect(rowsOf(qc, browseKeys.cards(showing, DEFAULT_SORT))).toEqual([[P, 1]]);
    expect(qc.getQueryData(browseKeys.count(showing))).toBe(50);
  });
});

describe('the optimistic halves', () => {
  it('judges when APPLIED, so the patches go after the member-map paint', () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ set: 'out' }) });
    seed(qc, f, [[P, 1]]);
    qc.setQueryData(pipelineKeys.members, members([]));

    const patches = settlePatches(qc, P, 'pipeline'); // built before the paint
    qc.setQueryData(pipelineKeys.members, members([[P, 5]])); // the paint
    for (const p of patches) qc.setQueriesData(p.filters, p.update);

    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[1]]);
    expect(qc.getQueryData(browseKeys.count(f))).toBe(49);
  });

  it('builds patches only for the lists that hold the property', () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ set: 'out' }) });
    seed(qc, f, [[1, 2]]);
    expect(settlePatches(qc, P, 'pipeline')).toEqual([]);
  });

  it('a failed write re-reads every Browse surface', () => {
    const qc = new QueryClient();
    expect(settleRereads(qc, P, 'pipeline', true)).toEqual([...browseKeys.all]);
  });
});

describe('markMember', () => {
  it('adds and removes one unit, and leaves an unloaded map unloaded', () => {
    const qc = new QueryClient();
    markMember(qc, 'tags', P, 1, true);
    expect(qc.getQueryData(curationKeys.propertyTagMembers)).toBeUndefined();

    qc.setQueryData(curationKeys.propertyTagMembers, new Map([[P, [1]]]));
    markMember(qc, 'tags', P, 2, true);
    markMember(qc, 'tags', P, 2, true);
    expect(qc.getQueryData(curationKeys.propertyTagMembers)).toEqual(new Map([[P, [1, 2]]]));
    markMember(qc, 'tags', P, 1, false);
    markMember(qc, 'tags', P, 2, false);
    expect(qc.getQueryData(curationKeys.propertyTagMembers)).toEqual(new Map());
  });
});
