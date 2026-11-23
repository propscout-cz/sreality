/* The feed's push notice over a fake push platform: push is on by default, so
 * the start screen asks for Apple's one tap itself while the prompt is
 * unanswered, says what to do once refused, and draws nothing where push is
 * on, impossible, or hidden by the plan. An iPhone that already allows
 * notifications comes on by itself and never sees the row. */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import * as auth from '@/lib/auth';
import { readPushOwner, resyncPush, setPushGate, writePushOwner } from '@/lib/push';
import { supabase } from '@/lib/supabase';
import { UnreadCountProvider } from '@/lib/unreadCount';
import { SERVER_KEY_B64, installPushFakes, type PushFakes } from '@/test/pushFakes';

import PushNotice from './PushNotice';

vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getNotificationUnreadCount: vi.fn(),
  getPushKey: vi.fn(),
  putPushSubscription: vi.fn(),
  deletePushSubscription: vi.fn(),
}));

let fakes: PushFakes;

function signIn(agendas: Record<string, boolean> = { notifications: true }) {
  vi.mocked(auth.useAuth).mockReturnValue({
    isAdmin: false,
    user: { id: 'user-1' },
    loading: false,
    agendas,
  } as unknown as ReturnType<typeof auth.useAuth>);
}

beforeEach(() => {
  localStorage.clear();
  setPushGate(null);
  signIn();
  vi.spyOn(supabase.auth, 'getSession').mockResolvedValue({
    data: { session: { user: { id: 'user-1' } } },
    error: null,
  } as never);
  vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 0 });
  vi.mocked(api.getPushKey).mockResolvedValue({ public_key: SERVER_KEY_B64 });
  vi.mocked(api.putPushSubscription).mockImplementation(async () => {
    fakes.log.push('PUT');
  });
});

afterEach(() => {
  fakes?.uninstall();
  vi.clearAllMocks();
});

function draw() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <UnreadCountProvider>
        <PushNotice />
      </UnreadCountProvider>
    </QueryClientProvider>,
  );
}

/* The hook has settled once the key was read and a tick has passed. */
async function settled() {
  await waitFor(() => expect(api.getPushKey).toHaveBeenCalled());
  await act(() => new Promise((r) => setTimeout(r, 20)));
}

const ROW = /Oznámení na tomto iPhonu jsou vypnutá/;

describe('the feed\'s push notice', () => {
  it('asks for the tap while the prompt is unanswered, and the tap turns push on', async () => {
    fakes = installPushFakes();
    draw();
    const row = await screen.findByRole('button', { name: ROW });
    expect(row).toHaveTextContent('Zapnout');
    fakes.log.length = 0;

    await userEvent.click(row);
    await waitFor(() => expect(screen.queryByRole('button', { name: ROW })).toBeNull());
    expect(fakes.log).toEqual(['requestPermission', 'subscribe', 'PUT']);
    expect(readPushOwner()).toBe('user-1');
  });

  it('keeps the row, and says why, when the server refuses the subscription', async () => {
    vi.mocked(api.putPushSubscription).mockRejectedValue(new ApiError('down', 0, null, 'network'));
    fakes = installPushFakes();
    draw();
    await userEvent.click(await screen.findByRole('button', { name: ROW }));
    const row = await screen.findByRole('button', { name: /nepodařilo zapnout/ });
    expect(row).toHaveTextContent('Zapnout');
    expect(fakes.subscription).toBeNull();
  });

  it('says what to do in Settings once notifications were refused', async () => {
    fakes = installPushFakes({ permission: 'denied' });
    draw();
    const row = await screen.findByRole('status');
    expect(row).toHaveTextContent('Povolte je v Nastavení iPhonu');
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('draws nothing once this iPhone is a push device', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    writePushOwner('user-1');
    draw();
    await settled();
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('is never seen where notifications are already allowed: push comes on by itself', async () => {
    fakes = installPushFakes({ permission: 'granted' });
    setPushGate(true);
    await resyncPush();
    expect(fakes.log).toEqual(['subscribe', 'PUT']);
    draw();
    await settled();
    expect(screen.queryByRole('button')).toBeNull();
    expect(fakes.requestPermission).not.toHaveBeenCalled();
  });

  it('draws nothing in a Safari tab, reading nothing', async () => {
    fakes = installPushFakes({ installed: false, push: false });
    draw();
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(screen.queryByRole('button')).toBeNull();
    expect(api.getPushKey).not.toHaveBeenCalled();
  });

  it('draws nothing, and reads nothing, where the plan hides notifications', async () => {
    signIn({ browse: true });
    fakes = installPushFakes();
    draw();
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(screen.queryByRole('button')).toBeNull();
    expect(api.getPushKey).not.toHaveBeenCalled();
  });
});
