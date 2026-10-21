/* The curation settle (ruling 9): after a write moves one property in or out of
 * a lens, every cached Browse read that engages the lens is judged again for
 * that property — a row it no longer belongs to drops IN PLACE (no page is
 * re-read), the exact count drops one, map and stats re-read. */

import { QueryClient, QueryClientProvider, type InfiniteData, type QueryKey } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { browseKeys } from './browseKeys';
import { browseReads, settleMemberWrite, settlePatches, settleRereads, type CurationLens } from './curationSettle';
import { DEFAULT_FILTERS, type ListingFilters } from './filters';
import { curationKeys, DEFAULT_SORT, dismissalKeys, pipelineKeys, type PipelineMembers } from './queries';
import type { SetScope } from './setScope';
import { useInfiniteList } from './useInfiniteList';

type Page = { rows: { property_id: number }[]; fetched?: number };
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

/* A Browse cohort with its cards and count cached, as Browse leaves them. */
function seed(qc: QueryClient, f: ListingFilters, cards: number[][], count = 50) {
  qc.setQueryData(browseKeys.cards(f, DEFAULT_SORT), pages(...cards));
  qc.setQueryData(browseKeys.count(f), count);
  qc.setQueryData(browseKeys.map(f, 200), { points: [] });
  qc.setQueryData(browseKeys.stats(f, { rows: [], cols: [], measure: 'ppm2' }), { cells: [] });
}

/* An optimistic write as useOptimisticWrite runs the halves: patches built,
 * the write painted, the patches applied, then the re-reads. */
function settle(qc: QueryClient, lens: CurationLens, paint: () => void) {
  const patches = settlePatches(qc, P, lens);
  paint();
  for (const p of patches) qc.setQueriesData(p.filters, p.update);
  for (const queryKey of settleRereads(qc, P, lens)) void qc.invalidateQueries({ queryKey });
}

const invalidated = (spy: { mock: { calls: ReadonlyArray<ReadonlyArray<unknown>> } }) =>
  spy.mock.calls.map(([arg]) => (arg as { queryKey: QueryKey }).queryKey);

describe('the settle', () => {
  it('drops a funnelled property from every loaded page of a "Mimo pipeline" cohort, in place', () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ set: 'out' }) });
    seed(qc, f, [[1, 2], [P, 3], [4]]);
    qc.setQueryData(pipelineKeys.members, members([]));
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    settle(qc, 'pipeline', () => qc.setQueryData(pipelineKeys.members, members([[P, 5]])));

    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[1, 2], [3], [4]]);
    expect(qc.getQueryData(browseKeys.count(f))).toBe(49);
    const keys = invalidated(invalidate);
    expect(keys).toContainEqual(['map', f]);
    expect(keys).toContainEqual(['stats', f]);
    expect(keys).toContainEqual(['browse-count', f]);
    // The lists are never re-read for a drop.
    expect(keys.some((k) => k[0] === 'cards')).toBe(false);
  });

  it('leaves a cohort the property still belongs to untouched', () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ set: 'in' }) });
    seed(qc, f, [[1, P]]);
    qc.setQueryData(pipelineKeys.members, members([[P, 5]]));
    const before = qc.getQueryData(browseKeys.cards(f, DEFAULT_SORT));
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    settle(qc, 'pipeline', () => qc.setQueryData(pipelineKeys.members, members([[P, 6]]))); // a move

    expect(qc.getQueryData(browseKeys.cards(f, DEFAULT_SORT))).toBe(before);
    expect(qc.getQueryData(browseKeys.count(f))).toBe(50);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('judges only the cohorts that engage the lens the write moved', () => {
    const qc = new QueryClient();
    const unscoped = filters();
    const byTag = filters({ tags: scope({ in: [9] }) });
    seed(qc, unscoped, [[P]]);
    seed(qc, byTag, [[P]]);
    qc.setQueryData(curationKeys.propertyTagMembers, new Map<number, number[]>()); // P carries no tag 9 (stale page)
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    settle(qc, 'pipeline', () => qc.setQueryData(pipelineKeys.members, members([[P, 5]])));

    expect(rowsOf(qc, browseKeys.cards(unscoped, DEFAULT_SORT))).toEqual([[P]]);
    expect(rowsOf(qc, browseKeys.cards(byTag, DEFAULT_SORT))).toEqual([[P]]);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('a stage exclusion drops the card moved into that stage', () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ out: [7] }) });
    seed(qc, f, [[P, 1]]);
    qc.setQueryData(pipelineKeys.members, members([[P, 2]]));
    settle(qc, 'pipeline', () => qc.setQueryData(pipelineKeys.members, members([[P, 7]])));
    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[1]]);
  });

  it('tags keep AND: detaching one of the required tags drops the card', async () => {
    const qc = new QueryClient();
    const f = filters({ tags: scope({ in: [1, 2] }) });
    seed(qc, f, [[P, 3]]);
    qc.setQueryData(curationKeys.propertyTagMembers, new Map([[P, [1, 2]]]));

    await settleMemberWrite(qc, 'tags', P, 2, false);

    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[3]]);
    expect(qc.getQueryData(browseKeys.count(f))).toBe(49);
  });

  it('"V žádné kolekci": a save drops the card; the member map shows the save', async () => {
    const qc = new QueryClient();
    const f = filters({ collections: scope({ set: 'out' }) });
    seed(qc, f, [[P, 3]]);
    qc.setQueryData(curationKeys.propertyCollectionMembers, new Map<number, number[]>());

    await settleMemberWrite(qc, 'collections', P, 4, true);

    expect(qc.getQueryData(curationKeys.propertyCollectionMembers)).toEqual(new Map([[P, [4]]]));
    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[3]]);
  });

  it('cannot judge without the member map: that cohort re-reads whole, nothing drops', async () => {
    const qc = new QueryClient();
    const f = filters({ collections: scope({ set: 'out' }) });
    seed(qc, f, [[P]]);
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    await settleMemberWrite(qc, 'collections', P, 4, true);

    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[P]]);
    expect(invalidated(invalidate)).toEqual(browseKeys.all.map(([root]) => [root, f]));
  });

  it('a property that newly matches a cohort it is not loaded in re-reads that cohort', async () => {
    const qc = new QueryClient();
    const f = filters({ collections: scope({ in: [4] }) });
    seed(qc, f, [[1, 2]]);
    qc.setQueryData(curationKeys.propertyCollectionMembers, new Map<number, number[]>());
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    await settleMemberWrite(qc, 'collections', P, 4, true);

    expect(invalidated(invalidate)).toEqual(browseKeys.all.map(([root]) => [root, f]));
  });

  it('a stale member map never drops a row the server shows: that cohort re-reads whole', async () => {
    const qc = new QueryClient();
    const f = filters({ tags: scope({ in: [1, 2] }) });
    seed(qc, f, [[P, 3]]);
    // The server shows P (tags 1 AND 2); this map missed tag 2, attached elsewhere.
    qc.setQueryData(curationKeys.propertyTagMembers, new Map([[P, [1]]]));
    const invalidate = vi.spyOn(qc, 'invalidateQueries');

    await settleMemberWrite(qc, 'tags', P, 3, true);

    expect(rowsOf(qc, browseKeys.cards(f, DEFAULT_SORT))).toEqual([[P, 3]]);
    expect(qc.getQueryData(browseKeys.count(f))).toBe(50);
    expect(invalidated(invalidate)).toEqual(browseKeys.all.map(([root]) => [root, f]));
  });

  it('a page that lost a row keeps the length it was read with, so the list pages on', async () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ set: 'out' }) });
    qc.setQueryData(pipelineKeys.members, members([]));
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);
    const { result } = renderHook(
      () =>
        useInfiniteList<{ property_id: number }>({
          queryKey: browseKeys.cards(f, DEFAULT_SORT),
          queryFn: async () => ({ rows: [{ property_id: P }, { property_id: 1 }], nextCursor: 'next' }),
          pageSize: 2,
          getRowId: (r) => r.property_id,
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    expect(result.current.hasNextPage).toBe(true);

    act(() => settle(qc, 'pipeline', () => qc.setQueryData(pipelineKeys.members, members([[P, 5]]))));

    await waitFor(() => expect(result.current.rows).toEqual([{ property_id: 1 }]));
    expect(result.current.hasNextPage).toBe(true);
  });
});

describe('the dismissal lens', () => {
  it('drops a hidden property from the lists that hide dismissed ones only', () => {
    const qc = new QueryClient();
    const hiding = filters();
    const showing = filters({ showDismissed: true });
    seed(qc, hiding, [[P, 1]]);
    seed(qc, showing, [[P, 1]]);
    settle(qc, 'dismissed', () => qc.setQueryData(dismissalKeys.state(P), true));

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

  it('a write made as a pipeline\'s owner settles and re-reads no Browse read — Browse never acts', () => {
    const qc = new QueryClient();
    const f = filters({ pipeline: scope({ set: 'out' }) });
    seed(qc, f, [[P, 1]]);
    qc.setQueryData(pipelineKeys.members, members([]));
    expect(settlePatches(qc, P, 'pipeline')).not.toEqual([]);
    expect(settlePatches(qc, P, 'pipeline', 'owner-1')).toEqual([]);
    expect(settleRereads(qc, P, 'pipeline', false, 'owner-1')).toEqual([]);
    expect(settleRereads(qc, P, 'pipeline', true, 'owner-1')).toEqual([]);
    expect(browseReads('owner-1', browseKeys.all)).toEqual([]);
    expect(browseReads(null, browseKeys.all)).toEqual([...browseKeys.all]);
  });
});

describe('the member map', () => {
  it('a landed write adds or removes one element, and leaves an unloaded map unloaded', async () => {
    const qc = new QueryClient();
    await settleMemberWrite(qc, 'tags', P, 1, true);
    expect(qc.getQueryData(curationKeys.propertyTagMembers)).toBeUndefined();

    qc.setQueryData(curationKeys.propertyTagMembers, new Map([[P, [1]]]));
    await settleMemberWrite(qc, 'tags', P, 2, true);
    await settleMemberWrite(qc, 'tags', P, 2, true);
    expect(qc.getQueryData(curationKeys.propertyTagMembers)).toEqual(new Map([[P, [1, 2]]]));
    await settleMemberWrite(qc, 'tags', P, 1, false);
    await settleMemberWrite(qc, 'tags', P, 2, false);
    expect(qc.getQueryData(curationKeys.propertyTagMembers)).toEqual(new Map());
  });
});
