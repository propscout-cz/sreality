/* The "Sign in" affordance is a destination, and the component's own comment
 * always called it a link — it was a <button> calling navigate('/login'). */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import AccountMenu from './AccountMenu';
import { fetchAgentRunOptions } from '@/lib/api';
import * as auth from '@/lib/auth';
import { ROUTES } from '@/lib/routes';
import { setScheme, setTheme } from '@/lib/theme';
import { ThemeToggle } from '@/pages/Settings';

vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  fetchAgentRunOptions: vi.fn(),
}));

vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));

const REFUSED = { start: { allowed: false, reason: 'no' }, queue: null, agents: [], limit: null };
const ALLOWED = { start: { allowed: true, reason: null }, queue: 'open' as const, agents: [], limit: { limit_usd: 10, spent_today_usd: 1 } };

function renderAs(user: unknown, options: Awaited<ReturnType<typeof fetchAgentRunOptions>> = REFUSED) {
  vi.mocked(fetchAgentRunOptions).mockResolvedValue(options);
  vi.mocked(auth.useAuth).mockReturnValue({
    user,
    loading: false,
    isAdmin: false,
    session: null,
    signOut: vi.fn(),
  } as unknown as ReturnType<typeof auth.useAuth>);
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <AccountMenu />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('<AccountMenu>', () => {
  it('renders Sign in as an anchor to the login route', () => {
    renderAs(null);
    const link = screen.getByRole('link', { name: 'Sign in' });
    expect(link).toHaveAttribute('href', ROUTES.login.build());
  });

  it('does not render Sign in as a button', () => {
    renderAs(null);
    expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull();
  });
});

describe('<AccountMenu> agent limit', () => {
  const openMenu = () => fireEvent.click(screen.getByRole('button', { name: 'a@example.cz' }));

  it('offers no agent control to an account that cannot use agents', async () => {
    renderAs({ email: 'a@example.cz' });
    await waitFor(() => expect(fetchAgentRunOptions).toHaveBeenCalled());
    openMenu();
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('menuitem', { name: 'Agent spending limit' })).toBeNull();
  });

  it('offers none while the answer is unknown', () => {
    renderAs({ email: 'a@example.cz' }, ALLOWED);
    openMenu();
    expect(screen.queryByRole('menuitem', { name: 'Agent spending limit' })).toBeNull();
  });

  it('opens the limit from the menu for an account that may run agents', async () => {
    renderAs({ email: 'a@example.cz' }, ALLOWED);
    openMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Agent spending limit' }));
    expect(await screen.findByRole('dialog', { name: 'Agent spending limit' })).toBeInTheDocument();
    expect(screen.queryByRole('menu')).toBeNull();
    expect(await screen.findByRole('textbox', { name: /USD per day/ })).toHaveValue('10');
  });
});

describe('<AccountMenu> appearance', () => {
  afterEach(() => {
    act(() => {
      setScheme('med');
      setTheme('light');
    });
    document.documentElement.removeAttribute('data-scheme');
    document.documentElement.removeAttribute('data-theme');
    localStorage.clear();
  });

  it('picks a colour scheme for this device', () => {
    renderAs({ email: 'a@example.cz' });
    fireEvent.click(screen.getByRole('button', { name: 'a@example.cz' }));
    expect(screen.getByRole('menuitemradio', { name: 'Měď' })).toHaveAttribute('aria-checked', 'true');

    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Grafit' }));
    expect(document.documentElement).toHaveAttribute('data-scheme', 'grafit');
    expect(localStorage.getItem('sreality.scheme')).toBe('grafit');
    expect(screen.getByRole('menuitemradio', { name: 'Grafit' })).toHaveAttribute('aria-checked', 'true');
  });

  /* One store behind every picker: Settings follows the menu without a reload. */
  it('keeps Settings in step with the menu', () => {
    renderAs({ email: 'a@example.cz' });
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole('button', { name: 'a@example.cz' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Kobalt' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Dark' }));
    expect(screen.getByRole('button', { name: 'Kobalt', pressed: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dark', pressed: true })).toBeInTheDocument();
  });

  it('sets light or dark beside the scheme', () => {
    renderAs({ email: 'a@example.cz' });
    fireEvent.click(screen.getByRole('button', { name: 'a@example.cz' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Dark' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
  });

  /* The joined bar (operator ruling 2026-10-27, system.md § Option groups) — SegmentedBar
   * itself, not a copy of its classes — its menuitemradio roles kept inside the menu: no
   * raised paper tile with a shadow. */
  it('draws light or dark as the joined bar, the chosen segment filled copper', () => {
    renderAs({ email: 'a@example.cz' });
    fireEvent.click(screen.getByRole('button', { name: 'a@example.cz' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Dark' }));
    const dark = screen.getByRole('menuitemradio', { name: 'Dark' });
    expect(dark).toHaveAttribute('aria-checked', 'true');
    expect(dark).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    for (const off of ['shadow-sm', 'bg-[var(--color-paper-3)]', 'font-medium']) expect(dark).not.toHaveClass(off);
    expect(screen.getByRole('menuitemradio', { name: 'Light' })).toHaveClass('text-[var(--color-ink-3)]');
    expect(dark.parentElement).toHaveClass('grid', 'border', 'border-[var(--color-rule)]', 'bg-[var(--color-paper-2)]', 'p-0.5');
    /* SegmentedBar's own root: the named group, its equal columns set inline. */
    expect(dark.parentElement).toBe(screen.getByRole('group', { name: 'Light or dark' }));
    expect(dark.parentElement).toHaveStyle({ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' });
    for (const m of screen.getAllByRole('menuitemradio', { name: /^(Light|Dark|System)$/ })) {
      expect(m).not.toHaveAttribute('aria-pressed');
      expect(m).toHaveClass('min-w-0', 'text-ellipsis');
    }
  });
});
