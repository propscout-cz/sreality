/* lib/push: the re-sync (W5b-2) and the key/body helpers. iOS never fires
 * `pushsubscriptionchange`, so the launch, a sign-in and the hourly resume
 * re-send this device's subscription; that re-send never prompts, never
 * subscribes, and never re-binds a subscription another account switched on. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { supabase } from '@/lib/supabase';
import { SERVER_KEY_B64, installPushFakes, type PushFakes } from '@/test/pushFakes';

import {
  SIGN_OUT_NOTICE_MS,
  decodePushKey,
  endPushHere,
  readPushOwner,
  resyncPush,
  signOutPush,
  writePushOwner,
} from './push';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  putPushSubscription: vi.fn(),
  deletePushSubscription: vi.fn(),
}));

let fakes: PushFakes;
const session = (id: string | null) =>
  ({ data: { session: id ? { user: { id } } : null }, error: null }) as unknown as Awaited<
    ReturnType<typeof supabase.auth.getSession>
  >;

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(supabase.auth, 'getSession').mockResolvedValue(session('user-1'));
  vi.mocked(api.putPushSubscription).mockResolvedValue(undefined);
});

afterEach(() => {
  fakes?.uninstall();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.mocked(api.putPushSubscription).mockReset();
  vi.mocked(api.deletePushSubscription).mockReset();
});

describe('resyncPush', () => {
  it("re-sends the signed-in owner's subscription, and only that", async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-1');
    await resyncPush();
    expect(api.putPushSubscription).toHaveBeenCalledTimes(1);
    expect(api.putPushSubscription).toHaveBeenCalledWith({
      endpoint: 'https://web.push.apple.com/QGx1',
      expirationTime: null,
      keys: { p256dh: 'BPp256dh', auth: 'authsecret' },
      user_agent: navigator.userAgent,
    });
    expect(fakes.requestPermission).not.toHaveBeenCalled();
    expect(fakes.subscribe).not.toHaveBeenCalled();
  });

  it('does nothing without a subscription', async () => {
    fakes = installPushFakes();
    await resyncPush();
    expect(api.putPushSubscription).not.toHaveBeenCalled();
    expect(fakes.subscribe).not.toHaveBeenCalled();
  });

  it('does nothing where there is no push at all', async () => {
    fakes = installPushFakes({ push: false });
    await resyncPush();
    expect(api.putPushSubscription).not.toHaveBeenCalled();
  });

  it('does nothing while signed out', async () => {
    vi.mocked(supabase.auth.getSession).mockResolvedValue(session(null));
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-1');
    await resyncPush();
    expect(api.putPushSubscription).not.toHaveBeenCalled();
    expect(fakes.subscription).not.toBeNull();
  });

  it('ends a subscription another account switched on, instead of re-binding it', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-0');
    await resyncPush();
    expect(api.putPushSubscription).not.toHaveBeenCalled();
    expect(fakes.log).toEqual(['unsubscribe']);
    expect(readPushOwner()).toBeNull();
  });

  it('ends a subscription nobody here switched on', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    await resyncPush();
    expect(api.putPushSubscription).not.toHaveBeenCalled();
    expect(fakes.subscription).toBeNull();
  });

  it('re-sends when this device cannot read its storage (nothing to tell apart)', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });
    await resyncPush();
    expect(api.putPushSubscription).toHaveBeenCalledTimes(1);
  });

  it('swallows a failed re-send', async () => {
    vi.mocked(api.putPushSubscription).mockRejectedValue(new ApiError('down', 0, null, 'network'));
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-1');
    await expect(resyncPush()).resolves.toBeUndefined();
    expect(fakes.subscription).not.toBeNull();
  });
});

/* Binding follows identity (AUDIT F18): no subscription outlives its user. */
describe('endPushHere', () => {
  it('ends the subscription here and forgets its owner, telling no server', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-1');
    await endPushHere();
    expect(fakes.subscription).toBeNull();
    expect(readPushOwner()).toBeNull();
    expect(api.deletePushSubscription).not.toHaveBeenCalled();
  });

  it('is a no-op without a subscription or without push', async () => {
    fakes = installPushFakes();
    await expect(endPushHere()).resolves.toBeUndefined();
    fakes.uninstall();
    fakes = installPushFakes({ push: false });
    await expect(endPushHere()).resolves.toBeUndefined();
  });
});

describe('signOutPush', () => {
  it('tells the server it signed out, then ends the subscription here', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-1');
    vi.mocked(api.deletePushSubscription).mockImplementation(async () => {
      fakes.log.push('DELETE');
    });
    await signOutPush();
    expect(fakes.log).toEqual(['DELETE', 'unsubscribe']);
    expect(api.deletePushSubscription).toHaveBeenCalledWith(
      { endpoint: 'https://web.push.apple.com/QGx1', reason: 'signed_out' },
      { signal: expect.any(AbortSignal) },
    );
    expect(readPushOwner()).toBeNull();
  });

  it(`waits at most ${SIGN_OUT_NOTICE_MS} ms for the server, and aborts the call`, async () => {
    vi.useFakeTimers();
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    let signal: AbortSignal | undefined;
    vi.mocked(api.deletePushSubscription).mockImplementation((_body, opts) => {
      signal = opts?.signal;
      return new Promise(() => {});
    });
    let done = false;
    void signOutPush().then(() => {
      done = true;
    });
    await vi.advanceTimersByTimeAsync(SIGN_OUT_NOTICE_MS - 1);
    expect(done).toBe(false);
    expect(fakes.subscription).not.toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    expect(done).toBe(true);
    expect(signal?.aborted).toBe(true);
    expect(fakes.subscription).toBeNull();
  });

  it('still ends the subscription when the server cannot be told', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    vi.mocked(api.deletePushSubscription).mockRejectedValue(new ApiError('down', 0, null, 'network'));
    await signOutPush();
    expect(fakes.subscription).toBeNull();
  });

  it('tells nobody anything without a subscription', async () => {
    fakes = installPushFakes();
    await signOutPush();
    expect(api.deletePushSubscription).not.toHaveBeenCalled();
  });
});

describe('decodePushKey', () => {
  it('reads a base64url P-256 point', () => {
    const key = decodePushKey(SERVER_KEY_B64)!;
    expect(key).toHaveLength(65);
    expect(key[0]).toBe(4);
  });

  it.each(['', 'not base64!', 'AAAA', SERVER_KEY_B64.slice(0, 40)])('refuses %j', (raw) => {
    expect(decodePushKey(raw)).toBeNull();
  });
});
