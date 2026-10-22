/* useMergeProperties — Browse's merge of a statement of letters, through the one
 * transport and the app's MutationCache (lib/mutationCache): a success is the
 * one receipt (one letter: the merge's own with "Otevřít #S"; several: a line
 * per letter and no page to open), the refresh (the note marks' counts
 * included, MS16) and `onMerged` (merge mode closes); a refusal is the route's
 * Czech sentence toasted once, merge mode left open. And useMergePreview,
 * MS12's counts before the click, over the letters two properties hold.
 * lib/api reads its base URL at module evaluation, hence the dynamic imports
 * after the env stub. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';

import type { Letters, MergeResult } from './api';
import { supabase } from './supabase';
import * as toast from './toast';

vi.mock('./toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./toast')>()),
  pushToast: vi.fn(() => 1),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test.invalid');
  vi.spyOn(supabase.auth, 'getSession').mockResolvedValue({
    data: { session: { access_token: 'USER-JWT' } },
    error: null,
  } as unknown as Awaited<ReturnType<typeof supabase.auth.getSession>>);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const letter = (survivor: number, retired: number, over: Partial<MergeResult> = {}): MergeResult => ({
  merge_group_id: `g${survivor}`, survivor_id: survivor, retired_ids: [retired], listings_moved: 1,
  pairs_ruled_same: 1, rulings_taken_back: 0, hidden_for_you: false,
  carried: { notes: 0, pipeline: null, collections: [], tags: [] },
  ...over,
});

async function mergeAnswered(status: number, body: unknown) {
  const sent: string[] = [];
  vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
    sent.push(String(init.body));
    return { ok: status < 400, status, text: async () => JSON.stringify(body) } as Response;
  });
  const { useMergeProperties } = await import('./useMergeProperties');
  const { createMutationCache } = await import('./mutationCache');
  const { curationKeys } = await import('./queries');
  const qc = new QueryClient({ mutationCache: createMutationCache() });
  qc.setQueryData(curationKeys.noteCounts, {});
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  const noteCountsStale = () => qc.getQueryState(curationKeys.noteCounts)?.isInvalidated;
  const onMerged = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
  const { result } = renderHook(() => useMergeProperties(onMerged), { wrapper });
  const merge = (letters: Letters = { 10: 'A', 20: 'A' }) => result.current.mutateAsync(letters);
  return { merge, sent, invalidate, noteCountsStale, onMerged };
}

it('posts the statement, toasts the one receipt, refreshes what the merge moved and closes merge mode', async () => {
  const { merge, sent, invalidate, noteCountsStale, onMerged } = await mergeAnswered(200, {
    letters: { A: letter(10, 20, { carried: { notes: 2, pipeline: null, collections: [], tags: [] } }) },
  });
  await act(() => merge());
  expect(JSON.parse(sent[0])).toEqual({ letters: { 10: 'A', 20: 'A' } });
  const [[kind, message, ttl, action]] = vi.mocked(toast.pushToast).mock.calls;
  expect([kind, message, ttl, action?.label]).toEqual(
    ['ok', 'Sloučeno do nemovitosti #10. Přesunuto: 2 poznámky.', 0, 'Otevřít #10']);
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['curation'] });
  expect(noteCountsStale()).toBe(true);
  expect(onMerged).toHaveBeenCalledTimes(1);
});

it('reads several letters as one receipt, a line per letter, with no page to open', async () => {
  const { merge, onMerged } = await mergeAnswered(200, {
    letters: { B: letter(30, 40), A: letter(10, 20) },
  });
  await act(() => merge({ 10: 'A', 20: 'A', 30: 'B', 40: 'B' }));
  const [[kind, message, ttl, action]] = vi.mocked(toast.pushToast).mock.calls;
  expect([kind, message, ttl, action]).toEqual(
    ['ok', 'A: Sloučeno do nemovitosti #10. B: Sloučeno do nemovitosti #30.', 0, undefined]);
  expect(onMerged).toHaveBeenCalledTimes(1);
});

it('toasts the route’s Czech refusal once and leaves merge mode open', async () => {
  const clash = 'Písmeno B: Inzerát v kategorii Byty a inzerát v kategorii Domy …, proto nemohou mít stejné písmeno. Dejte jim různá písmena.';
  const { merge, onMerged } = await mergeAnswered(409, {
    detail: { code: 'refused', message: clash, ids: [30, 40] },
  });
  await act(() => expect(merge({ 10: 'A', 20: 'A', 30: 'B', 40: 'B' })).rejects.toThrow(clash));
  expect(vi.mocked(toast.pushToast).mock.calls).toEqual([['err', clash]]);
  expect(onMerged).not.toHaveBeenCalled();
});

it('reads the counts a merge would take back once a letter holds two properties, as the statement', async () => {
  const calls: string[] = [];
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    calls.push(`${init.method ?? 'GET'} ${url} ${(init.headers as Record<string, string>).Authorization}`);
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ letters: { A: { property_ids: [10, 20], rulings_taken_back: 4 } } }),
    } as Response;
  });
  const { useMergePreview } = await import('./useMergeProperties');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const lone = renderHook(() => useMergePreview({ 20: 'A', 30: 'B' }), { wrapper });
  expect(lone.result.current.fetchStatus).toBe('idle');
  const two = renderHook(() => useMergePreview({ 20: 'A', 10: 'A', 30: 'B' }), { wrapper });
  await waitFor(() => expect(two.result.current.data?.letters.A.rulings_taken_back).toBe(4));
  /* B, held by one property, is not asked about: the server would refuse it. */
  expect(calls).toEqual([
    'GET https://api.test.invalid/properties/merge?letters=10%3AA%2C20%3AA Bearer USER-JWT',
  ]);
  expect(qc.getQueryData(['merged-adverts', 'merge-preview', '10:A,20:A'])).toEqual({
    letters: { A: { property_ids: [10, 20], rulings_taken_back: 4 } },
  });
});
