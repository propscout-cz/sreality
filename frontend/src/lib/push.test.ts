/* lib/push: the re-sync (W5b-2) and the key/body helpers. iOS never fires
 * `pushsubscriptionchange`, so the launch, a sign-in and the hourly resume
 * re-send this device's subscription; that re-send never prompts, never
 * subscribes, and never re-binds a subscription another account switched on. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { supabase } from '@/lib/supabase';
import { SERVER_KEY_B64, installPushFakes, type PushFakes } from '@/test/pushFakes';

import { decodePushKey, readPushOwner, resyncPush, writePushOwner } from './push';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  putPushSubscription: vi.fn(),
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
  vi.restoreAllMocks();
  vi.mocked(api.putPushSubscription).mockReset();
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
