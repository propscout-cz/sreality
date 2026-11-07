/* Prohlídky (the Phone app's sixth tab): my viewings one day at a time, in time
 * order, each opening its property; the arrows, a swipe and the day jumps flip
 * the day either way, the past as reachable as what is ahead. jsdom lays nothing
 * out, so the slide and the date wheel are browser checks; the wiring is pinned here. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { usePhoneHeaderAside } from '@/components/phone/PhoneShell';
import * as api from '@/lib/api';
import * as queries from '@/lib/queries';
import type { Note } from '@/lib/types';

import PhoneViewings from './PhoneViewings';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listViewings: vi.fn(),
}));
vi.mock('@/components/phone/PhoneShell', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/phone/PhoneShell')>()),
  usePhoneHeaderAside: vi.fn(),
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
const PAST = 'text-[var(--color-ink-3)]';
const LEAD = 'bg-[var(--color-copper)]';
const lastAside = (): string | null => {
  const calls = vi.mocked(usePhoneHeaderAside).mock.calls;
  return calls[calls.length - 1][0];
};
const timeOf = (text: string) => screen.getByText(text, { selector: 'time' });

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
    expect(timeOf('09:00')).toHaveClass(PAST);
    expect(timeOf('14:30')).not.toHaveClass(PAST);
    // The header counts what the rows show as ahead: 14:30 today and the 27th.
    expect(lastAside()).toBe('2 v plánu');
  });

  it('steps a day either way and offers the nearest days that hold a viewing', async () => {
    renderAt();
    await screen.findByText('Seifertova, Praha 3 – Žižkov');
    expect(screen.queryByRole('button', { name: 'Dnes' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Vybrat den')).not.toHaveAttribute('min');
    fireEvent.click(screen.getByRole('button', { name: 'Předchozí den' }));
    expect(screen.getByText('Včera')).toBeInTheDocument();
    expect(screen.getByTestId('search')).toHaveTextContent('?day=2026-10-22');
    expect(screen.getByText('Tento den nic. Nejbližší prohlídka je níže.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Další prohlídka · pá 23\. 10\./ })).toHaveClass(LEAD);
    const back = screen.getByRole('button', { name: 'Předchozí prohlídka · út 20. 10.' });
    expect(back).not.toHaveClass(LEAD);
    fireEvent.click(back);
    expect(await screen.findByText('Krátká, Brno')).toBeInTheDocument();
    expect(rows()).toEqual(['/property/45']);
    // A day gone by: every viewing on it reads as past, and no now rule.
    expect(timeOf('11:00')).toHaveClass(PAST);
    expect(screen.getAllByRole('listitem', { hidden: true }).every((li) => li.querySelector('a'))).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Další prohlídka: pá 23. 10.' }));
    expect(screen.getByTestId('search')).toHaveTextContent(/^$/);
    fireEvent.click(screen.getByRole('button', { name: 'Další prohlídka: út 27. 10.' }));
    expect(await screen.findByText('Dlouhá, Praha 1')).toBeInTheDocument();
    expect(timeOf('10:00')).not.toHaveClass(PAST);
    fireEvent.click(screen.getByRole('button', { name: 'Dnes' }));
    expect(screen.getByTestId('search')).toHaveTextContent(/^$/);
  });

  it('with nothing ahead, the last viewing is the way back', async () => {
    vi.setSystemTime(new Date('2026-11-07T09:00:00Z'));
    renderAt();
    expect(await screen.findByText('Tento den nic a dál už žádná naplánovaná.')).toBeInTheDocument();
    expect(lastAside()).toBe('0 v plánu');
    expect(screen.queryByRole('button', { name: /^Další prohlídka/ })).not.toBeInTheDocument();
    const back = screen.getByRole('button', { name: 'Předchozí prohlídka · út 27. 10.' });
    expect(back).toHaveClass(LEAD);
    fireEvent.click(back);
    expect(await screen.findByText('Dlouhá, Praha 1')).toBeInTheDocument();
    expect(screen.getByTestId('search')).toHaveTextContent('?day=2026-10-27');
    expect(timeOf('10:00')).toHaveClass(PAST);
    fireEvent.click(screen.getByRole('button', { name: 'Předchozí prohlídka: pá 23. 10.' }));
    expect(await screen.findByText('Seifertova, Praha 3 – Žižkov')).toBeInTheDocument();
  });

  it('flips the day on a sideways swipe, not on a scroll', async () => {
    renderAt();
    await screen.findByText('Seifertova, Praha 3 – Žižkov');
    // The day's page, which outlives the rows a flip replaces.
    const page = screen.getByTestId('viewings-day');
    const swipe = (dx: number, dy: number) => {
      fireEvent.touchStart(page, { touches: [{ clientX: 200, clientY: 300 }] });
      fireEvent.touchEnd(page, { changedTouches: [{ clientX: 200 + dx, clientY: 300 + dy }] });
    };
    swipe(-30, 200);
    expect(screen.getByText('Dnes')).toBeInTheDocument();
    swipe(-120, 10);
    expect(screen.getByText('Zítra')).toBeInTheDocument();
    swipe(120, 10);
    swipe(120, 10);
    expect(screen.getByText('Včera')).toBeInTheDocument();
  });

  it('opens on today when the URL names no real day', async () => {
    renderAt('/viewings?day=2026-00-10');
    expect(await screen.findByText('Seifertova, Praha 3 – Žižkov')).toBeInTheDocument();
    expect(screen.getByText('Dnes')).toBeInTheDocument();
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
