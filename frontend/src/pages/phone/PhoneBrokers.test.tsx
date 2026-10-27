/* Makléři on the phone (#1769): /brokers under the phone shell is the reduced
 * leaderboard — place, category, offer, metric — over the desktop's own
 * `fetchBrokerLeaderboard`, plus the name search shown in place of the roll. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { placeLabelOf } from '@/components/phone/placeLabel';
import * as api from '@/lib/api';
import * as brokers from '@/lib/brokers';
import type { BrokerLeaderRow, BrokerPublic, LeaderboardParams } from '@/lib/brokers';
import { ROUTES } from '@/lib/routes';
import { expectNoNestedInteractive } from '@/test/a11y';

import Brokers from '../Brokers';
import { brokerInitials, metricUnitHead } from './PhoneBrokers';

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
      districts: [],
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
    expect(
      within(sheet).getByText('Kraj, okres, obec, část obce nebo ulice; bez místa platí celá ČR.'),
    ).toBeInTheDocument();
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

/* Operator ruling 2026-10-27 (system.md § Option groups): a pick-one is ONE
 * joined bar, on the phone's own screens its phone tone — the chosen segment
 * ink on the inset well, never copper (colour here only ever means a stage). */
describe('<Brokers> on the phone: one joined bar per pick-one', () => {
  const bar = (name: string) => {
    const group = screen.getByRole('group', { name });
    const buttons = within(group).getAllByRole('button');
    /* One track of adjoining segments, not a row of separate hairline chips. */
    expect(Array.from(group.children)).toEqual(buttons);
    expect(group).toHaveClass('border', 'border-[var(--color-rule-strong)]');
    for (const b of buttons) {
      expect(b).not.toHaveClass('border');
      expect(b.className).not.toMatch(/copper/);
    }
    /* The sideways-scrolling row never squeezes a bar below its labels. */
    expect(group.parentElement).toHaveClass('shrink-0');
    return { group, buttons };
  };
  const pressed = (group: HTMLElement) =>
    within(group)
      .getAllByRole('button')
      .filter((b) => b.getAttribute('aria-pressed') === 'true')
      .map((b) => b.textContent);

  it('draws Typ and Nabídka as phone-tone bars inside the Výběr row', async () => {
    renderPage();
    await screen.findByRole('list', { name: 'Žebříček makléřů' });
    const row = screen.getByRole('group', { name: 'Výběr' });
    for (const [name, labels, lit] of [
      ['Typ', ['Byty', 'Domy'], 'Byty'],
      ['Nabídka', ['Prodej', 'Pronájem'], 'Prodej'],
    ] as const) {
      const { group, buttons } = bar(name);
      expect(row.contains(group)).toBe(true);
      expect(buttons.map((b) => b.textContent)).toEqual(labels);
      expect(pressed(group)).toEqual([lit]);
      expect(within(group).getByRole('button', { name: lit })).toHaveClass(
        'bg-[var(--color-inset)]',
        'text-[var(--color-ink)]',
      );
    }
  });

  it('a tap on the lit Typ or Nabídka segment releases it and lights nothing', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Žebříček makléřů' });
    const typ = screen.getByRole('group', { name: 'Typ' });
    const nabidka = screen.getByRole('group', { name: 'Nabídka' });

    await user.click(within(typ).getByRole('button', { name: 'Byty' }));
    await waitFor(() => expect(lastParams().categoryMain).toBeNull());
    expect(pressed(typ)).toEqual([]);

    await user.click(within(nabidka).getByRole('button', { name: 'Pronájem' }));
    await waitFor(() => expect(lastParams().categoryType).toBe('pronajem'));
    expect(pressed(nabidka)).toEqual(['Pronájem']);
    await user.click(within(nabidka).getByRole('button', { name: 'Pronájem' }));
    await waitFor(() => expect(lastParams().categoryType).toBeNull());
    expect(pressed(nabidka)).toEqual([]);
  });

  it('draws Řadit as a phone-tone bar whose lit segment never lets go', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Žebříček makléřů' });
    const { group, buttons } = bar('Řadit dle');
    expect(buttons.map((b) => b.textContent)).toEqual(['Nemovitosti', 'Inzeráty']);
    expect(pressed(group)).toEqual(['Nemovitosti']);
    expect(buttons[0]).toHaveClass('bg-[var(--color-inset)]', 'text-[var(--color-ink)]');

    await user.click(buttons[0]);
    expect(lastParams().metric).toBe('active_property_count');
    expect(pressed(group)).toEqual(['Nemovitosti']);
    await user.click(buttons[1]);
    await waitFor(() => expect(lastParams().metric).toBe('listing_count'));
    expect(pressed(group)).toEqual(['Inzeráty']);
  });
});

/* P28 (phone follows desktop, operator 2026-10-27): the unit is said once over
 * the figures, never under each one. */
describe('<Brokers> on the phone: P28', () => {
  // On screen only: not the sr-only copy in each row's link name, and not the
  // leaderboard kept mounted under `hidden` while a name is searched.
  const shown = (text: string, root: Pick<typeof screen, 'queryAllByText'> = screen) =>
    root
      .queryAllByText(text)
      .filter((el) => !el.classList.contains('sr-only') && !el.closest('[hidden]'));

  it('the ranked unit is said once above the roll, and follows the metric', async () => {
    const user = userEvent.setup();
    renderPage();
    const board = await screen.findByRole('list', { name: 'Žebříček makléřů' });

    expect(shown('nemovitostí')).toHaveLength(1);
    expect(board.contains(shown('nemovitostí')[0])).toBe(false);
    expect(shown('nemovitostí', within(board))).toHaveLength(0);
    expect(within(board).getAllByRole('link')[0]).toHaveAccessibleName(/49 nemovitostí/);

    await user.click(screen.getByRole('button', { name: 'Inzeráty' }));
    const first = within(screen.getByRole('list', { name: 'Žebříček makléřů' })).getAllByRole('link')[0];
    await waitFor(() => expect(first).toHaveAccessibleName(/99 inzerátů/));
    expect(shown('inzerátů')).toHaveLength(1);
    expect(shown('nemovitostí')).toHaveLength(0);
    expect(shown('inzerátů')[0].textContent).toBe(metricUnitHead('listing_count'));
  });

  it('search results say their unit once too', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Žebříček makléřů' });
    await user.type(screen.getByRole('searchbox', { name: 'Hledat makléře podle jména' }), 'Kub');

    const results = await screen.findByRole('list', { name: 'Nalezení makléři' });
    expect(within(results).getByRole('link')).toHaveAccessibleName(/12 nemovitostí/);
    expect(shown('nemovitostí', within(results))).toHaveLength(0);
    expect(shown('nemovitostí')).toHaveLength(1);
    expect(results.contains(shown('nemovitostí')[0])).toBe(false);
  });

  it('names the head from the same branch as the figures', () => {
    expect(metricUnitHead('active_property_count')).toBe('nemovitostí');
    expect(metricUnitHead('listing_count')).toBe('inzerátů');
  });
});

describe('phone broker helpers', () => {
  const PRAHA = { name: 'Praha', context: null, level: 'obec', id: 554782 } as const;
  const ZIZKOV = { name: 'Žižkov', context: 'Praha', level: 'cast_obce', id: 490067 } as const;
  const STREET = { name: 'Na Rybníčkách', context: 'Pardubice', level: 'ulice', id: 666742 } as const;

  it('labels the picked chips compactly, counting every level', () => {
    expect(placeLabelOf([])).toBe('Celá ČR');
    expect(placeLabelOf([PRAHA])).toBe('Praha');
    expect(placeLabelOf([STREET])).toBe('Na Rybníčkách');
    expect(placeLabelOf([PRAHA, ZIZKOV, STREET])).toBe('Praha +2');
    expect(placeLabelOf([ZIZKOV, STREET])).toBe('Žižkov +1');
  });

  it('leads with an include when there is one, else with the whole country minus the excludes', () => {
    expect(placeLabelOf([{ ...ZIZKOV, excluded: true }, PRAHA])).toBe('Praha +1');
    expect(placeLabelOf([{ ...ZIZKOV, excluded: true }])).toBe('Celá ČR − Žižkov');
    expect(placeLabelOf([{ ...STREET, excluded: true }, { ...ZIZKOV, excluded: true }])).toBe('Celá ČR −2');
  });

  it('draws initials from the first and last word of the name, degrees aside', () => {
    expect(brokerInitials('Jana Kubíčková')).toBe('JK');
    expect(brokerInitials('Ing. Petr van der Berg')).toBe('PB');
    expect(brokerInitials('Petr Novák, MBA')).toBe('PN');
    expect(brokerInitials('Bc.')).toBe('?');
    expect(brokerInitials(null)).toBe('?');
  });
});
