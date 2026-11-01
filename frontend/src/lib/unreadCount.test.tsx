/* The unread count's one observer (W5a, AUDIT F9): Shell mounts it once, and
 * every reader (the desktop badge, the phone bell, the Notifications page) gets
 * its number from it, so one poll serves both frames. Gated by the agenda, as
 * the nav item is; a mark-read anywhere re-reads it through the curation clock (W7).
 * The same number is the installed app's icon badge (ruling R4, lib/appBadge);
 * in the page only this observer sets it (an identity change clears it). */
import { act, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
import { familyTargets } from '@/lib/curationSync';
import { notificationKeys } from '@/lib/queries';
import { revalidate } from '@/lib/revalidate';
import { syncAppBadge } from '@/lib/appBadge';
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

  /* W7, the operator's ask: a notification resolved in another window or the app. */
  it('follows a change made elsewhere within one clock answer, every bell at once', async () => {
    signIn(false, { notifications: true });
    const qc = renderReaders();
    expect(await screen.findByText('bell: 4')).toBeInTheDocument();
    vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 1 });
    await act(async () => {
      void revalidate(qc, familyTargets(qc, 'notifications', null));
    });
    await waitFor(() => expect(screen.getByText('bell: 1')).toBeInTheDocument());
    expect(screen.getByText('badge: 1')).toBeInTheDocument();
  });

  it('never moves on a share: an owner I follow carries no notifications read of mine', async () => {
    signIn(false, { notifications: true });
    const qc = renderReaders();
    expect(await screen.findByText('bell: 4')).toBeInTheDocument();
    expect(familyTargets(qc, 'notifications', 'owner-1')).toEqual([]);
  });

  it('polls nothing: the clock is its one trigger', async () => {
    signIn(false, { notifications: true });
    const qc = renderReaders();
    expect(await screen.findByText('bell: 4')).toBeInTheDocument();
    const query = qc.getQueryCache().find({ queryKey: notificationKeys.unreadCount });
    expect(query?.observers[0].options.refetchInterval).toBeUndefined();
  });
});

/* jsdom has no Badging API and is no installed app; each test grants what it needs. */
type BadgeNavigator = Navigator & { standalone?: boolean };
function grant({ installed = true, api: withApi = true } = {}) {
  const nav = navigator as BadgeNavigator;
  const setAppBadge = vi.fn(() => Promise.resolve());
  const clearAppBadge = vi.fn(() => Promise.resolve());
  if (withApi) {
    Object.defineProperty(nav, 'setAppBadge', { value: setAppBadge, configurable: true });
    Object.defineProperty(nav, 'clearAppBadge', { value: clearAppBadge, configurable: true });
  }
  Object.defineProperty(nav, 'standalone', { value: installed, configurable: true });
  return { setAppBadge, clearAppBadge };
}

describe('the app-icon badge', () => {
  afterEach(() => {
    const nav = navigator as unknown as Record<string, unknown>;
    delete nav.setAppBadge;
    delete nav.clearAppBadge;
    delete nav.standalone;
  });

  it('shows the unread count on the installed app\'s icon', async () => {
    const badge = grant();
    signIn(false, { notifications: true });
    renderReaders();
    await waitFor(() => expect(badge.setAppBadge).toHaveBeenCalledWith(4));
    expect(badge.clearAppBadge).not.toHaveBeenCalled();
  });

  it('clears the icon at zero, and when the plan hides notifications', async () => {
    const badge = grant();
    vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 0 });
    signIn(false, { notifications: true });
    const { unmount } = render(
      <QueryClientProvider client={new QueryClient()}>
        <UnreadCountProvider>
          <Reader name="bell" />
        </UnreadCountProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(badge.clearAppBadge).toHaveBeenCalled());
    expect(badge.setAppBadge).not.toHaveBeenCalled();
    unmount();
    badge.clearAppBadge.mockClear();
    signIn(false, { notifications: false });
    renderReaders();
    expect(badge.clearAppBadge).toHaveBeenCalled();
    expect(badge.setAppBadge).not.toHaveBeenCalled();
  });

  it('leaves the icon alone until the first count lands', () => {
    const badge = grant();
    vi.mocked(api.getNotificationUnreadCount).mockReturnValue(new Promise(() => {}));
    signIn(false, { notifications: true });
    renderReaders();
    expect(badge.setAppBadge).not.toHaveBeenCalled();
    expect(badge.clearAppBadge).not.toHaveBeenCalled();
  });

  it('clears the icon when Shell goes (sign-out)', async () => {
    const badge = grant();
    signIn(false, { notifications: true });
    const qc = new QueryClient();
    const { unmount } = render(
      <QueryClientProvider client={qc}>
        <UnreadCountProvider>
          <Reader name="bell" />
        </UnreadCountProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(badge.setAppBadge).toHaveBeenCalledWith(4));
    unmount();
    expect(badge.clearAppBadge).toHaveBeenCalled();
  });

  it('never badges a browser tab', async () => {
    const badge = grant({ installed: false });
    signIn(false, { notifications: true });
    renderReaders();
    expect(await screen.findByText('bell: 4')).toBeInTheDocument();
    expect(badge.setAppBadge).not.toHaveBeenCalled();
    expect(badge.clearAppBadge).not.toHaveBeenCalled();
  });

  it('swallows a refused badge and an engine without the API', async () => {
    const badge = grant();
    badge.setAppBadge.mockImplementation(() => Promise.reject(new Error('NotAllowedError')));
    signIn(false, { notifications: true });
    renderReaders();
    await waitFor(() => expect(badge.setAppBadge).toHaveBeenCalledWith(4));
    const nav = navigator as unknown as Record<string, unknown>;
    delete nav.setAppBadge;
    delete nav.clearAppBadge;
    expect(() => syncAppBadge(3)).not.toThrow();
    grant({ api: false });
    Object.defineProperty(navigator, 'setAppBadge', { value: () => Promise.resolve(), configurable: true });
    /* setAppBadge without clearAppBadge: the clear throws synchronously. */
    expect(() => syncAppBadge(0)).not.toThrow();
  });
});
