/* The unread count's one observer (W5a, AUDIT F9): Shell mounts it once, and
 * every reader (the desktop badge, the phone bell, the Notifications page) gets
 * its number from it, so one poll serves both frames. Gated by the agenda, as
 * the nav item is; marking rows read refetches it through `notificationKeys.all`. */
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
import { notificationKeys } from '@/lib/queries';
import { UnreadCountProvider, useUnreadCount } from '@/lib/unreadCount';

vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getNotificationUnreadCount: vi.fn(),
}));

function signIn(isAdmin: boolean, agendas: Record<string, boolean> | null) {
  vi.mocked(auth.useAuth).mockReturnValue({
    isAdmin,
    session: null,
    user: null,
    loading: false,
    agendas,
  } as unknown as ReturnType<typeof auth.useAuth>);
}

function Reader({ name }: { name: string }) {
  const { visible, count } = useUnreadCount();
  return <p>{`${name}: ${visible ? count : 'hidden'}`}</p>;
}

function renderReaders() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <UnreadCountProvider>
        <Reader name="badge" />
        <Reader name="bell" />
      </UnreadCountProvider>
    </QueryClientProvider>,
  );
  return qc;
}

beforeEach(() => {
  vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 4 });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('the unread observer', () => {
  it('reads the count once and hands it to every reader', async () => {
    signIn(false, { notifications: true });
    renderReaders();
    expect(await screen.findByText('badge: 4')).toBeInTheDocument();
    expect(screen.getByText('bell: 4')).toBeInTheDocument();
    expect(api.getNotificationUnreadCount).toHaveBeenCalledTimes(1);
  });

  it('polls nothing and hides every bell when the plan has no notifications agenda', () => {
    signIn(false, { notifications: false });
    renderReaders();
    expect(screen.getByText('badge: hidden')).toBeInTheDocument();
    expect(screen.getByText('bell: hidden')).toBeInTheDocument();
    expect(api.getNotificationUnreadCount).not.toHaveBeenCalled();
  });

  it('shows the bell to an admin and while the plan read is not in', async () => {
    signIn(true, { notifications: false });
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <UnreadCountProvider>
          <Reader name="admin" />
        </UnreadCountProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('admin: 4')).toBeInTheDocument();
    unmount();
    signIn(false, null);
    renderReaders();
    expect(await screen.findByText('bell: 4')).toBeInTheDocument();
  });

  it('refetches when a mark-read invalidates the notifications keys', async () => {
    signIn(false, { notifications: true });
    const qc = renderReaders();
    expect(await screen.findByText('bell: 4')).toBeInTheDocument();
    vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 1 });
    await act(() => qc.invalidateQueries({ queryKey: notificationKeys.all }));
    await waitFor(() => expect(screen.getByText('bell: 1')).toBeInTheDocument());
    expect(screen.getByText('badge: 1')).toBeInTheDocument();
  });
});
