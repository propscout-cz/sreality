/* Makléři on the phone (#1769): /brokers under the phone shell is the reduced
 * leaderboard — place, category, offer, metric — over the desktop's own
 * `fetchBrokerLeaderboard`, plus the name search shown in place of the roll. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import * as brokers from '@/lib/brokers';
import type { BrokerLeaderRow, BrokerPublic, LeaderboardParams } from '@/lib/brokers';
import { ROUTES } from '@/lib/routes';
import { expectNoNestedInteractive } from '@/test/a11y';

import Brokers from '../Brokers';
import { brokerInitials, placeLabelOf } from './PhoneBrokers';

vi.mock('@/lib/phoneShell', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/phoneShell')>()),
  usePhoneShell: () => true,
}));
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchBrokerLeaderboard: vi.fn(),
  searchBrokersByName: vi.fn(),
  searchBrokerFirms: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listBrokerMergeCandidates: vi.fn(),
}));

const leader = (id: number, extra: Partial<BrokerLeaderRow> = {}): BrokerLeaderRow => ({
  broker_id: id,
  display_name: `Makléř ${id}`,
  firm_id: null,
  firm_name: `Firma ${id}`,
  firm_domain: null,
  listing_count: 100 - id,
  property_count: 80 - id,
  active_listing_count: 60 - id,
  active_property_count: 50 - id,
  ...extra,
});

beforeEach(() => {
  vi.mocked(brokers.fetchBrokerLeaderboard).mockResolvedValue([leader(1), leader(2, { firm_name: null, firm_domain: null })]);
  vi.mocked(brokers.searchBrokersByName).mockResolvedValue([
    { broker_id: 42, display_name: 'Jana Kubíčková', firm_name: 'Domov 9', firm_domain: null, cz_active_property_count: 12 } as BrokerPublic,
  ]);
});

afterEach(() => vi.clearAllMocks());

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/brokers']}>
        <Brokers />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastParams = (): LeaderboardParams => {
  const calls = vi.mocked(brokers.fetchBrokerLeaderboard).mock.calls;
  return calls[calls.length - 1][0];
};

describe('<Brokers> on the phone', () => {
  it('opens on byty · prodej across the country, ranked by active properties', async () => {
    renderPage();
    const board = await screen.findByRole('list', { name: 'Žebříček makléřů' });
    expect(lastParams()).toMatchObject({
      regionIds: [],
      okresIds: [],
      obecIds: [],
      categoryMain: 'byt',
      categoryType: 'prodej',
      metric: 'active_property_count',
      limit: 100,
    });
    const rows = within(board).getAllByRole('link');
    expect(rows[0]).toHaveAttribute('href', ROUTES.brokerDetail.build({ id: 1 }));
    expect(rows[0].textContent).toContain('Makléř 1');
    expect(rows[0].textContent).toContain('49');
    expect(rows[1].textContent).toContain('nezávislý / neznámá kancelář');
    expectNoNestedInteractive(board);
    /* The desktop's extras stay on the desktop. */
    expect(screen.queryByText('Firma')).toBeNull();
    expect(api.listBrokerMergeCandidates).not.toHaveBeenCalled();
  });

  it('a category, offer or metric chip changes the request', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Žebříček makléřů' });

    await user.click(screen.getByRole('button', { name: 'Domy' }));
    await waitFor(() => expect(lastParams().categoryMain).toBe('dum'));
    expect(screen.getByRole('button', { name: 'Domy' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: 'Prodej' }));
    await waitFor(() => expect(lastParams().categoryType).toBeNull());

    await user.click(screen.getByRole('button', { name: 'Inzeráty' }));
    await waitFor(() => expect(lastParams().metric).toBe('listing_count'));
    const first = within(await screen.findByRole('list', { name: 'Žebříček makléřů' })).getAllByRole('link')[0];
    await waitFor(() => expect(first.textContent).toContain('99'));
  });

  it('opens the place picker in a sheet', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Žebříček makléřů' });
    await user.click(screen.getByRole('button', { name: 'Místo: Celá ČR' }));
    const sheet = screen.getByRole('dialog', { name: 'Místo' });
    expect(within(sheet).getByRole('textbox', { name: 'Místo' })).toBeInTheDocument();
  });

  it('searching by name swaps the roll for the results, clearing brings it back as it was', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Žebříček makléřů' });
    await user.click(screen.getByRole('button', { name: 'Domy' }));
    await user.type(screen.getByRole('searchbox', { name: 'Hledat makléře podle jména' }), 'Kub');

    const results = await screen.findByRole('list', { name: 'Nalezení makléři' });
    expect(brokers.searchBrokersByName).toHaveBeenCalledWith('Kub');
    const hit = within(results).getByRole('link');
    expect(hit).toHaveAttribute('href', ROUTES.brokerDetail.build({ id: 42 }));
    expect(hit.textContent).toContain('JK');
    expect(screen.queryByRole('list', { name: 'Žebříček makléřů' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Vymazat hledání' }));
    expect(await screen.findByRole('list', { name: 'Žebříček makléřů' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Domy' })).toHaveAttribute('aria-pressed', 'true');
    expect(lastParams().categoryMain).toBe('dum');
  });

  it('says a failed leaderboard failed', async () => {
    vi.mocked(brokers.fetchBrokerLeaderboard).mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText('Žebříček se nepodařilo načíst.')).toBeInTheDocument();
  });
});

describe('phone broker helpers', () => {
  it('labels only the chips that scope the board, compactly', () => {
    expect(placeLabelOf([])).toBe('Celá ČR');
    expect(placeLabelOf([{ name: 'Praha', context: null, level: 'obec', id: 554782 }])).toBe('Praha');
    expect(
      placeLabelOf([
        { name: 'Praha', context: null, level: 'obec', id: 554782 },
        { name: 'Brno', context: null, level: 'obec', id: 582786 },
        { name: 'Žižkov', context: 'Praha', level: 'cast_obce', id: 1 },
      ]),
    ).toBe('Praha +1');
  });

  it('draws initials from the first and last word of the name, degrees aside', () => {
    expect(brokerInitials('Jana Kubíčková')).toBe('JK');
    expect(brokerInitials('Ing. Petr van der Berg')).toBe('PB');
    expect(brokerInitials('Petr Novák, MBA')).toBe('PN');
    expect(brokerInitials('Bc.')).toBe('?');
    expect(brokerInitials(null)).toBe('?');
  });
});
