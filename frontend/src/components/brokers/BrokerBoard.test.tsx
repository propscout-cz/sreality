/* Browse › Makléři: the board over Browse's filters. The read is stubbed at the
 * API seam (apiPost), so the request lib/brokerBoard builds is the one these
 * pin; its keys against BoardArgs are lib/brokerBoard.test.ts's. */

import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import BrokerBoard from './BrokerBoard';
import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
import * as brokers from '@/lib/brokers';
import { DEFAULT_BOARD, type BoardMeta, type BoardOptions, type BoardRow } from '@/lib/brokerBoard';
import type { BrokerFirmOption } from '@/lib/brokers';
import { DEFAULT_FILTERS, type ListingFilters } from '@/lib/filters';
import { ROUTES } from '@/lib/routes';
import { BOARD_META, boardRow } from '@/test/brokerBoard';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiPost: vi.fn(),
  listBrokerMergeCandidates: vi.fn(),
}));
vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  searchBrokerFirms: vi.fn(),
  searchBrokersByName: vi.fn(),
}));
vi.mock('@/lib/supabase', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/supabase')>()),
  isSupabaseConfigured: () => true,
}));

const ROWS: BoardRow[] = [
  boardRow(7, { display_name: 'Jan Fleischner', firm_name: 'Alfa Reality', properties: 120, n: 40, exited: 12, unknown: 2, share: 0.3, rank: 0.17, has_phone: true }),
  boardRow(42, { display_name: 'Michaela Koudelová', properties: 30, n: 3, exited: 3 }),
  boardRow(9, { display_name: 'Petr Novák', properties: 5 }),
];

const answer = (rows: BoardRow[] = ROWS, meta: Partial<BoardMeta> = {}) => ({ data: rows, metadata: { ...BOARD_META, ...meta } });
const lastBody = () => vi.mocked(api.apiPost).mock.calls.at(-1)?.[1] as Record<string, unknown>;

function asUser(isAdmin: boolean): void {
  vi.mocked(auth.useAuth).mockReturnValue({ isAdmin, agendas: { brokers: true } } as unknown as ReturnType<
    typeof auth.useAuth
  >);
}

/* The board's options live in the view (the URL on the page); a state here. */
function Host({ filters, initial, onBoard }: { filters: ListingFilters; initial: BoardOptions; onBoard: (patch: Partial<BoardOptions>) => void }) {
  const [board, setBoard] = useState<BoardOptions>(initial);
  return (
    <BrokerBoard
      filters={filters}
      board={board}
      onBoardChange={(patch) => {
        onBoard(patch);
        setBoard((b) => ({ ...b, ...patch }));
      }}
    />
  );
}

function renderBoard(filters: ListingFilters = DEFAULT_FILTERS, initial: BoardOptions = DEFAULT_BOARD) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onBoard = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Host filters={filters} initial={initial} onBoard={onBoard} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { qc, onBoard };
}

beforeEach(() => {
  vi.clearAllMocks();
  asUser(false);
  vi.mocked(api.apiPost).mockResolvedValue(answer());
  vi.mocked(api.listBrokerMergeCandidates).mockResolvedValue({
    data: [],
    count: 0,
    reason_counts: { name_firm: 3 },
  } as unknown as Awaited<ReturnType<typeof api.listBrokerMergeCandidates>>);
  vi.mocked(brokers.searchBrokerFirms).mockResolvedValue([]);
  vi.mocked(brokers.searchBrokersByName).mockResolvedValue([]);
});

const ledger = () => screen.findByRole('table');
const rowOf = (name: string) => screen.getByRole('link', { name }).closest('tr') as HTMLElement;
const sentence = (re: RegExp) => screen.getByText((_, el) => el?.tagName === 'P' && re.test(el.textContent ?? ''));

describe('<BrokerBoard>', () => {
  it('asks for the board of the filters it is given', async () => {
    renderBoard({ ...DEFAULT_FILTERS, categoryMain: ['dum'], brokerId: 5 });
    await ledger();
    expect(api.apiPost).toHaveBeenCalledWith('/brokers/board', expect.any(Object), expect.any(AbortSignal));
    expect(lastBody()).toMatchObject({ category_main_in: ['dum'], category_type: 'prodej', broker_id: 5, horizon_days: 30 });
  });

  it('draws each broker: a link to its page, its firm, volume, speed, the unknown outcomes and the masked phone', async () => {
    renderBoard();
    await ledger();
    const link = screen.getByRole('link', { name: 'Jan Fleischner' });
    expect(link).toHaveAttribute('href', ROUTES.brokerDetail.build({ id: 7 }));
    expect(link).not.toHaveAttribute('target');
    const jan = rowOf('Jan Fleischner');
    expect(jan).toHaveTextContent('Alfa Reality');
    expect(jan).toHaveTextContent('120 nemovitostí');
    expect(within(jan).getByText('12 z 40')).toBeInTheDocument();
    expect(within(jan).getByText(/^30\s%$/)).toBeInTheDocument();
    expect(within(jan).getByTitle(/^Dolní mez \(Wilson, 95 %\): 17\s%$/)).toBeInTheDocument();
    expect(within(jan).getByText('na vyžádání')).toBeInTheDocument();
    /* The unknown outcomes in their own column, beside n, never in it. */
    expect(screen.getByRole('columnheader', { name: 'Nezjištěno' })).toBeInTheDocument();
    expect(within(jan).getAllByRole('cell').at(-1)).toHaveTextContent(/^2$/);
    const michaela = rowOf('Michaela Koudelová');
    expect(michaela).toHaveTextContent('nezávislý / neznámá kancelář');
    expect(within(michaela).getByText('n = 3')).toBeInTheDocument();
    expect(michaela).not.toHaveTextContent('%');
    const petr = rowOf('Petr Novák');
    expect(petr).not.toHaveTextContent('n =');
    expect(within(petr).queryByText('na vyžádání')).toBeNull();
    /* No ad counts: one vocabulary, property grain (ruling 4); no lens is set, so no note. */
    expect(screen.queryByText(/inzerát/i)).toBeNull();
    expect(screen.queryByText(/nebere v úvahu/)).toBeNull();
  });

  it('says who stands behind the rows, the market every speed is read against, and what speed means', async () => {
    renderBoard();
    await ledger();
    expect(
      sentence(
        /^Ve výběru 500 nemovitostí včetně skrytých, makléře známe u 400 \(bez makléře 20\s%\); Bazoš a Bezrealitky makléře neuvádějí\.$/,
      ),
    ).toBeInTheDocument();
    expect(sentence(/^Celý výběr: 10 z 40 opustilo trh do 30 dní, 25\s% · nezjištěno 4\.$/)).toBeInTheDocument();
    const legend = screen.getByText(/znamená prodáno nebo staženo, ne nutně prodej/);
    expect(legend).toHaveTextContent('od 8. 9. 2026 do 22. 9. 2026');
    expect(legend).toHaveTextContent('(30 dní a 7 dní rezerva)');
    expect(legend).toHaveTextContent('n ≥ 5');
    expect(screen.getByRole('button', { name: /Opustilo trh do 30 dní/ })).toHaveAttribute(
      'title',
      'prodáno nebo staženo, ne nutně prodej',
    );
  });

  it('says when a horizon no spell can have been followed through yet first answers', async () => {
    vi.mocked(api.apiPost).mockResolvedValue(
      answer(ROWS.map((r) => ({ ...r, n: 0, exited: 0, share: null, rank: null })), {
        horizon_days: 90,
        market: { n: 0, exited: 0, share: null },
        unknown: 0,
      }),
    );
    renderBoard(DEFAULT_FILTERS, { ...DEFAULT_BOARD, horizonDays: 90 });
    await ledger();
    const notYet = 'Pro 90 dní zatím nelze nic změřit: první odpovědi od 14. 12. 2026.';
    expect(screen.getByText((_, el) => el?.tagName === 'P' && el.textContent === notYet)).toBeInTheDocument();
    const legend = screen.getByText(/znamená prodáno nebo staženo, ne nutně prodej/);
    expect(legend).toHaveTextContent(notYet);
    expect(legend).not.toHaveTextContent('začal inzerovat');
  });

  it.each([
    [
      'outcome_selected',
      ['status', 'recently_changed_days', 'tom_days_min', 'tom_days_max', 'portals_excluded_active_only'],
      'výběr třídí podle výsledku. Vraťte na výchozí: „Status“, „Recently changed“, „Time on market (days)“ a „Source portal“.',
    ],
    [
      'mixed_category',
      ['category_type', 'category_main_in'],
      'míchá typy nemovitostí nebo nabídky. Zvolte jen jednu hodnotu: „Listing for“ a „Type“.',
    ],
  ] as const)('names the filters behind a refused speed (%s), keeps the volume and orders by it', async (code, by, why) => {
    vi.mocked(api.apiPost).mockResolvedValue(
      answer(ROWS.map((r) => ({ ...r, share: null, rank: null })), { speed_refused: code, speed_refused_by: [...by] }),
    );
    renderBoard(DEFAULT_FILTERS, { ...DEFAULT_BOARD, sort: 'speed' });
    await ledger();
    expect(screen.getByRole('note')).toHaveTextContent(`Rychlost se pro tento výběr nepočítá: ${why}`);
    expect(screen.queryByText(/^Celý výběr/)).toBeNull();
    expect(screen.getByRole('button', { name: /Opustilo trh/ })).toBeDisabled();
    expect(rowOf('Jan Fleischner')).toHaveTextContent('120 nemovitostí');
    expect(rowOf('Jan Fleischner')).not.toHaveTextContent('z 40');
    /* Whatever the link asked. */
    expect(lastBody().sort).toBe('speed');
    expect(screen.getByRole('columnheader', { name: /Nemovitostí/ })).toHaveAttribute('aria-sort', 'descending');
    expect(screen.getByRole('columnheader', { name: /Opustilo trh/ })).toHaveAttribute('aria-sort', 'none');
  });

  it('says it leaves the account lenses out, and never sends them', async () => {
    renderBoard({ ...DEFAULT_FILTERS, tags: { set: null, in: [4], out: [] }, withEstimates: true });
    await ledger();
    expect(screen.getByText('Žebříček makléřů nebere v úvahu: štítky, jen s odhadem.')).toBeInTheDocument();
    expect(lastBody()).not.toHaveProperty('tags');
    expect(lastBody()).not.toHaveProperty('with_estimates');
  });

  it('orders by a sortable column head, aria-sort following', async () => {
    const { onBoard } = renderBoard();
    await ledger();
    const speed = screen.getByRole('columnheader', { name: /Opustilo trh do 30 dní/ });
    expect(screen.getByRole('columnheader', { name: /Nemovitostí/ })).toHaveAttribute('aria-sort', 'descending');
    expect(speed).toHaveAttribute('aria-sort', 'none');
    expect(screen.getByRole('columnheader', { name: 'Makléř' })).not.toHaveAttribute('aria-sort');

    fireEvent.click(within(speed).getByRole('button'));
    expect(onBoard).toHaveBeenLastCalledWith({ sort: 'speed' });
    await waitFor(() => expect(lastBody().sort).toBe('speed'));
    expect(screen.getByRole('columnheader', { name: /Opustilo trh/ })).toHaveAttribute('aria-sort', 'descending');
    expect(screen.getByRole('columnheader', { name: /Nemovitostí/ })).toHaveAttribute('aria-sort', 'none');
    /* No "Řadit dle" bar beside the heads. */
    expect(screen.queryByRole('group', { name: /Řadit/ })).toBeNull();
  });

  it('sets the horizon, the minimum and the count, each sent', async () => {
    const { onBoard } = renderBoard();
    await ledger();
    const pick = (group: string, value: string) =>
      fireEvent.click(within(screen.getByRole('group', { name: group })).getByRole('button', { name: value }));
    pick('Opustilo trh do (dní)', '60');
    expect(onBoard).toHaveBeenLastCalledWith({ horizonDays: 60 });
    pick('Aspoň nemovitostí', '5');
    pick('Počet', '200');
    await waitFor(() => expect(lastBody()).toMatchObject({ horizon_days: 60, min_properties: 5, limit: 200 }));
  });

  it('keeps the previous rows on screen, marked busy, while the next board loads', async () => {
    renderBoard();
    await ledger();
    let resolve: (v: unknown) => void = () => {};
    vi.mocked(api.apiPost).mockReturnValueOnce(new Promise((r) => (resolve = r)));
    fireEvent.click(within(screen.getByRole('group', { name: 'Počet' })).getByRole('button', { name: '50' }));
    const section = screen.getByRole('region', { name: 'Žebříček makléřů' });
    await waitFor(() => expect(section).toHaveAttribute('aria-busy', 'true'));
    expect(screen.getByRole('link', { name: 'Jan Fleischner' })).toBeInTheDocument();
    await act(async () => resolve(answer([boardRow(3, { display_name: 'Nový Makléř' })])));
    expect(await screen.findByRole('link', { name: 'Nový Makléř' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Žebříček makléřů' })).not.toHaveAttribute('aria-busy');
  });

  it.each([
    ['no broker matches', () => vi.mocked(api.apiPost).mockResolvedValue(answer([])), 'Žádní makléři pro tento výběr.'],
    ['the board failed', () => vi.mocked(api.apiPost).mockRejectedValue(new Error('boom')), /Žebříček se nepodařilo načíst/],
  ])('says so when %s', async (_, stub, text) => {
    stub();
    renderBoard();
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  /* /broker-review/candidates is require_admin while the tab is agenda-gated. */
  it('sizes the merge-candidates badge for an admin, and never asks the admin-only route for anyone else', async () => {
    renderBoard();
    await ledger();
    expect(api.listBrokerMergeCandidates).not.toHaveBeenCalled();
    asUser(true);
    renderBoard();
    expect(await screen.findByRole('link', { name: 'Sloučit duplicity (3)' })).toHaveAttribute(
      'href',
      ROUTES.brokersReview.build(),
    );
    expect(api.listBrokerMergeCandidates).toHaveBeenCalledWith(1);
  });

  it('renders each name-search result as an anchor to that broker', async () => {
    vi.mocked(brokers.searchBrokersByName).mockResolvedValue([
      { broker_id: 99, display_name: 'Hledany Makler', firm_name: null, firm_domain: null, cz_active_property_count: 3, active_property_count: 5 },
    ] as unknown as Awaited<ReturnType<typeof brokers.searchBrokersByName>>);
    renderBoard();
    fireEvent.change(screen.getByPlaceholderText(/Hledat makl/), { target: { value: 'Hledany' } });
    const hit = await screen.findByRole('link', { name: /Hledany Makler/ }, { timeout: 3000 });
    expect(hit).toHaveAttribute('href', ROUTES.brokerDetail.build({ id: 99 }));
  });
});

/* The Firma chips are an output filter on the broker's firm (P29). */
describe('<BrokerBoard> Firma', () => {
  // Busiest first, as the server orders them; counts under 1000 so no thousands separator enters the text.
  const firms = (n: number): BrokerFirmOption[] =>
    Array.from({ length: n }, (_, i) => ({
      firm_id: i + 1,
      canonical_domain: `firma-${String(i + 1).padStart(2, '0')}.cz`,
      display_name: null,
      is_franchise: false,
      broker_count: 500 - i * 10,
    }));

  it('toggles a firm into the request and out again', async () => {
    vi.mocked(brokers.searchBrokerFirms).mockResolvedValue(firms(2));
    const { onBoard } = renderBoard();
    const first = await screen.findByText('firma-01.cz');
    fireEvent.click(first);
    expect(first).toHaveAttribute('aria-pressed', 'true');
    expect(onBoard).toHaveBeenLastCalledWith({ firmIds: [1] });
    await waitFor(() => expect(lastBody().firm_ids).toEqual([1]));
    fireEvent.click(first);
    expect(onBoard).toHaveBeenLastCalledWith({ firmIds: [] });
  });

  it('P29: the five largest firms, then "+ 19 dalších" opens the rest', async () => {
    vi.mocked(brokers.searchBrokerFirms).mockResolvedValue(firms(24));
    renderBoard();
    expect(await screen.findByText('firma-05.cz')).toBeInTheDocument();
    expect(brokers.searchBrokerFirms).toHaveBeenCalledWith('', 24);
    expect(screen.queryByText('firma-06.cz')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /\+\s*19\s*dalších/ }));
    expect(screen.getByText('firma-24.cz')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /dalš/ })).toBeNull();
    await waitFor(() => expect(screen.getByText('firma-06.cz')).toHaveFocus());
  });

  it('P29: a chosen firm stays in view, pressed, when a refetch ranks it past the fifth', async () => {
    const before = firms(24);
    vi.mocked(brokers.searchBrokerFirms).mockResolvedValue(before);
    const { qc } = renderBoard();
    fireEvent.click(await screen.findByText('firma-05.cz'));
    const after = [...before.slice(0, 4), { ...before[5], broker_count: before[4].broker_count + 1 }, before[4], ...before.slice(6)];
    vi.mocked(brokers.searchBrokerFirms).mockResolvedValue(after);
    await act(() => qc.refetchQueries({ queryKey: ['broker-firm-options'] }));
    await waitFor(() => expect(screen.getByText('firma-06.cz')).toBeInTheDocument());
    expect(screen.getByText('firma-05.cz')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /\+\s*18\s*dalších/ })).toBeInTheDocument();
  });
});
