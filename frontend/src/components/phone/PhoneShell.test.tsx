/* The phone frame (#1766): Shell draws it under 768 px unless this device asked
 * for the desktop view; the routes inside are unchanged. The header is exactly
 * h-14 (the desktop panes pin to 3.5 rem), the tab bar carries the five areas
 * (W1's four and Odkaz), a page outside the tab roots gets a back affordance,
 * the header's bell carries the unread count to Notifications (W5a, ruling R4),
 * and the account sheet holds the way to the desktop view and, for an admin,
 * the phone's one way to Health. */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Shell from '@/components/Shell';
import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
import * as pinAudit from '@/lib/pinAudit';
import { DESKTOP_VIEW_KEY, PHONE_SHELL_QUERY } from '@/lib/phoneShell';

import HomeRedirect from './HomeRedirect';

vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getNotificationUnreadCount: vi.fn(),
}));
vi.mock('@/lib/pinAudit', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/pinAudit')>()),
  fetchPinAuditTotal: vi.fn(),
}));
vi.mock('@/lib/supabase', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/supabase')>()),
  isSupabaseConfigured: () => true,
}));

const originalMatchMedia = window.matchMedia;
function setNarrow(narrow: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: query === PHONE_SHELL_QUERY ? narrow : false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  localStorage.clear();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 0 });
  vi.mocked(pinAudit.fetchPinAuditTotal).mockResolvedValue(0);
  vi.mocked(auth.useAuth).mockReturnValue({
    isAdmin: true,
    session: null,
    user: { email: 'operator@example.cz' },
    loading: false,
    agendas: null,
    signOut: vi.fn(),
  } as unknown as ReturnType<typeof auth.useAuth>);
});

afterEach(() => {
  window.matchMedia = originalMatchMedia;
  vi.restoreAllMocks();
});

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<HomeRedirect />} />
            <Route path="/feed" element={<p>feed body</p>} />
            <Route path="/browse" element={<p>browse body</p>} />
            <Route path="/pipeline" element={<p>pipeline body</p>} />
            <Route path="/health" element={<p>health body</p>} />
            <Route path="/notifications" element={<p>notifications body</p>} />
            <Route path="/property/:propertyId" element={<p>property body</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('phone frame', () => {
  it('draws a 3.5 rem header and five tabs on a narrow screen', () => {
    setNarrow(true);
    renderAt('/feed');
    expect(screen.getByText('feed body')).toBeInTheDocument();
    const header = screen.getByRole('banner');
    expect(header.firstElementChild).toHaveClass('h-14');
    expect(within(header).getByRole('heading', { name: 'Nové' })).toBeInTheDocument();
    const tabs = within(screen.getByRole('navigation', { name: 'Hlavní navigace' })).getAllByRole('link');
    expect(tabs.map((t) => t.textContent)).toEqual(['Nové', 'Pipeline', 'Sbírky', 'Makléři', 'Odkaz']);
    expect(tabs[4]).toHaveAttribute('href', '/link');
    expect(tabs[0]).toHaveAttribute('aria-current', 'page');
    /* No back affordance on a tab root; no desktop nav or footer. */
    expect(screen.queryByRole('button', { name: /Nové$/ })).toBeNull();
    expect(screen.queryByRole('contentinfo')).toBeNull();
  });

  it('gives a page outside the tab roots a back button and keeps its tab marked', () => {
    setNarrow(true);
    renderAt('/property/304388');
    expect(screen.getByText('property body')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nové' })).toBeInTheDocument();
    const feedTab = within(screen.getByRole('navigation', { name: 'Hlavní navigace' })).getByRole('link', {
      name: 'Nové',
    });
    expect(feedTab).toHaveAttribute('aria-current', 'true');
  });

  it('hides the tab bar while a text field has focus', () => {
    setNarrow(true);
    renderAt('/feed');
    const nav = screen.getByRole('navigation', { name: 'Hlavní navigace' });
    const input = document.createElement('input');
    document.body.appendChild(input);
    act(() => {
      input.focus();
    });
    expect(nav).not.toBeVisible();
    act(() => {
      fireEvent.focusIn(document.body);
    });
    expect(nav).toBeVisible();
    input.remove();
  });

  it('opens the account sheet with the theme, the desktop view and sign-out', async () => {
    setNarrow(true);
    renderAt('/feed');
    await userEvent.click(screen.getByRole('button', { name: 'Účet' }));
    const sheet = await screen.findByRole('dialog', { name: 'Účet' });
    expect(within(sheet).getByText('operator@example.cz')).toBeInTheDocument();
    expect(within(sheet).getByRole('radio', { name: 'Světlý' })).toBeInTheDocument();
    expect(within(sheet).getByRole('button', { name: /Desktopová verze/ })).toBeInTheDocument();
    expect(within(sheet).getByRole('button', { name: 'Odhlásit' })).toBeInTheDocument();
  });

  /* The phone has no Admin menu, and system alerts never reach the bell (ruling
   * R2b): the sheet's Health row is the one way to Health, and only an admin
   * sees it (the page is admin-only). */
  it('gives an admin a Health row that closes the sheet and opens Health', async () => {
    setNarrow(true);
    renderAt('/feed');
    await userEvent.click(screen.getByRole('button', { name: 'Účet' }));
    const sheet = await screen.findByRole('dialog', { name: 'Účet' });
    const health = within(sheet).getByRole('link', { name: 'Health' });
    expect(health).toHaveAttribute('href', '/health');
    await userEvent.click(health);
    expect(await screen.findByText('health body')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'Účet' })).toBeNull();
  });

  it('shows no Health row to a non-admin', async () => {
    vi.mocked(auth.useAuth).mockReturnValue({
      isAdmin: false,
      session: null,
      user: { email: 'operator@example.cz' },
      loading: false,
      agendas: null,
      signOut: vi.fn(),
    } as unknown as ReturnType<typeof auth.useAuth>);
    setNarrow(true);
    renderAt('/feed');
    await userEvent.click(screen.getByRole('button', { name: 'Účet' }));
    const sheet = await screen.findByRole('dialog', { name: 'Účet' });
    expect(within(sheet).queryByRole('link', { name: 'Health' })).toBeNull();
    expect(within(sheet).getByRole('button', { name: 'Odhlásit' })).toBeInTheDocument();
  });

  it('starts / on the feed on a phone and on Browse on a desktop', () => {
    setNarrow(true);
    const { unmount } = renderAt('/');
    expect(screen.getByText('feed body')).toBeInTheDocument();
    unmount();
    setNarrow(false);
    renderAt('/');
    expect(screen.getByText('browse body')).toBeInTheDocument();
  });
});

/* The bell (W5a, ruling R4): Shell's one unread count beside Účet, opening
 * the feed, hidden with the feed where the plan hides it. */
describe('phone bell', () => {
  const asUser = (agendas: Record<string, boolean> | null) =>
    vi.mocked(auth.useAuth).mockReturnValue({
      isAdmin: false,
      session: null,
      user: { email: 'operator@example.cz' },
      loading: false,
      agendas,
      signOut: vi.fn(),
    } as unknown as ReturnType<typeof auth.useAuth>);

  it('sits beside the account button and names the unread count', async () => {
    vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 3 });
    setNarrow(true);
    renderAt('/feed');
    const header = screen.getByRole('banner');
    const bell = await within(header).findByRole('link', { name: 'Upozornění: 3 nepřečtená' });
    expect(bell).toHaveAttribute('href', '/notifications');
    expect(bell).toHaveTextContent('3');
    expect(bell).toHaveClass('h-11', 'w-11');
    expect(bell.nextElementSibling).toBe(within(header).getByRole('button', { name: 'Účet' }));
  });

  it('caps the tally at 99+ and keeps the whole number in its name', async () => {
    vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 140 });
    setNarrow(true);
    renderAt('/feed');
    const bell = await screen.findByRole('link', { name: 'Upozornění: 140 nepřečtených' });
    expect(bell).toHaveTextContent('99+');
  });

  it('draws no tally when nothing is unread', async () => {
    setNarrow(true);
    renderAt('/feed');
    await waitFor(() => expect(api.getNotificationUnreadCount).toHaveBeenCalled());
    const bell = screen.getByRole('link', { name: 'Upozornění' });
    expect(bell.textContent).toBe('');
  });

  it('is not drawn, and polls nothing, where the plan hides notifications', () => {
    vi.mocked(api.getNotificationUnreadCount).mockClear();
    asUser({ notifications: false });
    setNarrow(true);
    renderAt('/feed');
    expect(screen.queryByRole('link', { name: /^Upozornění/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Účet' })).toBeInTheDocument();
    expect(api.getNotificationUnreadCount).not.toHaveBeenCalled();
  });

  it('opens Notifications under the back affordance, the tab it came from still marked', async () => {
    vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 2 });
    asUser({ notifications: true });
    setNarrow(true);
    renderAt('/pipeline');
    await userEvent.click(await screen.findByRole('link', { name: 'Upozornění: 2 nepřečtená' }));
    expect(await screen.findByText('notifications body')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pipeline' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /^Upozornění/ })).toHaveAttribute('aria-current', 'page');
    const pipelineTab = within(screen.getByRole('navigation', { name: 'Hlavní navigace' })).getByRole('link', {
      name: 'Pipeline',
    });
    expect(pipelineTab).toHaveAttribute('aria-current', 'true');
  });

  /* One observer serves both frames: the desktop badge reads the same count. */
  it('reads the count the desktop badge reads, from one request', async () => {
    vi.mocked(api.getNotificationUnreadCount).mockClear();
    vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 5 });
    setNarrow(false);
    renderAt('/browse');
    const link = await screen.findByRole('link', { name: /^Notifications/ });
    await waitFor(() => expect(link).toHaveAccessibleName('Notifications 5 unread notifications'));
    expect(api.getNotificationUnreadCount).toHaveBeenCalledTimes(1);
  });
});

describe('desktop frame', () => {
  it('is untouched on a wide screen: the top nav and the footer, no tab bar', () => {
    setNarrow(false);
    renderAt('/browse');
    expect(screen.queryByRole('navigation', { name: 'Hlavní navigace' })).toBeNull();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
  });

  it('is drawn on a narrow screen when this device asked for the desktop view', () => {
    setNarrow(true);
    localStorage.setItem(DESKTOP_VIEW_KEY, '1');
    renderAt('/browse');
    expect(screen.queryByRole('navigation', { name: 'Hlavní navigace' })).toBeNull();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
  });
});
