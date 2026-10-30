/* Makléři on the phone (#1769): /brokers under the phone shell is the broker
 * board over the URL's Browse filters, edited in the Filtry sheet's body, with
 * the desktop tab's own request (lib/brokerBoard), plus the name search shown in
 * place of the roll. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { placeLabelOf } from '@/components/phone/placeLabel';
import * as api from '@/lib/api';
import * as brokers from '@/lib/brokers';
import type { BrokerPublic } from '@/lib/brokers';
import type { BoardMeta } from '@/lib/brokerBoard';
import { ROUTES } from '@/lib/routes';
import { expectNoNestedInteractive } from '@/test/a11y';
import { BOARD_META, boardRow } from '@/test/brokerBoard';

import Brokers from '../Brokers';
import { brokerInitials } from './PhoneBrokers';

const never = vi.hoisted(() => () => new Promise<never>(() => {}));
vi.mock('@/lib/phoneShell', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/phoneShell')>()),
  usePhoneShell: () => true,
}));
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  searchBrokersByName: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiPost: vi.fn(),
  listBrokerMergeCandidates: vi.fn(),
  listCollections: vi.fn(never),
  listTags: vi.fn(never),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchNoPriceCount: vi.fn(never),
  fetchDismissedCount: vi.fn(never),
  fetchPipelineStages: vi.fn(never),
}));
vi.mock('@/lib/pgRead', () => ({ pgRead: vi.fn(never) }));

const answer = (meta: Partial<BoardMeta> = {}) => ({
  data: [
    boardRow(1, { firm_name: 'Firma 1', properties: 49, n: 40, exited: 12, share: 0.3, rank: 0.17 }),
    boardRow(2, { properties: 48, n: 3, exited: 1 }),
  ],
  metadata: { ...BOARD_META, ...meta },
});

beforeEach(() => {
  vi.mocked(api.apiPost).mockResolvedValue(answer());
  vi.mocked(brokers.searchBrokersByName).mockResolvedValue([
    { broker_id: 42, display_name: 'Jana Kubíčková', firm_name: 'Domov 9', firm_domain: null, active_properties: 12 } as BrokerPublic,
  ]);
});

afterEach(() => vi.clearAllMocks());

function Search() {
  return <p data-testid="search">{useLocation().search}</p>;
}

function renderPage(url = '/brokers') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <Brokers />
        <Search />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastBody = () => vi.mocked(api.apiPost).mock.calls.at(-1)?.[1] as Record<string, unknown>;
const roll = () => screen.findByRole('list', { name: 'Žebříček makléřů' });

describe('<Brokers> on the phone', () => {
  it.each([
    ['/brokers', { category_main_in: ['byt'], category_type: 'prodej', districts: null, horizon_days: 30, sort: 'properties', limit: 100 }, 'Celá ČR · byty · prodej'],
    ['/brokers?cat=dum&deal=pronajem&horizon=60&rank_by=speed', { category_main_in: ['dum'], category_type: 'pronajem', horizon_days: 60, sort: 'speed' }, 'Celá ČR · domy · pronájem'],
  ])('reads the cohort and the options from the URL (%s) as the desktop tab does, volume and speed on each row', async (url, body, caption) => {
    renderPage(url);
    const board = await roll();
    expect(api.apiPost).toHaveBeenCalledWith('/brokers/board', expect.any(Object), expect.any(AbortSignal));
    expect(lastBody()).toMatchObject(body);
    expect(screen.getByText(caption)).toBeInTheDocument();
    const rows = within(board).getAllByRole('link');
    expect(rows[0]).toHaveAttribute('href', ROUTES.brokerDetail.build({ id: 1 }));
    expect(rows[0].textContent).toContain('Makléř 1');
    expect(rows[0].textContent).toContain('49');
    expect(rows[0].textContent).toMatch(/12 z 40 · 30\s%/);
    expect(rows[1].textContent).toContain('n = 3');
    expect(rows[1].textContent).toContain('nezávislý / neznámá kancelář');
    expectNoNestedInteractive(board);
    /* The desktop's extras stay on the desktop. */
    expect(screen.queryByText('Firma')).toBeNull();
    expect(api.listBrokerMergeCandidates).not.toHaveBeenCalled();
  });

  it('edits the cohort in the Filtry sheet body and applies it to the URL', async () => {
    const user = userEvent.setup();
    renderPage('/brokers?cat=dum&horizon=60');
    await roll();
    await user.click(screen.getByRole('button', { name: 'Filtry (1 navíc)' }));
    const sheet = screen.getByRole('dialog', { name: 'Filtry' });
    await waitFor(() => expect(within(sheet).getAllByRole('region').length).toBeGreaterThan(0));
    /* The order and the presets are the Feed's, never the board's. */
    expect(within(sheet).queryByRole('button', { name: /^Řazení/ })).toBeNull();
    expect(within(sheet).queryByRole('button', { name: 'Předvolba' })).toBeNull();

    await user.click(within(sheet).getByRole('button', { name: 'Vymazat' }));
    await user.click(within(sheet).getByRole('button', { name: 'Zobrazit makléře' }));
    expect(screen.queryByRole('dialog', { name: 'Filtry' })).toBeNull();
    await waitFor(() => expect(lastBody().category_main_in).toEqual(['byt']));
    const sp = new URLSearchParams(screen.getByTestId('search').textContent ?? '');
    expect(sp.get('cat')).not.toBe('dum');
    expect(sp.get('horizon')).toBe('60');
    expect(screen.getByRole('button', { name: 'Filtry' })).toBeInTheDocument();
  });

  it('searching by name swaps the roll for the results, clearing brings it back as it was', async () => {
    const user = userEvent.setup();
    renderPage('/brokers?cat=dum');
    await roll();
    await user.type(screen.getByRole('searchbox', { name: 'Hledat makléře podle jména' }), 'Kub');

    const results = await screen.findByRole('list', { name: 'Nalezení makléři' });
    expect(brokers.searchBrokersByName).toHaveBeenCalledWith('Kub');
    const hit = within(results).getByRole('link');
    expect(hit).toHaveAttribute('href', ROUTES.brokerDetail.build({ id: 42 }));
    expect(hit.textContent).toContain('JK');
    expect(screen.queryByRole('list', { name: 'Žebříček makléřů' })).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Vymazat hledání' }));
    expect(await roll()).toBeInTheDocument();
    expect(lastBody().category_main_in).toEqual(['dum']);
  });

  it('explains a refused speed and turns Rychlost off', async () => {
    vi.mocked(api.apiPost).mockResolvedValue(
      answer({ speed_refused: 'mixed_category', speed_refused_by: ['category_main_in'] }),
    );
    renderPage();
    await roll();
    expect(screen.getByRole('note')).toHaveTextContent('Zvolte jen jednu hodnotu: „Type“.');
    expect(within(screen.getByRole('group', { name: 'Řadit dle' })).getByRole('button', { name: 'Rychlost' })).toBeDisabled();
  });

  it('says a failed board failed', async () => {
    vi.mocked(api.apiPost).mockRejectedValue(new Error('boom'));
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

  it('draws Řadit and the horizon as phone-tone bars in the Výběr row, writing the board options', async () => {
    const user = userEvent.setup();
    renderPage();
    await roll();
    const choice = screen.getByRole('group', { name: 'Výběr' });

    const sort = bar('Řadit dle');
    expect(choice.contains(sort.group)).toBe(true);
    expect(sort.buttons.map((b) => b.textContent)).toEqual(['Nemovitosti', 'Rychlost']);
    expect(pressed(sort.group)).toEqual(['Nemovitosti']);
    expect(sort.buttons[0]).toHaveClass('bg-[var(--color-inset)]', 'text-[var(--color-ink)]');
    await user.click(sort.buttons[1]);
    await waitFor(() => expect(lastBody().sort).toBe('speed'));
    expect(pressed(sort.group)).toEqual(['Rychlost']);

    const horizon = bar('Opustilo trh do (dní)');
    expect(horizon.buttons.map((b) => b.textContent)).toEqual(['14 dní', '30 dní', '60 dní', '90 dní']);
    expect(pressed(horizon.group)).toEqual(['30 dní']);
    await user.click(horizon.buttons[2]);
    await waitFor(() => expect(lastBody().horizon_days).toBe(60));
    expect(new URLSearchParams(screen.getByTestId('search').textContent ?? '').get('horizon')).toBe('60');
  });
});

/* P28 (phone follows desktop, operator 2026-10-27): the unit is said once over
 * the figures, never under each one. */
describe('<Brokers> on the phone: P28', () => {
  // On screen only: not the sr-only copy in each row's link name, and not the
  // board kept mounted under `hidden` while a name is searched.
  const shown = (text: string, root: Pick<typeof screen, 'queryAllByText'> = screen) =>
    root
      .queryAllByText(text)
      .filter((el) => !el.classList.contains('sr-only') && !el.closest('[hidden]'));

  it('the unit is said once above the roll', async () => {
    renderPage();
    const board = await roll();
    expect(shown('nemovitostí')).toHaveLength(1);
    expect(board.contains(shown('nemovitostí')[0])).toBe(false);
    expect(within(board).getAllByRole('link')[0]).toHaveAccessibleName(/49 nemovitostí/);
  });

  it('search results say their unit once too', async () => {
    const user = userEvent.setup();
    renderPage();
    await roll();
    await user.type(screen.getByRole('searchbox', { name: 'Hledat makléře podle jména' }), 'Kub');

    const results = await screen.findByRole('list', { name: 'Nalezení makléři' });
    expect(within(results).getByRole('link')).toHaveAccessibleName(/12 nemovitostí/);
    expect(shown('nemovitostí', within(results))).toHaveLength(0);
    expect(shown('nemovitostí')).toHaveLength(1);
    expect(results.contains(shown('nemovitostí')[0])).toBe(false);
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
