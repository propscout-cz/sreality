/* useBuildSkew's one probe on mount exists only for a page a service worker
 * may have booted from its cached shell; a page loaded from the network is
 * current by construction and must not probe on mount. */
import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { isBuildStale } from '@/lib/buildSkew';
import { pushToast } from '@/lib/toast';
import { useBuildSkew } from './useBuildSkew';

vi.mock('@/lib/buildSkew', () => ({ isBuildStale: vi.fn() }));
vi.mock('@/lib/toast', () => ({ pushToast: vi.fn() }));

function controlledByWorker(controlled: boolean) {
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: controlled ? { controller: {} } : undefined,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(isBuildStale).mockResolvedValue(true);
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  controlledByWorker(false);
});

describe('useBuildSkew mount probe', () => {
  it('probes once, 10 s after mount, when a service worker controls the page', async () => {
    controlledByWorker(true);
    renderHook(() => useBuildSkew());
    await vi.advanceTimersByTimeAsync(9_999);
    expect(isBuildStale).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(isBuildStale).toHaveBeenCalledTimes(1);
    expect(pushToast).toHaveBeenCalledTimes(1);
  });

  it('does not probe on mount without a worker', async () => {
    controlledByWorker(false);
    renderHook(() => useBuildSkew());
    await vi.advanceTimersByTimeAsync(60_000);
    expect(isBuildStale).not.toHaveBeenCalled();
  });

  it('drops the pending probe on unmount', async () => {
    controlledByWorker(true);
    const { unmount } = renderHook(() => useBuildSkew());
    unmount();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(isBuildStale).not.toHaveBeenCalled();
  });
});
