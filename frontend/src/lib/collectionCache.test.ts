import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { createElement, type ReactNode } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('./api', async (orig) => ({
  ...(await orig<typeof import('./api')>()),
  listCollections: vi.fn(),
}));
vi.mock('./queries', async (orig) => ({
  ...(await orig<typeof import('./queries')>()),
  fetchPropertyCollectionMemberSet: vi.fn(),
}));

import { listCollections } from './api';
import {
  collectionLabel,
  isMine,
  revalidateCollections,
  shareAudience,
  useCollectionMembership,
  useCollections,
} from './collectionCache';
import { browseKeys } from './browseKeys';
import { curationKeys, fetchPropertyCollectionMemberSet } from './queries';
import type { Collection } from './types';

const own = (over: Partial<Collection> = {}): Collection => ({
  id: 1, name: 'Šortlist', description: null, created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z', listing_count: 1, monitoring_enabled: false,
  notify_channels: [], is_system: false, owner_email: null, shared_with: [], ...over,
});
const sharedBy = (email: string, over: Partial<Collection> = {}): Collection =>
  own({ owner_email: email, shared_with: [{ account_id: 'me', email: null }], ...over });

describe('mine vs shared: one predicate, one label, one audience', () => {
  it('isMine is owner_email == null', () => {
    expect(isMine(own())).toBe(true);
    expect(isMine(sharedBy('petr@example.cz'))).toBe(false);
    expect(isMine({ owner_email: null })).toBe(true);
  });

  it('collectionLabel names the owner of a shared collection only', () => {
    expect(collectionLabel(own({ name: 'monitoring' }))).toBe('monitoring');
    expect(collectionLabel(sharedBy('petr@example.cz', { name: 'monitoring' })))
      .toBe('monitoring · petr@example.cz');
  });

  it('shareAudience: my recipients and the owners of what is shared with me, sorted, once each', () => {
    const byId = new Map<number, Collection>([
      [1, own({ id: 1, shared_with: [
        { account_id: 'b', email: 'zuzana@example.cz' },
        { account_id: 'c', email: 'adam@example.cz' },
      ] })],
      [2, sharedBy('petr@example.cz', { id: 2 })],
      [3, sharedBy('adam@example.cz', { id: 3 })],
      [4, own({ id: 4 })],
    ]);
    expect(shareAudience([1, 2, 3, 4], byId)).toEqual([
      'adam@example.cz', 'petr@example.cz', 'zuzana@example.cz',
    ]);
    // An unshared collection, or an id the list does not know, has no audience.
    expect(shareAudience([4, 99], byId)).toEqual([]);
    expect(shareAudience([2], byId)).toEqual(['petr@example.cz']);
  });
});

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
}

describe('collection read hooks', () => {
  it('useCollections lists the collections and indexes them by id', async () => {
    const a = { id: 7, name: 'Šortlist' } as Collection;
    vi.mocked(listCollections).mockResolvedValue({ data: [a], total: 1 });
    const { result } = renderHook(() => useCollections(), { wrapper: wrapper() });
    expect(result.current.collections).toEqual([]);
    await waitFor(() => expect(result.current.collections).toEqual([a]));
    expect(result.current.byId.get(7)).toBe(a);
  });

  it('useCollectionMembership is not ready until the member map lands', async () => {
    vi.mocked(listCollections).mockResolvedValue({
      data: [own({ id: 7 }), own({ id: 9 })],
      total: 2,
    });
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7, 9]]]));
    const { result } = renderHook(() => useCollectionMembership(42), { wrapper: wrapper() });
    expect(result.current.ready).toBe(false);
    expect(result.current.saved).toBe(false);
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect([...result.current.ids]).toEqual([7, 9]);
    expect(result.current.saved).toBe(true);
  });

  /* The split needs the owners, so a resolved map alone must not paint a mark. */
  it('useCollectionMembership waits for the collection list too', async () => {
    vi.mocked(listCollections).mockReturnValue(new Promise(() => {}));
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    const { result } = renderHook(() => useCollectionMembership(42), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.ids.size).toBe(1));
    expect(result.current.ready).toBe(false);
    expect(result.current.saved).toBe(false);
  });

  it('partitions a property\'s collections into mine and shared; saved means mine', async () => {
    vi.mocked(listCollections).mockResolvedValue({
      data: [own({ id: 7 }), sharedBy('petr@example.cz', { id: 9 })],
      total: 2,
    });
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(
      // 404: an id the list does not know (a stale cache) lands in neither half.
      new Map([[42, [7, 9, 404]], [43, [9]]]),
    );
    const both = renderHook(() => useCollectionMembership(42), { wrapper: wrapper() });
    await waitFor(() => expect(both.result.current.ready).toBe(true));
    expect([...both.result.current.ids]).toEqual([7, 9, 404]);
    expect([...both.result.current.mine]).toEqual([7]);
    expect([...both.result.current.shared]).toEqual([9]);
    expect(both.result.current.saved).toBe(true);

    const sharedOnly = renderHook(() => useCollectionMembership(43), { wrapper: wrapper() });
    await waitFor(() => expect(sharedOnly.result.current.ready).toBe(true));
    expect(sharedOnly.result.current.saved).toBe(false);
  });

  it('useCollections splits the list by owner', async () => {
    const a = own({ id: 7 });
    const b = sharedBy('petr@example.cz', { id: 9 });
    vi.mocked(listCollections).mockResolvedValue({ data: [a, b], total: 2 });
    const { result } = renderHook(() => useCollections(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.collections).toHaveLength(2));
    expect(result.current.mine).toEqual([a]);
    expect(result.current.shared).toEqual([b]);
  });

  /* MS16: a failed read is a retry, never "in no collection". */
  it('a failed member read stays not-ready and errored', async () => {
    vi.mocked(fetchPropertyCollectionMemberSet).mockRejectedValue(new Error('HTTP 500'));
    const { result } = renderHook(() => useCollectionMembership(42), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.query.isError).toBe(true));
    expect(result.current.ready).toBe(false);
  });
});

describe('collection revalidation contract', () => {
  it('invalidates exactly the two global keys when no collection is named', () => {
    /* The shared member map is pinned first on purpose: every membership writer
     * but the save menu had hand-typed a key list that omitted it, which is the
     * drift this helper exists to end (docs/architecture.md rule 18). */
    const invalidateQueries = vi.fn();
    revalidateCollections({ invalidateQueries } as never);
    expect(invalidateQueries.mock.calls.map(([arg]) => arg.queryKey)).toEqual([
      curationKeys.propertyCollectionMembers,
      curationKeys.collections,
    ]);
  });

  it("adds the named collection's own page when the write touches one", () => {
    const invalidateQueries = vi.fn();
    revalidateCollections({ invalidateQueries } as never, { collection_id: 7 });
    expect(invalidateQueries.mock.calls.map(([arg]) => arg.queryKey)).toEqual([
      curationKeys.propertyCollectionMembers,
      curationKeys.collections,
      curationKeys.collection(7),
    ]);
  });

  /* What a membership change does to Browse is the settle's (lib/curationSettle):
   * revalidating the collection reads never re-reads a Browse surface. */
  it('never re-reads a Browse surface', () => {
    const invalidateQueries = vi.fn();
    revalidateCollections({ invalidateQueries } as never, { collection_id: 7, shareChanged: true });
    const roots = invalidateQueries.mock.calls.map(([arg]) => arg.queryKey[0]);
    for (const [root] of browseKeys.all) expect(roots).not.toContain(root);
  });
});

/* The hooks are the only readers of the two keys: a hand-typed copy would skip
 * whatever the hooks add (W5's mine/shared split) and paint its own answer. */
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p);
  }
  return out;
}

// A read pairs the key with a queryFn; an invalidateQueries over it is a writer's refresh.
// The key may carry the acting segment (lib/acting `actingKey(actAs, …)`).
const KEY_READ =
  /queryKey:\s*(?:actingKey\(\s*\w+,\s*)?curationKeys\.(collections|propertyCollectionMembers)\)?,\s*queryFn\b/;
const FETCHER_IMPORT =
  /import\s*\{[^}]*\b(listCollections|fetchPropertyCollectionMemberSet)\b[^}]*\}\s*from/;
// lib/api.ts + lib/queries.ts define the fetchers; queries.ts's Browse collection
// prefilter is a cohort reader that reads the full map outside the cache.
const OWNERS = new Set(['lib/collectionCache.ts', 'lib/api.ts', 'lib/queries.ts']);

describe('collection read census', () => {
  const root = join(process.cwd(), 'src');

  it('no surface outside lib/collectionCache reads the two keys or imports their fetchers', () => {
    const offenders = sources(root)
      .map((f) => relative(root, f))
      .filter((rel) => !OWNERS.has(rel))
      .filter((rel) => {
        const src = readFileSync(join(root, rel), 'utf8');
        return KEY_READ.test(src) || FETCHER_IMPORT.test(src);
      });
    expect(offenders).toEqual([]);
  });

  it('sees both reads and both imports in lib/collectionCache (so a regex miss cannot pass)', () => {
    const src = readFileSync(join(root, 'lib/collectionCache.ts'), 'utf8');
    expect(src.match(new RegExp(KEY_READ.source, 'g'))).toHaveLength(2);
    expect(src.match(new RegExp(FETCHER_IMPORT.source, 'g'))).toHaveLength(2);
  });
});
