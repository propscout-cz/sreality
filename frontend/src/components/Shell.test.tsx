/* Shell — error containment.
 *
 * The boundary used to sit at App level, ABOVE Shell, so any page crash also
 * removed the nav, the footer and the toast surface — while the fallback told
 * the user to "use the back button", which needs the nav to exist. The boundary
 * now wraps the route body only; App keeps a keyed last-resort net for what
 * renders outside it (TopBar, Footer, ToastViewport, the Explore-area modal).
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import Shell from './Shell';
import * as auth from '@/lib/auth';
import * as api from '@/lib/api';
import { writeLastBrowseSearch } from '@/lib/lastBrowseView';

vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getNotificationUnreadCount: vi.fn(),
}));
vi.mock('@/lib/supabase', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/supabase')>()),
  isSupabaseConfigured: () => true,
}));

function Boom(): never {
  throw new Error('page exploded');
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 0 });
  vi.mocked(auth.useAuth).mockReturnValue({
    isAdmin: true,
    session: null,
    user: null,
    loading: false,
    agendas: [],
  } as unknown as ReturnType<typeof auth.useAuth>);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderShellWith(element: React.ReactNode, at = '/browse') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/browse" element={element} />
            <Route path="/property/:propertyId" element={element} />
            <Route path="/new-dedup/pin-audit" element={element} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, qc };
}

describe('<Shell> error containment', () => {
  it('keeps the nav when the route body crashes', () => {
    renderShellWith(<Boom />);
    expect(screen.getByText('This page hit an error')).toBeInTheDocument();
    /* The nav is what the fallback's advice depends on. */
    expect(screen.getByRole('navigation')).toBeInTheDocument();
  });

  it('renders the route body normally when nothing throws', () => {
    renderShellWith(<p>page content</p>);
    expect(screen.getByText('page content')).toBeInTheDocument();
    expect(screen.queryByText('This page hit an error')).toBeNull();
  });
});

/* The unread badge sits INSIDE the Notifications link, so an aria-label on it
 * replaces the link's own name. The nav item has to stay findable by the word
 * the operator reads. */
describe('<Shell> notifications badge', () => {
  it('keeps the nav item named "Notifications" and adds the count to it', async () => {
    vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 5 });
    renderShellWith(<p>page content</p>);
    const link = await screen.findByRole('link', { name: /^Notifications/ });
    await waitFor(() =>
      expect(link).toHaveAccessibleName('Notifications 5 unread notifications'),
    );
  });
});

/* The nav's Browse entry is a way BACK to the view that was left: from another
 * page it carries that view's filters; on Browse itself the same click stays the
 * reset. */
describe('<Shell> Browse entry', () => {
  beforeEach(() => localStorage.clear());

  it('leads back to the remembered view from another page', () => {
    writeLastBrowseSearch('?price=3000000-6000000&sort=price_asc');
    renderShellWith(<p>page content</p>, '/property/7');
    expect(screen.getByRole('link', { name: 'Browse' })).toHaveAttribute(
      'href',
      '/browse?price=3000000-6000000&sort=price_asc',
    );
  });

  it('is the bare page on Browse itself, whatever is remembered', () => {
    writeLastBrowseSearch('?price=3000000-6000000');
    renderShellWith(<p>page content</p>, '/browse?price=3000000-6000000');
    expect(screen.getByRole('link', { name: 'Browse' })).toHaveAttribute('href', '/browse');
  });

  it('is the bare page when nothing is remembered', () => {
    renderShellWith(<p>page content</p>, '/property/7');
    expect(screen.getByRole('link', { name: 'Browse' })).toHaveAttribute('href', '/browse');
  });
});

/* The hidden set is the Admin menu's first item (ruling P33, 2026-10-27). Its
 * count left the Admin trigger with W1 (ruling Q7): the location gauge's stored
 * cells on Health are the one location count, so the trigger is its name alone. */
describe('<Shell> !AUDIT POLOH', () => {
  it('lives in the Admin menu, not the row, and the trigger carries no count', async () => {
    const user = userEvent.setup();
    renderShellWith(<p>page content</p>);
    const nav = screen.getByRole('navigation');
    const trigger = within(nav).getByRole('button', { name: /^Admin/ });
    expect(trigger).toHaveAccessibleName('Admin');
    expect(within(nav).queryByRole('link', { name: /AUDIT POLOH/ })).toBeNull();
    await user.click(trigger);
    const first = within(within(nav).getByRole('menu')).getAllByRole('menuitem')[0];
    expect(first).toHaveAccessibleName('!AUDIT POLOH');
    expect(first).toHaveAttribute('href', '/new-dedup/pin-audit');
  });

  /* Before the move the row entry lit on its own page and the trigger did not
   * (Dashboard's `end` keeps /new-dedup from prefix-matching). Now the page is
   * inside the menu, so the trigger lights there and only the audit row is
   * current. */
  it('lights the Admin trigger and only its own row on the audit page', async () => {
    const user = userEvent.setup();
    renderShellWith(<p>page content</p>, '/new-dedup/pin-audit');
    const nav = screen.getByRole('navigation');
    const trigger = within(nav).getByRole('button', { name: /^Admin/ });
    const classes = trigger.className.split(/\s+/);
    expect(classes).toContain('text-[var(--color-chrome-ink)]');
    expect(classes).not.toContain('text-[var(--color-chrome-ink-2)]');
    await user.click(trigger);
    expect(within(nav).getByRole('menuitem', { name: '!AUDIT POLOH' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('menuitem', { name: 'Dashboard' })).not.toHaveAttribute(
      'aria-current',
    );
  });
});

/* THE BAR OVERFLOWED ITS OWN ROW. At 1440px the header measured 1555px and the
 * last entries — Settings, and the account button after it — were clipped off
 * the right edge. The height is load-bearing (three Browse panes pin themselves
 * to 3.5rem), so the fix is to carry less: one "Admin" trigger with a section
 * per program, and the admin-only links inside it.
 *
 * jsdom measures no widths, so what is pinned here is the STRUCTURE the fix
 * rests on — one admin trigger, not four, and nothing lost on the way in.
 */
describe('<Shell> admin menu', () => {
  it('carries the admin surfaces under one trigger instead of four', async () => {
    const user = userEvent.setup();
    renderShellWith(<p>page content</p>);
    const nav = screen.getByRole('navigation');
    for (const gone of ['NEW DEDUP', 'AUTODEDUP', 'Settings']) {
      expect(within(nav).queryByRole('button', { name: new RegExp(`^${gone}`) })).toBeNull();
    }
    const trigger = within(nav).getByRole('button', { name: /^Admin/ });
    await user.click(trigger);
    /* Every section that used to be its own trigger, still reachable — and the
     * three admin-only links that used to spend a slot in the row. */
    for (const label of ['!AUDIT POLOH', 'Broker Review', 'Datasets', 'Groups', 'Residual', 'Soudce', 'Health']) {
      expect(within(nav).getByRole('menuitem', { name: label })).toBeInTheDocument();
    }
    /* Paused, not removed: a surface switched off is a different fact from one
     * that was deleted, so it stays visible and stays inert. */
    expect(within(nav).getByText('Outreach')).toHaveAttribute('aria-disabled', 'true');
  });

  it('lets the menu own its items through a labelled group, not a bare div', async () => {
    const user = userEvent.setup();
    renderShellWith(<p>page content</p>);
    const nav = screen.getByRole('navigation');
    await user.click(within(nav).getByRole('button', { name: /^Admin/ }));
    const menu = within(nav).getByRole('menu');
    /* A menu owns its menuitems directly or through a group. Sections went in to
     * keep four programs legible under one trigger; a plain wrapper between the
     * two would have bought that legibility with the menu's own structure. */
    const sections = within(menu).getAllByRole('group');
    expect(sections.length).toBeGreaterThan(1);
    expect(within(sections[0]).getAllByRole('menuitem').length).toBeGreaterThan(0);
    /* Each section is NAMED — and its visible heading is not read a second time. */
    for (const section of sections) {
      expect(section).toHaveAttribute('aria-label');
    }
  });

  /* The audit queue leads the menu (ruling P33), then Health: where the operator
   * starts when something is wrong, and where the bell's system alerts open. */
  it('opens with the audit queue, then Health', async () => {
    const user = userEvent.setup();
    renderShellWith(<p>page content</p>);
    const nav = screen.getByRole('navigation');
    await user.click(within(nav).getByRole('button', { name: /^Admin/ }));
    const [first, second] = within(within(nav).getByRole('menu')).getAllByRole('menuitem');
    expect(first).toHaveAccessibleName('!AUDIT POLOH');
    expect(first).toHaveAttribute('href', '/new-dedup/pin-audit');
    expect(second).toHaveAccessibleName('Health');
    expect(second).toHaveAttribute('href', '/health');
  });

  it('keeps the daily surfaces in the row itself', () => {
    renderShellWith(<p>page content</p>);
    const nav = screen.getByRole('navigation');
    for (const label of ['Browse', 'Pipeline', 'Estimations', 'Collections']) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument();
    }
  });
});

/* Ruling P34 (2026-10-27): the build line goes; the map tiles' attribution is a
 * licence condition, so it stays. */
describe('<Shell> footer', () => {
  it('keeps only the map-tile attribution', () => {
    renderShellWith(<p>page content</p>);
    const footer = screen.getByRole('contentinfo');
    for (const gone of ['read-only', 'U1a', 'Supabase']) {
      expect(footer).not.toHaveTextContent(gone);
    }
    expect(within(footer).getByRole('link', { name: 'OpenFreeMap' })).toHaveAttribute(
      'href',
      'https://openfreemap.org',
    );
    expect(within(footer).getByRole('link', { name: 'OpenStreetMap' })).toHaveAttribute(
      'href',
      'https://www.openstreetmap.org/copyright',
    );
  });
});
