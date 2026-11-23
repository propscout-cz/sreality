import { describe, expect, it, vi } from 'vitest';

import { readGroupFailure } from './readGroup';

const read = (isError: boolean, isFetching = false) => ({
  isError,
  isFetching,
  refetch: vi.fn(async () => ({}) as never),
});

describe('readGroupFailure', () => {
  it('is null while no read has failed', () => {
    expect(readGroupFailure([read(false), read(false, true)])).toBeNull();
  });

  it('retries only the reads that failed', () => {
    const ok = read(false);
    const bad = read(true);
    readGroupFailure([ok, bad])!.retry();
    expect(bad.refetch).toHaveBeenCalledTimes(1);
    expect(ok.refetch).not.toHaveBeenCalled();
  });

  it('is retrying while a failed read is back in flight, not while a loaded one refreshes', () => {
    expect(readGroupFailure([read(true, true)])!.retrying).toBe(true);
    expect(readGroupFailure([read(true), read(false, true)])!.retrying).toBe(false);
  });
});
