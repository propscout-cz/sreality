/* lib/useListHold — ruling Q3(c) with the pointer hold (2026-11-01): a change
 * made elsewhere moves an open Browse list at once — rows leave and enter, and
 * its count, map and stats re-read — but a list re-read waits while a hand is on
 * the list (the pointer over it, a finger on it) and lands when it leaves. The
 * aggregates are never held; the list's own pages and the user's own clicks
 * pass. Every change made elsewhere arrives as W4's owner delivers it:
 * `revalidate(qc, familyTargets(qc, family, null))`. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { dismissProperty } from '@/lib/api';
import { browseKeys } from '@/lib/browseKeys';
import { familyTargets, type CurationFamily } from '@/lib/curationSync';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import { DEFAULT_SORT, fetchIsDismissed } from '@/lib/queries';
import { isOwed, revalidate } from '@/lib/revalidate';
import type { SetScope } from '@/lib/setScope';
import { useDismissal } from '@/lib/useDismissal';
import { useInfiniteList } from '@/lib/useInfiniteList';

import { useHeld, useListHold } from './useListHold';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  dismissProperty: vi.fn(),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchIsDismissed: vi.fn(),
}));

const PAGE = 2;
const OLDEST_FIRST = { ...DEFAULT_SORT, direction: 'asc' as const };
const inStage = (stage: number): SetScope => ({ set: null, in: [stage], out: [] });

/* The server: four properties, ids in sort order, each at a pipeline stage. */
let server: { dismissed: Set<number>; stage: Map<number, number> };
const cohort = (f: ListingFilters): number[] =>
  [1, 2, 3, 4].filter(
    (id) =>
      (f.showDismissed || !server.dismissed.has(id)) &&
      (f.pipeline == null || f.pipeline.in.includes(server.stage.get(id) ?? -1)),
  );
/* Keyset paging, as the Browse read pages: the rows after the cursor's row. */
const listFn = vi.fn(async (f: ListingFilters, newestFirst: boolean, cursor: number | null) => {
  const ids = newestFirst ? cohort(f) : [...cohort(f)].reverse();
  const after = cursor == null ? ids : ids.slice(ids.indexOf(cursor) + 1);
  const rows = after.slice(0, PAGE).map((property_id) => ({ property_id }));
  return { rows, nextCursor: rows.at(-1)?.property_id };
});
const countFn = vi.fn(async (f: ListingFilters) => cohort(f).length);
const mapFn = vi.fn(async (f: ListingFilters) => cohort(f));
const statsFn = vi.fn(async (f: ListingFilters) => cohort(f).length);

function Row({ id, label }: { id: number; label: string }) {
  const { dismiss } = useDismissal(id);
  return (
    <li>
      <button type="button">{`${label} ${id}`}</button>
      <button type="button" onClick={() => dismiss.mutate()}>{`hide ${id}`}</button>
    </li>
  );
}

/* One Browse cohort as the page reads it: its cards with a hand, and its count,
 * map and stats beside them. */
function Cohort({ f, label }: { f: ListingFilters; label: string }) {
  const [sort, setSort] = useState(DEFAULT_SORT);
  const newestFirst = sort === DEFAULT_SORT;
  const list = useInfiniteList<{ property_id: number }>({
    queryKey: browseKeys.cards(f, sort),
    queryFn: (cursor) => listFn(f, newestFirst, cursor as number | null),
    pageSize: PAGE,
    getRowId: (r) => r.property_id,
  });
  const count = useQuery({ queryKey: browseKeys.count(f), queryFn: () => countFn(f) });
  useQuery({ queryKey: browseKeys.map(f, 100), queryFn: () => mapFn(f) });
  useQuery({ queryKey: browseKeys.statsPrefilters(f), queryFn: () => statsFn(f) });
  const hand = useListHold([browseKeys.cards(f, sort)]);
  return (
    <section>
      <output aria-label={`${label} count`}>{count.data ?? ''}</output>
      <ul aria-label={label} {...hand}>
        {list.rows.map((r) => (
          <Row key={r.property_id} id={r.property_id} label={label} />
        ))}
        <li>
          <button type="button" onClick={list.fetchNextPage}>{`${label} more`}</button>
          <button type="button" onClick={() => setSort(newestFirst ? OLDEST_FIRST : DEFAULT_SORT)}>
            {`${label} order`}
          </button>
        </li>
      </ul>
    </section>
  );
}

function renderCohorts(...cohorts: { f: ListingFilters; label: string }[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  render(
    <QueryClientProvider client={qc}>
      {cohorts.map((c) => (
        <Cohort key={c.label} {...c} />
      ))}
      <button type="button">outside</button>
    </QueryClientProvider>,
  );
  return qc;
}

const list = (label: string) => screen.getByRole('list', { name: label });
const rows = (label: string) =>
  within(list(label))
    .queryAllByRole('button', { name: new RegExp(`^${label} \\d$`) })
    .map((b) => Number(b.textContent?.split(' ')[1]));
const elsewhere = (qc: QueryClient, family: CurationFamily) =>
  act(async () => {
    void revalidate(qc, familyTargets(qc, family, null));
  });
const calls = () => ({
  list: listFn.mock.calls.length,
  count: countFn.mock.calls.length,
  map: mapFn.mock.calls.length,
  stats: statsFn.mock.calls.length,
});

beforeEach(() => {
  server = { dismissed: new Set(), stage: new Map([[1, 10], [2, 10], [3, 10], [4, 20]]) };
  vi.mocked(fetchIsDismissed).mockImplementation(async (id: number) => server.dismissed.has(id));
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe('a dismissal elsewhere, under the pointer', () => {
  it('re-reads the count, map and stats at once; the rows move only once the pointer leaves', async () => {
    const qc = renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    fireEvent.pointerOver(within(list('A')).getByText('A 2'));
    const before = calls();

    server.dismissed.add(2);
    await elsewhere(qc, 'dismissals');
    await waitFor(() => expect(screen.getByLabelText('A count')).toHaveTextContent('3'));
    expect(mapFn).toHaveBeenCalledTimes(before.map + 1);
    expect(statsFn).toHaveBeenCalledTimes(before.stats + 1);
    expect(listFn).toHaveBeenCalledTimes(before.list);
    expect(rows('A')).toEqual([1, 2]);

    server.dismissed.add(1); // the pointer still resting: nothing moves
    await elsewhere(qc, 'dismissals');
    await waitFor(() => expect(screen.getByLabelText('A count')).toHaveTextContent('2'));
    expect(listFn).toHaveBeenCalledTimes(before.list);
    expect(rows('A')).toEqual([1, 2]);

    fireEvent.pointerLeave(list('A'));
    await waitFor(() => expect(rows('A')).toEqual([3, 4]));
    expect(listFn).toHaveBeenCalledTimes(before.list + 1); // one re-read for both changes
  });

  it('with no hand on the list, the row leaves at once', async () => {
    const qc = renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    server.dismissed.add(2);
    await elsewhere(qc, 'dismissals');
    await waitFor(() => expect(rows('A')).toEqual([1, 3]));
  });

  it('a pointer over anything outside the list ends the hand, as a portal closed under it leaves no pointerout', async () => {
    const qc = renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    fireEvent.pointerOver(within(list('A')).getByText('A 1'));
    server.dismissed.add(1);
    await elsewhere(qc, 'dismissals');
    expect(rows('A')).toEqual([1, 2]);

    fireEvent.pointerOver(screen.getByRole('button', { name: 'outside' }));
    await waitFor(() => expect(rows('A')).toEqual([2, 3]));
  });
});

describe('a pipeline stage changed elsewhere', () => {
  const A = { ...DEFAULT_FILTERS, pipeline: inStage(10) };
  const B = { ...DEFAULT_FILTERS, pipeline: inStage(20) };

  it('moves the row from one stage cohort to the other, into its sort position; a hand holds only its own list', async () => {
    const qc = renderCohorts({ f: A, label: 'A' }, { f: B, label: 'B' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    await waitFor(() => expect(rows('B')).toEqual([4]));

    server.stage.set(1, 20);
    await elsewhere(qc, 'board');
    await waitFor(() => expect(rows('A')).toEqual([2, 3]));
    await waitFor(() => expect(rows('B')).toEqual([1, 4]));

    fireEvent.pointerOver(within(list('A')).getByText('A 2'));
    server.stage.set(2, 20);
    await elsewhere(qc, 'board');
    await waitFor(() => expect(rows('B')).toEqual([1, 2]));
    expect(rows('A')).toEqual([2, 3]);
    await waitFor(() => expect(screen.getByLabelText('A count')).toHaveTextContent('1'));

    fireEvent.pointerLeave(list('A'));
    await waitFor(() => expect(rows('A')).toEqual([3]));
  });
});

describe("the list's own reads pass the hand", () => {
  it('a page fetched under the hand lands; the change it holds lands at the leave, every loaded page from the first', async () => {
    const qc = renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    fireEvent.pointerOver(within(list('A')).getByText('A 1'));
    server.dismissed.add(1);
    await elsewhere(qc, 'dismissals');
    const before = listFn.mock.calls.length;

    fireEvent.click(screen.getByRole('button', { name: 'A more' }));
    await waitFor(() => expect(rows('A')).toEqual([1, 2, 3, 4]));
    expect(listFn).toHaveBeenCalledTimes(before + 1);
    expect(listFn).toHaveBeenLastCalledWith(DEFAULT_FILTERS, true, 2);
    expect(isOwed(qc, browseKeys.cards(DEFAULT_FILTERS, DEFAULT_SORT))).toBe(true);

    fireEvent.pointerLeave(list('A'));
    await waitFor(() => expect(rows('A')).toEqual([2, 3, 4]));
    expect(listFn.mock.calls.slice(before + 1).map((c) => c[2])).toEqual([null, 3]);
  });

  it('a new order chosen under the hand loads at once', async () => {
    renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    const order = screen.getByRole('button', { name: 'A order' });
    fireEvent.pointerOver(order);
    fireEvent.click(order);
    await waitFor(() => expect(rows('A')).toEqual([4, 3]));
  });
});

describe('a click of my own under the hand', () => {
  it('composes: my row drops at once, the change made elsewhere waits for the leave, one re-read then', async () => {
    let land!: () => void;
    vi.mocked(dismissProperty).mockImplementation(
      () =>
        new Promise((resolve) => {
          land = () => {
            server.dismissed.add(2);
            resolve(undefined as never);
          };
        }),
    );
    const qc = renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    await act(async () => {}); // the rows' dismissal reads settle
    fireEvent.pointerOver(within(list('A')).getByText('A 1'));
    server.dismissed.add(1);
    await elsewhere(qc, 'dismissals');
    const before = calls();

    fireEvent.click(screen.getByRole('button', { name: 'hide 2' }));
    await waitFor(() => expect(rows('A')).toEqual([1])); // dropped in place, under the hand: the user's own click
    await act(async () => land());
    await waitFor(() => expect(countFn).toHaveBeenCalledTimes(before.count + 1));
    expect(listFn).toHaveBeenCalledTimes(before.list);
    expect(rows('A')).toEqual([1]);

    fireEvent.pointerLeave(list('A'));
    await waitFor(() => expect(rows('A')).toEqual([3, 4]));
    expect(listFn).toHaveBeenCalledTimes(before.list + 1);
  });
});

describe('a finger on the list', () => {
  it('holds from touchstart until the last finger lifts', async () => {
    const qc = renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    const card = within(list('A')).getByText('A 2');
    fireEvent.touchStart(card, { touches: [{ target: card }] });
    server.dismissed.add(2);
    await elsewhere(qc, 'dismissals');
    await waitFor(() => expect(screen.getByLabelText('A count')).toHaveTextContent('3'));
    expect(rows('A')).toEqual([1, 2]);

    fireEvent.touchEnd(card, { touches: [] });
    await waitFor(() => expect(rows('A')).toEqual([1, 3]));
  });
});

/* Review repair (W6): the ruling names the pointer and a touch. Keyboard focus
 * had been a third hand, and one that could stick: a focused row the click
 * removed fires no focusout, so the list stopped following for good. */
describe('keyboard focus is no hand', () => {
  it('a focus inside the list holds nothing', async () => {
    const qc = renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    act(() => within(list('A')).getByText('A 2').focus());
    server.dismissed.add(2);
    await elsewhere(qc, 'dismissals');
    await waitFor(() => expect(rows('A')).toEqual([1, 3]));
  });

  it('a focused row my click removed holds nothing once the pointer leaves', async () => {
    vi.mocked(dismissProperty).mockImplementation(async () => {
      server.dismissed.add(2);
      return undefined as never;
    });
    const qc = renderCohorts({ f: DEFAULT_FILTERS, label: 'A' });
    await waitFor(() => expect(rows('A')).toEqual([1, 2]));
    await act(async () => {}); // the rows' dismissal reads settle
    const hide = screen.getByRole('button', { name: 'hide 2' });
    fireEvent.pointerOver(hide);
    act(() => hide.focus());
    fireEvent.click(hide);
    await waitFor(() => expect(rows('A')).toEqual([1]));
    fireEvent.pointerLeave(list('A'));

    server.dismissed.add(1);
    await elsewhere(qc, 'dismissals');
    await waitFor(() => expect(rows('A')).toEqual([3, 4]));
  });
});

/* A value drawn from a read the hand does not hold (the collection page: the
 * header's count rides the read its table is drawn from). */
function Members({ label }: { label: string }) {
  const members = useQuery({ queryKey: ['members'], queryFn: async () => cohort(DEFAULT_FILTERS) });
  const hand = useListHold([['nothing-held']]);
  const shown = useHeld(hand, members.data ?? []);
  return (
    <section>
      <output aria-label={`${label} count`}>{members.data?.length ?? ''}</output>
      <ul aria-label={label} {...hand}>
        {shown.map((id) => (
          <li key={id}>
            <button type="button">{`${label} ${id}`}</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

describe('useHeld: rows held in the view', () => {
  function renderMembers() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    render(
      <QueryClientProvider client={qc}>
        <Members label="M" />
        <button type="button">outside</button>
      </QueryClientProvider>,
    );
    return qc;
  }
  const reread = (qc: QueryClient) =>
    act(async () => {
      void revalidate(qc, [['members']]);
    });

  it('the read lands at once (its count with it); the rows stay as drawn until the pointer leaves', async () => {
    const qc = renderMembers();
    await waitFor(() => expect(rows('M')).toEqual([1, 2, 3, 4]));
    fireEvent.pointerOver(within(list('M')).getByText('M 2'));
    server.dismissed.add(2);
    await reread(qc);
    await waitFor(() => expect(screen.getByLabelText('M count')).toHaveTextContent('3'));
    expect(rows('M')).toEqual([1, 2, 3, 4]);

    fireEvent.pointerLeave(list('M'));
    await waitFor(() => expect(rows('M')).toEqual([1, 3, 4]));
  });

  it('a finger holds them too, until it lifts; with no hand they follow at once', async () => {
    const qc = renderMembers();
    await waitFor(() => expect(rows('M')).toEqual([1, 2, 3, 4]));
    server.dismissed.add(4);
    await reread(qc);
    await waitFor(() => expect(rows('M')).toEqual([1, 2, 3]));

    const row = within(list('M')).getByText('M 1');
    fireEvent.touchStart(row, { touches: [{ target: row }] });
    server.dismissed.add(1);
    await reread(qc);
    await waitFor(() => expect(screen.getByLabelText('M count')).toHaveTextContent('2'));
    expect(rows('M')).toEqual([1, 2, 3]);
    fireEvent.touchEnd(row, { touches: [] });
    await waitFor(() => expect(rows('M')).toEqual([2, 3]));
  });
});
