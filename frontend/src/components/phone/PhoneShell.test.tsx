/* The phone frame (#1766): Shell draws it under 768 px unless this device asked
 * for the desktop view; the routes inside are unchanged. The header is exactly
 * h-14 (the desktop panes pin to 3.5 rem), the tab bar carries the five areas
 * (W1's four and Odkaz), a page outside the tab roots gets a back affordance,
 * and the account sheet holds the way to the desktop view. */
import { act, fireEvent, render, screen, within } from '@testing-library/react';
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
  vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({
    watchdog: 0,
    collection_monitor: 0,
    system_health: 0,
    total: 0,
    unread_count: 0,
  });
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
