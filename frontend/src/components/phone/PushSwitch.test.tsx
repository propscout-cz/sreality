/* The account sheet's push switch (W5b, program § 3.6), in the real phone
 * frame over a fake push platform: each state the row can be in, what it says
 * when it cannot be toggled, and Apple's gesture rule end to end (the tap runs
 * requestPermission inside the click, then subscribe, then the PUT). */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Shell from '@/components/Shell';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import * as auth from '@/lib/auth';
import { PHONE_SHELL_QUERY } from '@/lib/phoneShell';
import { SERVER_KEY_B64, installPushFakes, type PushFakes } from '@/test/pushFakes';

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
vi.mock('@/lib/supabase', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/supabase')>()),
  isSupabaseConfigured: () => true,
}));

const LABEL = 'Oznámení na tomto iPhonu';
let fakes: PushFakes;
const originalMatchMedia = window.matchMedia;

function signIn(agendas: Record<string, boolean> | null = { notifications: true }, isAdmin = false) {
  vi.mocked(auth.useAuth).mockReturnValue({
    isAdmin,
    session: null,
    user: { id: 'user-1', email: 'operator@example.cz' },
    loading: false,
    agendas,
    signOut: vi.fn(),
  } as unknown as ReturnType<typeof auth.useAuth>);
}

beforeEach(() => {
  localStorage.clear();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  window.matchMedia = ((query: string) => ({
    matches: query === PHONE_SHELL_QUERY,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 0 });
  vi.mocked(api.getPushKey).mockResolvedValue({ public_key: SERVER_KEY_B64 });
  vi.mocked(api.putPushSubscription).mockImplementation(async () => {
    fakes.log.push('PUT');
  });
  vi.mocked(api.deletePushSubscription).mockImplementation(async () => {
    fakes.log.push('DELETE');
  });
  signIn();
});

afterEach(() => {
  fakes?.uninstall();
  window.matchMedia = originalMatchMedia;
  vi.clearAllMocks();
});

async function openSheet() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/feed']}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/feed" element={<p>feed body</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Účet' }));
  const sheet = await screen.findByRole('dialog', { name: 'Účet' });
  return within(sheet).findByRole('switch', { name: LABEL });
}

describe('the push switch in the account sheet', () => {
  it('sits first among the rows, a whole 44 px row, before Health and the desktop view', async () => {
    fakes = installPushFakes();
    signIn({ notifications: true }, true);
    const row = await openSheet();
    expect(row).toHaveClass('min-h-11', 'w-full');
    const sheet = screen.getByRole('dialog', { name: 'Účet' });
    const follows = (el: Element) =>
      expect(row.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    follows(within(sheet).getByRole('link', { name: 'Health' }));
    follows(within(sheet).getByRole('button', { name: /Desktopová verze/ }));
    follows(within(sheet).getByRole('button', { name: 'Odhlásit' }));
  });

  it('is off, and usable, with no subscription', async () => {
    fakes = installPushFakes();
    const row = await openSheet();
    await waitFor(() => expect(row).toBeEnabled());
    expect(row).toHaveAttribute('aria-checked', 'false');
    expect(row).not.toHaveAccessibleDescription();
  });

  it('is on when this iPhone holds a subscription', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    const row = await openSheet();
    await waitFor(() => expect(row).toHaveAttribute('aria-checked', 'true'));
    expect(row).toBeEnabled();
  });

  it('is disabled while it reads the device', async () => {
    fakes = installPushFakes();
    vi.mocked(api.getPushKey).mockReturnValue(new Promise(() => {}));
    const row = await openSheet();
    expect(row).toBeDisabled();
    expect(row).toHaveAttribute('aria-busy', 'true');
  });

  it.each([
    ['in a Safari tab', { installed: false, push: false }, 'Přidejte aplikaci na plochu'],
    ['after notifications were refused', { permission: 'denied' as const }, 'Povolte oznámení v Nastavení'],
    ['without Web Push', { push: false }, 'Oznámení zatím fungují jen na iPhonu'],
    ['on another phone', { apple: false }, 'Oznámení zatím fungují jen na iPhonu'],
  ])('is disabled %s, and says what would help', async (_name, platform, hint) => {
    fakes = installPushFakes(platform);
    const row = await openSheet();
    await waitFor(() => expect(row).toHaveAccessibleDescription(hint));
    expect(row).toBeDisabled();
    expect(row).toHaveAttribute('aria-checked', 'false');
  });

  it('is disabled while the server has no key', async () => {
    fakes = installPushFakes();
    vi.mocked(api.getPushKey).mockRejectedValue(new ApiError('push is not configured', 503, null));
    const row = await openSheet();
    await waitFor(() => expect(row).toHaveAccessibleDescription('Oznámení teď nejdou zapnout'));
    expect(row).toBeDisabled();
  });

  it('runs requestPermission inside the tap, then subscribe, then the PUT', async () => {
    fakes = installPushFakes();
    const row = await openSheet();
    await waitFor(() => expect(row).toBeEnabled());
    fakes.log.length = 0;

    act(() => {
      fireEvent.click(row);
      /* Still inside the click: the prompt was asked for before any await. */
      expect(fakes.log).toEqual(['requestPermission']);
    });
    await waitFor(() => expect(row).toHaveAttribute('aria-checked', 'true'));
    expect(fakes.log).toEqual(['requestPermission', 'subscribe', 'PUT']);
    expect(row).toBeEnabled();
  });

  it('switches off: unsubscribe, then DELETE', async () => {
    fakes = installPushFakes({ subscribed: true, permission: 'granted' });
    const row = await openSheet();
    await waitFor(() => expect(row).toHaveAttribute('aria-checked', 'true'));
    fakes.log.length = 0;
    fireEvent.click(row);
    await waitFor(() => expect(row).toHaveAttribute('aria-checked', 'false'));
    expect(fakes.log).toEqual(['unsubscribe', 'DELETE']);
  });

  it('says so, in brick, when the server refuses the subscription', async () => {
    fakes = installPushFakes();
    vi.mocked(api.putPushSubscription).mockRejectedValue(new ApiError('down', 0, null, 'network'));
    const row = await openSheet();
    await waitFor(() => expect(row).toBeEnabled());
    fireEvent.click(row);
    await waitFor(() =>
      expect(row).toHaveAccessibleDescription('Oznámení se nepodařilo zapnout. Zkuste to znovu.'),
    );
    expect(row).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText('Oznámení se nepodařilo zapnout. Zkuste to znovu.')).toHaveClass(
      'text-[var(--color-brick)]',
    );
  });

  it('is not drawn where the plan hides notifications', async () => {
    fakes = installPushFakes();
    signIn({ notifications: false });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/feed']}>
          <Routes>
            <Route element={<Shell />}>
              <Route path="/feed" element={<p>feed body</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Účet' }));
    const sheet = await screen.findByRole('dialog', { name: 'Účet' });
    expect(within(sheet).getByRole('button', { name: 'Odhlásit' })).toBeInTheDocument();
    expect(within(sheet).queryByRole('switch')).toBeNull();
    expect(api.getPushKey).not.toHaveBeenCalled();
  });
});
