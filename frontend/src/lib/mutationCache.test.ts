/* lib/mutationCache — a committed write is told to its own cache's listeners
 * (the curation sync owner's nudge), before the hook's own onSuccess, and a
 * listener can neither fail the write nor outlive its stop. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { MutationObserver, QueryClient } from '@tanstack/react-query';

import { createMutationCache, onCommittedWrite } from './mutationCache';

vi.mock('@/lib/toast', () => ({ pushToast: vi.fn() }));

const write = (qc: QueryClient, options: { onSuccess?: () => void } = {}) =>
  new MutationObserver(qc, { mutationFn: async () => 'ok', onError: () => {}, ...options }).mutate().catch(() => {});

afterEach(() => vi.restoreAllMocks());

describe('onCommittedWrite', () => {
  it("tells a commit to its own cache's listeners only, and nothing once stopped", async () => {
    const qc = new QueryClient({ mutationCache: createMutationCache() });
    const other = new QueryClient({ mutationCache: createMutationCache() });
    const heard = vi.fn();
    const stop = onCommittedWrite(qc.getMutationCache(), heard);
    await write(other);
    expect(heard).not.toHaveBeenCalled();
    await write(qc);
    expect(heard).toHaveBeenCalledTimes(1);
    stop();
    await write(qc);
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it('tells a write whose own onSuccess throws, and a listener that throws fails no write', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const qc = new QueryClient({ mutationCache: createMutationCache() });
    const order: string[] = [];
    onCommittedWrite(qc.getMutationCache(), () => {
      order.push('told');
      throw new Error('a closed channel');
    });
    const ok = new MutationObserver(qc, { mutationFn: async () => 'ok' });
    await ok.mutate();
    expect(ok.getCurrentResult().status).toBe('success');
    await write(qc, {
      onSuccess: () => {
        order.push('hook');
        throw new Error('the screen it updates is gone');
      },
    });
    expect(order).toEqual(['told', 'told', 'hook']);
  });
});
