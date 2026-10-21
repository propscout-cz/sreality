/* usePush, the account sheet's switch (W5b): its state is READ from the
 * platform and this device's subscription, the reading happens when the sheet
 * opens (the worker update, the key), a tap runs requestPermission ->
 * subscribe -> PUT with nothing else awaited between (Apple's gesture rule),
 * a failed PUT leaves no subscription behind, and a 503 key never ends one. */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import * as auth from '@/lib/auth';
import { SERVER_KEY_B64, installPushFakes, type PushFakes } from '@/test/pushFakes';

import { readPushOwner, writePushOwner } from './push';
import { usePush } from './usePush';

vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getPushKey: vi.fn(),
  putPushSubscription: vi.fn(),
  deletePushSubscription: vi.fn(),
}));

let fakes: PushFakes;

beforeEach(() => {
  localStorage.clear();
  vi.mocked(auth.useAuth).mockReturnValue({ user: { id: 'user-1' } } as ReturnType<typeof auth.useAuth>);
  vi.mocked(api.getPushKey).mockResolvedValue({ public_key: SERVER_KEY_B64 });
  vi.mocked(api.putPushSubscription).mockImplementation(async () => {
    fakes.log.push('PUT');
  });
  vi.mocked(api.deletePushSubscription).mockImplementation(async () => {
    fakes.log.push('DELETE');
  });
});

afterEach(() => {
  fakes?.uninstall();
  vi.clearAllMocks();
});

const settled = async (result: { current: { state: string } }) =>
  waitFor(() => expect(result.current.state).not.toBe('busy'));

describe('usePush: the state', () => {
  it('is not_installed in a browser tab, before reading anything', () => {
    fakes = installPushFakes({ installed: false, push: false });
    const { result } = renderHook(() => usePush());
    expect(result.current.state).toBe('not_installed');
    expect(api.getPushKey).not.toHaveBeenCalled();
  });

  it('is unsupported in an installed app without Web Push', () => {
    fakes = installPushFakes({ push: false });
    const { result } = renderHook(() => usePush());
    expect(result.current.state).toBe('unsupported');
  });

  it('is unsupported where no worker is registered (the dev server)', async () => {
    fakes = installPushFakes({ registered: false });
    const { result } = renderHook(() => usePush());
    await waitFor(() => expect(result.current.state).toBe('unsupported'));
  });

  it('is off with no subscription, after the worker update and the key fetch', async () => {
    fakes = installPushFakes();
    const { result } = renderHook(() => usePush());
    expect(result.current.state).toBe('busy');
    await settled(result);
    expect(result.current.state).toBe('off');
    expect(fakes.update).toHaveBeenCalledTimes(1);
    expect(api.getPushKey).toHaveBeenCalledTimes(1);
  });

  it('is on when this device holds a subscription', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    const { result } = renderHook(() => usePush());
    await settled(result);
    expect(result.current.state).toBe('on');
  });

  it('is denied once the user refused notifications', async () => {
    fakes = installPushFakes({ permission: 'denied' });
    const { result } = renderHook(() => usePush());
    await settled(result);
    expect(result.current.state).toBe('denied');
  });

  it('is unavailable when the server has no key, and a 503 ends no subscription', async () => {
    vi.mocked(api.getPushKey).mockRejectedValue(new ApiError('push is not configured', 503, null));
    fakes = installPushFakes();
    const { result } = renderHook(() => usePush());
    await settled(result);
    expect(result.current.state).toBe('unavailable');

    fakes.uninstall();
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    fakes.rotateKey();
    const second = renderHook(() => usePush());
    await settled(second.result);
    expect(second.result.current.state).toBe('on');
    expect(fakes.subscription!.unsubscribe).not.toHaveBeenCalled();
  });

  it('ends a subscription made under another server key, on the server key it got', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-1');
    fakes.rotateKey();
    const { result } = renderHook(() => usePush());
    await settled(result);
    expect(fakes.log).toContain('unsubscribe');
    expect(result.current.state).toBe('off');
    expect(readPushOwner()).toBeNull();
  });

  it('keeps a subscription made under the same key', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    const { result } = renderHook(() => usePush());
    await settled(result);
    act(() => result.current.disable());
    await settled(result);
    act(() => result.current.enable());
    await waitFor(() => expect(result.current.state).toBe('on'));
    fakes.log.length = 0;
    const again = renderHook(() => usePush());
    await settled(again.result);
    expect(again.result.current.state).toBe('on');
    expect(fakes.log).not.toContain('unsubscribe');
  });
});

describe('usePush: enable', () => {
  it('asks permission inside the tap, then subscribes, then registers with the server', async () => {
    fakes = installPushFakes();
    const { result } = renderHook(() => usePush());
    await settled(result);
    fakes.log.length = 0;

    act(() => result.current.enable());
    /* Synchronously, inside the handler: nothing was awaited before it. */
    expect(fakes.log).toEqual(['requestPermission']);
    expect(result.current.state).toBe('busy');

    await waitFor(() => expect(result.current.state).toBe('on'));
    expect(fakes.log).toEqual(['requestPermission', 'subscribe', 'PUT']);
    expect(fakes.subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: expect.any(Uint8Array),
    });
    const key = fakes.subscribe.mock.calls[0][0].applicationServerKey as Uint8Array;
    expect(key).toHaveLength(65);
    expect(key[0]).toBe(4);
    expect(api.putPushSubscription).toHaveBeenCalledWith({
      endpoint: 'https://web.push.apple.com/QGx1',
      expirationTime: null,
      keys: { p256dh: 'BPp256dh', auth: 'authsecret' },
      user_agent: navigator.userAgent,
    });
    expect(readPushOwner()).toBe('user-1');
  });

  it('subscribes nothing when permission is refused', async () => {
    fakes = installPushFakes();
    fakes.requestPermission.mockImplementation(() => {
      fakes.log.push('requestPermission');
      fakes.permission = 'denied';
      return Promise.resolve('denied');
    });
    const { result } = renderHook(() => usePush());
    await settled(result);
    act(() => result.current.enable());
    await waitFor(() => expect(result.current.state).toBe('denied'));
    expect(fakes.subscribe).not.toHaveBeenCalled();
    expect(api.putPushSubscription).not.toHaveBeenCalled();
  });

  it('unsubscribes again and says so when the server refuses the subscription', async () => {
    vi.mocked(api.putPushSubscription).mockRejectedValue(new ApiError('down', 0, null, 'network'));
    fakes = installPushFakes();
    const { result } = renderHook(() => usePush());
    await settled(result);
    act(() => result.current.enable());
    await waitFor(() => expect(result.current.error).toBe('Oznámení se nepodařilo zapnout. Zkuste to znovu.'));
    await settled(result);
    expect(result.current.state).toBe('off');
    expect(fakes.subscription).toBeNull();
    expect(readPushOwner()).toBeNull();
  });

  it('names the iPhone when the server refuses a non-Apple endpoint (422)', async () => {
    vi.mocked(api.putPushSubscription).mockRejectedValue(new ApiError('not apple', 422, null));
    fakes = installPushFakes();
    const { result } = renderHook(() => usePush());
    await settled(result);
    act(() => result.current.enable());
    await waitFor(() => expect(result.current.error).toBe('Oznámení zatím fungují jen na iPhonu.'));
  });

  it('surfaces a failed subscribe', async () => {
    fakes = installPushFakes();
    fakes.subscribe.mockRejectedValue(new DOMException('no', 'NotAllowedError'));
    const { result } = renderHook(() => usePush());
    await settled(result);
    act(() => result.current.enable());
    await waitFor(() => expect(result.current.error).toBe('Oznámení se nepodařilo zapnout. Zkuste to znovu.'));
    expect(result.current.state).toBe('off');
  });
});

describe('usePush: disable', () => {
  it('unsubscribes, then tells the server it was switched off', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-1');
    const { result } = renderHook(() => usePush());
    await settled(result);
    fakes.log.length = 0;

    act(() => result.current.disable());
    await waitFor(() => expect(result.current.state).toBe('off'));
    expect(fakes.log).toEqual(['unsubscribe', 'DELETE']);
    expect(api.deletePushSubscription).toHaveBeenCalledWith({
      endpoint: 'https://web.push.apple.com/QGx1',
      reason: 'switched_off',
    });
    expect(readPushOwner()).toBeNull();
  });

  it('is off even when the server cannot be told', async () => {
    vi.mocked(api.deletePushSubscription).mockRejectedValue(new ApiError('down', 0, null, 'network'));
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    const { result } = renderHook(() => usePush());
    await settled(result);
    act(() => result.current.disable());
    await waitFor(() => expect(result.current.state).toBe('off'));
    expect(result.current.error).toBeNull();
  });
});
