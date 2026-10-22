/* lib/acting — acting as a pipeline's owner (migration 641, design §3.5).
 *
 * Pins: the per-device active board and the sticky "edit as owner" flag
 * (remembered, validated, cleared on any board change); the context; the key
 * segment — byte-identical outside a provider, isolated inside — through the
 * real hooks (collections, tags, the pipeline reads, a dismissal); the pending
 * index of an acting write never answering for my own; a write inside the
 * provider moving only the owner's entries; and the server reconcile. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import * as api from '@/lib/api';
import * as queries from '@/lib/queries';
import { curationKeys, dismissalKeys, pipelineKeys, type PipelineMembers } from '@/lib/queries';
import type { PipelineShare, PipelineStage } from '@/lib/types';
import {
  ACTIVE_BOARD_KEY,
  ActingAs,
  EDIT_AS_OWNER_KEY,
  actOpts,
  actingKey,
  getActiveBoard,
  reloadActingStore,
  resetActiveBoard,
  setActiveBoard,
  setEditAsOwner,
  sharedBoardLabel,
  useActAs,
  useActing,
  useActiveBoard,
  useEditAsOwner,
  useEditAsOwnerShare,
} from './acting';
import { browseKeys } from './browseKeys';
import { settleCollectionWrite, useCollectionMembers, useCollections } from './collectionCache';
import { DEFAULT_FILTERS } from './filters';
import { DEFAULT_SORT } from './queries';
import { settleTagWrite } from './tagCache';
import { useArtifactLinks } from './useArtifactLinks';
import { usePipelineBoard, usePipelineMembers, usePipelineStages } from './pipelineReads';
import { reconcileActiveBoard } from './pipelineShares';
import { useCardTags } from './useCardTags';
import { useDismissal } from './useDismissal';
import { usePipelineCard } from './usePipelineCard';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    listCollections: vi.fn(async () => ({ data: [], total: 0 })),
    listTags: vi.fn(async () => ({ data: [] })),
    fetchArtifactLinks: vi.fn(async () => new Map()),
    removePipelineCard: vi.fn(),
    addPipelineCard: vi.fn(),
    movePipelineCard: vi.fn(),
    dismissProperty: vi.fn(async () => ({ dismissed: true })),
  };
});

vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
    fetchPipelineStages: vi.fn(async () => []),
    fetchPipelineBoard: vi.fn(async () => []),
    fetchPipelineMembers: vi.fn(async () => new Map()),
    fetchPropertyCollectionMemberSet: vi.fn(async () => new Map()),
    fetchPropertyTagMemberSet: vi.fn(async () => new Map()),
    fetchIsDismissed: vi.fn(async () => false),
  };
});

const READ: PipelineShare = { account_id: 'owner-r', email: 'petr@example.cz', mode: 'read' };
const EDIT: PipelineShare = { account_id: 'owner-e', email: 'jana@example.cz', mode: 'edit' };

beforeEach(() => {
  localStorage.clear();
  reloadActingStore();
});
afterEach(() => resetActiveBoard());

describe('the active board store', () => {
  it('defaults to my own board and remembers a pick across a reload', () => {
    expect(getActiveBoard()).toBeNull();
    setActiveBoard(EDIT);
    expect(JSON.parse(localStorage.getItem(ACTIVE_BOARD_KEY) ?? 'null')).toEqual(EDIT);
    reloadActingStore();
    expect(getActiveBoard()).toEqual(EDIT);
    resetActiveBoard();
    expect(localStorage.getItem(ACTIVE_BOARD_KEY)).toBeNull();
    expect(getActiveBoard()).toBeNull();
  });

  it('reads a malformed value as my own board', () => {
    for (const raw of ['{', '"x"', '{"account_id":"a","mode":"admin"}', '{"mode":"read"}']) {
      localStorage.setItem(ACTIVE_BOARD_KEY, raw);
      reloadActingStore();
      expect(getActiveBoard()).toBeNull();
    }
  });

  it('wakes its subscribers on a pick', () => {
    const { result } = renderHook(() => useActiveBoard());
    expect(result.current).toBeNull();
    act(() => setActiveBoard(READ));
    expect(result.current).toEqual(READ);
  });

  it('keeps "edit as owner" on across reloads, only on an edit board, and ends it on any board change', () => {
    const { result } = renderHook(() => useEditAsOwner());
    act(() => setEditAsOwner(true));
    expect(result.current).toBe(false); // my own board: nothing to act on
    act(() => setActiveBoard(READ));
    act(() => setEditAsOwner(true));
    expect(result.current).toBe(false); // a read share never acts on a property page
    act(() => setActiveBoard(EDIT));
    act(() => setEditAsOwner(true));
    expect(result.current).toBe(true);
    expect(localStorage.getItem(EDIT_AS_OWNER_KEY)).toBe('1');
    reloadActingStore();
    expect(result.current).toBe(true);
    act(() => setActiveBoard({ ...EDIT, mode: 'read' }));
    expect(result.current).toBe(false);
    expect(localStorage.getItem(EDIT_AS_OWNER_KEY)).toBeNull();
    act(() => setActiveBoard(EDIT));
    act(() => setEditAsOwner(true));
    act(() => resetActiveBoard());
    expect(result.current).toBe(false);
  });

  it('answers the property page\'s acting share only for an edit board with the switch on', () => {
    const { result } = renderHook(() => useEditAsOwnerShare());
    expect(result.current).toBeNull();
    act(() => setActiveBoard(EDIT));
    expect(result.current).toBeNull(); // the board alone does not act
    act(() => setEditAsOwner(true));
    expect(result.current).toEqual(EDIT);
    act(() => setEditAsOwner(false));
    expect(result.current).toBeNull();
  });

  it('labels a shared board by its owner and mode', () => {
    expect(sharedBoardLabel(READ)).toBe('petr@example.cz · jen ke čtení');
    expect(sharedBoardLabel(EDIT)).toBe('jana@example.cz · úpravy');
  });
});

describe('the context and the key segment', () => {
  it('answers null outside a provider and the share inside one', () => {
    expect(renderHook(() => useActing()).result.current).toBeNull();
    const wrapper = ({ children }: { children: ReactNode }) => <ActingAs share={EDIT}>{children}</ActingAs>;
    const { result } = renderHook(() => ({ share: useActing(), actAs: useActAs() }), { wrapper });
    expect(result.current).toEqual({ share: EDIT, actAs: 'owner-e' });
  });

  it('returns the very key outside a provider and a prefixed one inside', () => {
    const key = curationKeys.tags;
    expect(actingKey(null, key)).toBe(key);
    expect(actingKey(undefined, key)).toBe(key);
    expect(actingKey('owner-e', key)).toEqual(['acting', 'owner-e', 'curation', 'tags']);
    expect(actOpts(null)).toEqual([]);
    expect(actOpts('owner-e')).toEqual([{ actAs: 'owner-e' }]);
  });
});

/* Every curation / pipeline hook, mounted twice — as myself and as the owner —
 * in ONE cache: my entries sit under today's keys exactly, the owner's under
 * the acting segment, and the two never collide. */
describe('the hooks read under the acting segment', () => {
  function mountBoth() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const useEveryRead = () => {
      useCollections();
      useCollectionMembers();
      useCardTags(42);
      useArtifactLinks();
      usePipelineStages();
      usePipelineBoard();
      usePipelineMembers();
      useDismissal(42);
    };
    const own = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const acting = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>
        <ActingAs share={EDIT}>{children}</ActingAs>
      </QueryClientProvider>
    );
    renderHook(useEveryRead, { wrapper: own });
    renderHook(useEveryRead, { wrapper: acting });
    return qc;
  }

  const OWN_KEYS = [
    curationKeys.collections,
    curationKeys.propertyCollectionMembers,
    curationKeys.artifactLinks,
    curationKeys.tags,
    curationKeys.propertyTagMembers,
    pipelineKeys.stages,
    pipelineKeys.board,
    pipelineKeys.members,
    dismissalKeys.state(42),
  ];

  it('keeps my keys byte-identical and puts the owner\'s beside them', async () => {
    const qc = mountBoth();
    await waitFor(() => expect(qc.getQueryCache().getAll().every((q) => q.state.status === 'success')).toBe(true));
    const keys = qc.getQueryCache().getAll().map((q) => JSON.stringify(q.queryKey)).sort();
    const expected = [
      ...OWN_KEYS.map((k) => JSON.stringify(k)),
      ...OWN_KEYS.map((k) => JSON.stringify(['acting', 'owner-e', ...k])),
    ].sort();
    expect(keys).toEqual(expected);
  });

  it('hands the owner to every acting transport, and nothing to mine', async () => {
    const qc = mountBoth();
    await waitFor(() => expect(qc.getQueryCache().getAll().every((q) => q.state.status === 'success')).toBe(true));
    expect(api.listCollections).toHaveBeenCalledWith();
    expect(api.listCollections).toHaveBeenCalledWith({ actAs: 'owner-e' });
    expect(api.listTags).toHaveBeenCalledWith();
    expect(api.listTags).toHaveBeenCalledWith({ actAs: 'owner-e' });
    expect(queries.fetchIsDismissed).toHaveBeenCalledWith(42);
    expect(queries.fetchIsDismissed).toHaveBeenCalledWith(42, { actAs: 'owner-e' });
    expect(api.fetchArtifactLinks).toHaveBeenCalledWith();
    expect(api.fetchArtifactLinks).toHaveBeenCalledWith({ actAs: 'owner-e' });
    for (const read of [
      queries.fetchPipelineBoard,
      queries.fetchPipelineStages,
      queries.fetchPipelineMembers,
      queries.fetchPropertyCollectionMemberSet,
      queries.fetchPropertyTagMemberSet,
    ]) {
      expect(read).toHaveBeenCalledWith(expect.objectContaining({ actAs: 'owner-e' }));
      expect(read).toHaveBeenCalledWith(expect.objectContaining({ actAs: null }));
    }
  });
});

describe('a write inside the provider', () => {
  const ENTRY: PipelineStage = {
    id: 1, key: 'review', label: 'Zájem', position: 1, color: 'copper',
    is_terminal: false, is_entry: true, code: '1',
  };
  const card = { property_id: 42, stage_id: 1, stage_label: 'Zájem', stage_color: null, stage_code: '1', stage_position: 1, is_terminal: false };

  it('moves only the owner\'s entries and keeps its own pending index', async () => {
    let land!: () => void;
    vi.mocked(api.removePipelineCard).mockReturnValue(
      new Promise((resolve) => (land = () => resolve({ removed: true }))),
    );
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    qc.setQueryData(pipelineKeys.stages, [ENTRY]);
    qc.setQueryData<PipelineMembers>(pipelineKeys.members, new Map([[42, card]]));
    qc.setQueryData(actingKey('owner-e', pipelineKeys.stages), [ENTRY]);
    qc.setQueryData<PipelineMembers>(actingKey('owner-e', pipelineKeys.members), new Map([[42, card]]));
    const own = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const acting = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>
        <ActingAs share={EDIT}>{children}</ActingAs>
      </QueryClientProvider>
    );
    const mine = renderHook(() => usePipelineCard(), { wrapper: own });
    const theirs = renderHook(() => usePipelineCard(), { wrapper: acting });

    act(() => theirs.result.current.remove.mutate(42));
    await waitFor(() => expect(theirs.result.current.pending(42)).toBe(true));
    expect(mine.result.current.pending(42)).toBe(false);
    expect(api.removePipelineCard).toHaveBeenCalledWith(42, { actAs: 'owner-e' });
    expect(qc.getQueryData<PipelineMembers>(actingKey('owner-e', pipelineKeys.members))?.has(42)).toBe(false);
    expect(qc.getQueryData<PipelineMembers>(pipelineKeys.members)?.has(42)).toBe(true);
    await act(async () => land());
    await waitFor(() => expect(theirs.result.current.pending(42)).toBe(false));
  });
});

describe('every acting write lands on the owner\'s entries only, and never on Browse', () => {
  const ENTRY: PipelineStage = {
    id: 1, key: 'review', label: 'Zájem', position: 1, color: 'copper',
    is_terminal: false, is_entry: true, code: '1',
  };
  const LATER: PipelineStage = { ...ENTRY, id: 2, key: 'offer', label: 'Nabídka', position: 2, is_entry: false, code: '2' };
  const BROWSE = browseKeys.cards({ ...DEFAULT_FILTERS, pipeline: { set: 'out', in: [], out: [] } }, DEFAULT_SORT);

  function seeded() {
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
    for (const k of [pipelineKeys.stages, actingKey('owner-e', pipelineKeys.stages)]) qc.setQueryData(k, [ENTRY, LATER]);
    qc.setQueryData<PipelineMembers>(pipelineKeys.members, new Map());
    qc.setQueryData<PipelineMembers>(actingKey('owner-e', pipelineKeys.members), new Map());
    qc.setQueryData(BROWSE, { pages: [{ rows: [{ property_id: 42 }] }], pageParams: [null] });
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    const acting = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>
        <ActingAs share={EDIT}>{children}</ActingAs>
      </QueryClientProvider>
    );
    const roots = browseKeys.all.map(([r]) => r as unknown);
    const touchedBrowse = () => invalidate.mock.calls.some(([f]) => roots.includes(f?.queryKey?.[0]));
    return { qc, acting, touchedBrowse };
  }

  it('adds and moves a card as the owner, on the owner\'s map', async () => {
    vi.mocked(api.addPipelineCard).mockResolvedValue({ property_id: 42, stage_key: 'review', added: true });
    vi.mocked(api.movePipelineCard).mockResolvedValue({ property_id: 42, stage_id: 2, stage_key: 'offer' });
    const { qc, acting, touchedBrowse } = seeded();
    const theirs = renderHook(() => usePipelineCard(), { wrapper: acting });
    act(() => theirs.result.current.add.mutate(42));
    await waitFor(() =>
      expect(qc.getQueryData<PipelineMembers>(actingKey('owner-e', pipelineKeys.members))?.get(42)?.stage_id).toBe(1),
    );
    expect(api.addPipelineCard).toHaveBeenCalledWith(42, { actAs: 'owner-e' });
    act(() => theirs.result.current.move.mutate({ propertyId: 42, stageId: 2 }));
    await waitFor(() =>
      expect(qc.getQueryData<PipelineMembers>(actingKey('owner-e', pipelineKeys.members))?.get(42)?.stage_id).toBe(2),
    );
    expect(api.movePipelineCard).toHaveBeenCalledWith(42, 2, undefined, { actAs: 'owner-e' });
    expect(qc.getQueryData<PipelineMembers>(pipelineKeys.members)?.size).toBe(0);
    expect(qc.getQueryData<{ pages: { rows: unknown[] }[] }>(BROWSE)?.pages[0].rows).toHaveLength(1);
    await waitFor(() => expect(theirs.result.current.pending(42)).toBe(false));
    expect(touchedBrowse()).toBe(false);
  });

  it('dismisses as the owner without settling or re-reading Browse', async () => {
    const { qc, acting, touchedBrowse } = seeded();
    qc.setQueryData(dismissalKeys.state(42), false);
    qc.setQueryData(actingKey('owner-e', dismissalKeys.state(42)), false);
    const theirs = renderHook(() => useDismissal(42), { wrapper: acting });
    act(() => theirs.result.current.dismiss.mutate(undefined));
    await waitFor(() => expect(api.dismissProperty).toHaveBeenCalledWith(42, { actAs: 'owner-e' }));
    await waitFor(() => expect(theirs.result.current.pending).toBe(false));
    // The owner's state re-reads (as the owner); mine is never painted.
    expect(queries.fetchIsDismissed).toHaveBeenCalledWith(42, { actAs: 'owner-e' });
    expect(qc.getQueryData(dismissalKeys.state(42))).toBe(false);
    expect(qc.getQueryData<{ pages: { rows: unknown[] }[] }>(BROWSE)?.pages[0].rows).toHaveLength(1);
    expect(touchedBrowse()).toBe(false);
  });

  it('settles a tag or collection written as the owner on the owner\'s map alone', async () => {
    const { qc } = seeded();
    for (const set of [curationKeys.propertyTagMembers, curationKeys.propertyCollectionMembers]) {
      qc.setQueryData(set, new Map<number, number[]>());
      qc.setQueryData(actingKey('owner-e', set), new Map<number, number[]>());
    }
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    await settleTagWrite(qc, 42, 7, true, 'owner-e');
    await settleCollectionWrite(qc, 42, 9, true, 'owner-e');
    expect(qc.getQueryData<Map<number, number[]>>(actingKey('owner-e', curationKeys.propertyTagMembers))?.get(42)).toEqual([7]);
    expect(qc.getQueryData<Map<number, number[]>>(actingKey('owner-e', curationKeys.propertyCollectionMembers))?.get(42)).toEqual([9]);
    expect(qc.getQueryData<Map<number, number[]>>(curationKeys.propertyTagMembers)?.size).toBe(0);
    expect(qc.getQueryData<Map<number, number[]>>(curationKeys.propertyCollectionMembers)?.size).toBe(0);
    const keys = invalidate.mock.calls.map(([f]) => f?.queryKey);
    expect(keys.length).toBeGreaterThan(0);
    expect(keys.every((k) => k?.[0] === 'acting' && k[1] === 'owner-e')).toBe(true);
  });
});

describe('reconcileActiveBoard', () => {
  const lists = (me: PipelineShare[]) => ({ shared_with: [], shared_with_me: me });
  it('keeps my own board and a board still listed as it is', () => {
    expect(reconcileActiveBoard(lists([]), null)).toBe('keep');
    expect(reconcileActiveBoard(lists([READ, EDIT]), EDIT)).toBe('keep');
  });
  it('drops a board the server no longer lists', () => {
    expect(reconcileActiveBoard(lists([READ]), EDIT)).toBe('gone');
  });
  it('takes over a mode the owner changed', () => {
    expect(reconcileActiveBoard(lists([{ ...EDIT, mode: 'read' }]), EDIT)).toEqual({
      update: { ...EDIT, mode: 'read' },
    });
  });
});
