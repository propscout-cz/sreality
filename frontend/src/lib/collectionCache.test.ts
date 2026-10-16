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
  revalidateCollections,
  useCollectionMembership,
  useCollections,
} from './collectionCache';
import { browseKeys } from './browseKeys';
import { curationKeys, fetchPropertyCollectionMemberSet } from './queries';
import type { Collection } from './types';

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
    vi.mocked(fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7, 9]]]));
    const { result } = renderHook(() => useCollectionMembership(42), { wrapper: wrapper() });
    expect(result.current.ready).toBe(false);
    expect(result.current.saved).toBe(false);
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect([...result.current.ids]).toEqual([7, 9]);
    expect(result.current.saved).toBe(true);
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

  /* When Browse is scoped to collections, membership IS the cohort: a removal
   * has to drop the row from the list, not just un-fill a glyph. */
  it('re-reads the Browse surfaces only when membership is the cohort', () => {
    const off = vi.fn();
    revalidateCollections({ invalidateQueries: off } as never, { collection_id: 7 });
    expect(off).toHaveBeenCalledTimes(3);

    const on = vi.fn();
    revalidateCollections({ invalidateQueries: on } as never, { cohortScoped: true });
    expect(on.mock.calls.map(([arg]) => arg.queryKey)).toEqual([
      curationKeys.propertyCollectionMembers,
      curationKeys.collections,
      ...browseKeys.all,
    ]);
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
const KEY_READ =
  /queryKey:\s*curationKeys\.(collections|propertyCollectionMembers),\s*queryFn\b/;
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
