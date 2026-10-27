/* Prohlídky (the Phone app's sixth tab): my viewings one day at a time, in time
 * order, each opening its property; the arrows, a swipe and the day jumps flip
 * the day, Nadcházející never steps before today. jsdom lays nothing out, so
 * the slide and the date wheel are browser checks; the wiring is pinned here. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import * as queries from '@/lib/queries';
import type { Note } from '@/lib/types';

import PhoneViewings from './PhoneViewings';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listViewings: vi.fn(),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchPropertySummaries: vi.fn(),
  fetchListingCovers: vi.fn(),
  fetchPipelineMembers: vi.fn(),
  fetchPipelineStages: vi.fn(),
}));

const viewing = (id: number, property_id: number, viewing_at: string, body = ''): Note => ({
  id, property_id, body, viewing_at, created_at: '2026-10-01T00:00:00Z', updated_at: null, shared: false,
  owner_email: null,
});

/* "Now" is Friday 2026-10-23, 10:00 in Prague. */
const VIEWINGS: Note[] = [
  viewing(4, 45, '2026-10-20T09:00:00Z'),
  viewing(1, 42, '2026-10-23T07:00:00Z', 'Zazvonit u Nováků.'),
  viewing(2, 43, '2026-10-23T12:30:00Z'),
  viewing(3, 44, '2026-10-27T09:00:00Z'),
];

const summary = (property_id: number, display_label: string): queries.PropertySummary => ({
  property_id, listing_id: property_id * 10, is_active: true, category_main: 'byt', category_type: 'prodej',
  disposition: '2+kk', subtype: null, area_m2: 54, price_czk: 6_500_000, display_label,
});

function Where() {
  const { search } = useLocation();
  return <output data-testid="search">{search}</output>;
}

function renderAt(path = '/viewings') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/viewings"
            element={
              <>
                <PhoneViewings />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const rows = () => screen.getAllByRole('link').map((a) => a.getAttribute('href'));

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-23T08:00:00Z'));
  vi.mocked(api.listViewings).mockResolvedValue({ data: VIEWINGS });
  vi.mocked(queries.fetchPropertySummaries).mockResolvedValue(
    new Map([
      [42, summary(42, 'Seifertova, Praha 3 – Žižkov')],
      [43, summary(43, 'Lesní 3, Beroun')],
      [44, summary(44, 'Dlouhá, Praha 1')],
      [45, summary(45, 'Krátká, Brno')],
    ]),
  );
  vi.mocked(queries.fetchListingCovers).mockResolvedValue(new Map() as never);
  vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map() as never);
  vi.mocked(queries.fetchPipelineStages).mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('<PhoneViewings>', () => {
  it('opens on today: its viewings in time order, each opening its property, the now rule between', async () => {
    renderAt();
    expect(await screen.findByText('Seifertova, Praha 3 – Žižkov')).toBeInTheDocument();
    expect(screen.getByText('Dnes')).toBeInTheDocument();
    expect(rows()).toEqual(['/property/42', '/property/43']);
    const [first, second] = screen.getAllByRole('listitem').filter((li) => li.querySelector('a'));
    expect(within(first).getByText('09:00')).toBeInTheDocument();
    expect(within(first).getByText('Zazvonit u Nováků.')).toBeInTheDocument();
    expect(within(second).getByText('14:30')).toBeInTheDocument();
    expect(within(second).getByText('2+kk · 54 m² · 6 500 000 Kč')).toBeInTheDocument();
    // The now rule (10:00) sits between the morning viewing and the afternoon one.
    const items = screen.getAllByRole('listitem', { hidden: true });
    expect(items.map((li) => (li.querySelector('a') ? 'row' : 'now'))).toEqual(['row', 'now', 'row']);
  });

  it('never steps before today while upcoming, and offers the next day that holds a viewing', async () => {
    renderAt();
    await screen.findByText('Seifertova, Praha 3 – Žižkov');
    expect(screen.getByRole('button', { name: 'Předchozí den' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Další den' }));
    expect(screen.getByText('Zítra')).toBeInTheDocument();
    expect(screen.getByTestId('search')).toHaveTextContent('?day=2026-10-24');
    expect(screen.getByText('Žádná prohlídka')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Další prohlídka · út 27\. 10\./ }));
    expect(await screen.findByText('Dlouhá, Praha 1')).toBeInTheDocument();
    expect(rows()).toEqual(['/property/44']);
    fireEvent.click(screen.getByRole('button', { name: 'Dnes' }));
    expect(screen.getByTestId('search')).toHaveTextContent(/^$/);
  });

  it('under Vše steps into the past and jumps to the previous viewing', async () => {
    renderAt();
    await screen.findByText('Seifertova, Praha 3 – Žižkov');
    fireEvent.click(screen.getByRole('button', { name: 'Vše' }));
    expect(screen.getByRole('button', { name: 'Předchozí den' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Předchozí prohlídka: út 20. 10.' }));
    expect(await screen.findByText('Krátká, Brno')).toBeInTheDocument();
    expect(screen.getByTestId('search')).toHaveTextContent('?scope=all&day=2026-10-20');
    // Back to upcoming: a past day cannot stay open.
    fireEvent.click(screen.getByRole('button', { name: 'Nadcházející' }));
    expect(await screen.findByText('Seifertova, Praha 3 – Žižkov')).toBeInTheDocument();
    expect(screen.getByTestId('search')).toHaveTextContent(/^$/);
  });

  /* Operator ruling 2026-10-27 (system.md § Option groups): the scope is the one
   * joined bar in its phone tone — chosen = ink on the inset well, never copper. */
  it('picks the scope on the phone-tone joined bar', async () => {
    renderAt();
    await screen.findByText('Seifertova, Praha 3 – Žižkov');
    const group = screen.getByRole('group', { name: 'Které prohlídky' });
    const buttons = within(group).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['Nadcházející', 'Vše']);
    expect(Array.from(group.children)).toEqual(buttons);
    expect(group).toHaveClass('border', 'border-[var(--color-rule-strong)]');
    for (const b of buttons) {
      expect(b).not.toHaveClass('border-l');
      expect(b.className).not.toMatch(/copper/);
    }
    expect(buttons[0]).toHaveAttribute('aria-pressed', 'true');
    expect(buttons[0]).toHaveClass('bg-[var(--color-inset)]', 'text-[var(--color-ink)]');
    expect(buttons[1]).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(buttons[1]);
    expect(screen.getByTestId('search')).toHaveTextContent('?scope=all');
    expect(buttons[1]).toHaveAttribute('aria-pressed', 'true');
    // A tap on the lit segment keeps it: the scope is never "none".
    fireEvent.click(buttons[1]);
    expect(screen.getByTestId('search')).toHaveTextContent('?scope=all');
    expect(buttons[1]).toHaveAttribute('aria-pressed', 'true');
  });

  it('flips the day on a sideways swipe, not on a scroll', async () => {
    renderAt();
    const row = await screen.findByText('Seifertova, Praha 3 – Žižkov');
    const swipe = (dx: number, dy: number) => {
      fireEvent.touchStart(row, { touches: [{ clientX: 200, clientY: 300 }] });
      fireEvent.touchEnd(row, { changedTouches: [{ clientX: 200 + dx, clientY: 300 + dy }] });
    };
    swipe(-30, 200);
    expect(screen.getByText('Dnes')).toBeInTheDocument();
    swipe(-120, 10);
    expect(screen.getByText('Zítra')).toBeInTheDocument();
  });

  it('says where a viewing is booked when there is none', async () => {
    vi.mocked(api.listViewings).mockResolvedValue({ data: [] });
    renderAt();
    expect(
      await screen.findByText('Prohlídku naplánujete na stránce nemovitosti v sekci Poznámky.'),
    ).toBeInTheDocument();
    expect(queries.fetchPropertySummaries).not.toHaveBeenCalled();
  });

  it('says a failed read failed and reads again', async () => {
    vi.mocked(api.listViewings).mockRejectedValueOnce(new Error('down'));
    renderAt();
    fireEvent.click(await screen.findByRole('button', { name: 'Zkusit znovu' }));
    await waitFor(() => expect(api.listViewings).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Seifertova, Praha 3 – Žižkov')).toBeInTheDocument();
  });
});
