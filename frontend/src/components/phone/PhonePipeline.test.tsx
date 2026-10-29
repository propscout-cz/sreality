/* Pipeline on the phone (#1768): under the phone frame /pipeline is the stage
 * pages, not the kanban — tabs in stage order with counts and the closed stages
 * set apart, a page per stage, and a card that moves only through its stage
 * badge's sheet, i.e. through usePipelineCard (rule 22). jsdom lays nothing out,
 * so the swipe itself (scroll-snap, the rAF sync) is a browser check; what is
 * pinned here is the wiring. */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Shell from '@/components/Shell';
import * as api from '@/lib/api';
import * as auth from '@/lib/auth';
import { createMutationCache } from '@/lib/mutationCache';
import { PHONE_PIPELINE_STAGE_KEY } from '@/lib/phonePipeline';
import { reloadActingStore, resetActiveBoard, setActiveBoard } from '@/lib/acting';
import { districtChipsToCsvParams } from '@/lib/filters';
import { PHONE_SHELL_QUERY } from '@/lib/phoneShell';
import * as pinAudit from '@/lib/pinAudit';
import * as queries from '@/lib/queries';
import type { PipelineBoardCard, PipelineStage } from '@/lib/types';
import Pipeline from '@/pages/Pipeline';

vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getNotificationUnreadCount: vi.fn(),
  movePipelineCard: vi.fn(),
  removePipelineCard: vi.fn(),
  listTags: vi.fn(),
  fetchArtifactLinks: vi.fn(),
  listPipelineShares: vi.fn(),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchPipelineStages: vi.fn(),
  fetchPipelineBoard: vi.fn(),
  fetchListingCovers: vi.fn(),
  fetchPropertyTagMemberSet: vi.fn(),
}));
vi.mock('@/lib/pinAudit', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/pinAudit')>()),
  fetchPinAuditTotal: vi.fn(),
}));
vi.mock('@/lib/supabase', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/supabase')>()),
  isSupabaseConfigured: () => true,
}));

const STAGES: PipelineStage[] = [
  /* Out of order on purpose: the pages follow `position`. */
  { id: 3, key: 'bought', label: 'Koupeno', position: 3, color: 'sage', is_terminal: true, is_entry: false, code: '9' },
  { id: 1, key: 'interest', label: 'Zájem', position: 1, color: 'copper', is_terminal: false, is_entry: true, code: '1' },
  /* No code: the badge falls back to the ordinal (stageBadge). */
  { id: 2, key: 'viewing', label: 'Prohlídka', position: 2, color: 'ochre', is_terminal: false, is_entry: false, code: null },
  { id: 4, key: 'lost', label: 'Zamítnuto', position: 4, color: 'brick', is_terminal: true, is_entry: false, code: '9' },
];

const base: PipelineBoardCard = {
  property_id: 42,
  stage_id: 1,
  board_position: 0,
  entered_stage_at: '2026-10-01T09:00:00Z',
  added_at: '2026-09-20T09:00:00Z',
  sreality_id: 111,
  source: 'sreality',
  source_id_native: '111',
  listing_id: 111,
  category_main: 'byt',
  category_type: 'prodej',
  price_per_m2: 90_909,
  price_per_m2_basis: 'sale_capital_czk_m2',
  display_label: 'Sadová 12, Praha',
  disposition: '2+kk',
  subtype: null,
  area_m2: 55,
  price_czk: 5_000_000,
  mf_gross_yield_pct: 4.3,
  total_price_change_pct: -5,
  price_change_count: 1,
  obec_id: 554782,
  cast_obce_id: null,
  ulice_id: null,
  okres_id: null,
  region_id: 19,
  obec: 'Praha',
  is_active: true,
};

const CARDS: PipelineBoardCard[] = [
  base,
  {
    ...base,
    property_id: 43,
    board_position: 1,
    listing_id: 222,
    entered_stage_at: '2026-10-12T06:00:00Z',
    category_type: 'pronajem',
    price_czk: 31_000,
    mf_gross_yield_pct: null,
    total_price_change_pct: null,
    display_label: null,
    is_active: false,
  },
  {
    ...base,
    property_id: 44,
    stage_id: 2,
    listing_id: 333,
    entered_stage_at: '2026-10-09T09:00:00Z',
    display_label: 'Lesní 3, Beroun',
    obec: 'Beroun',
    obec_id: 531057,
    region_id: 27,
  },
];

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
  /* Only Date is faked: the queries and waitFor keep their real timers. */
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-12T08:00:00Z'));
  localStorage.clear();
  reloadActingStore();
  setNarrow(true);
  vi.mocked(api.listPipelineShares).mockResolvedValue({ shared_with: [], shared_with_me: [] });
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({ unread_count: 0 });
  vi.mocked(pinAudit.fetchPinAuditTotal).mockResolvedValue(0);
  vi.mocked(auth.useAuth).mockReturnValue({
    isAdmin: true,
    session: null,
    user: { email: 'operator@example.cz' },
    loading: false,
    agendas: null,
    signOut: vi.fn(),
  } as unknown as ReturnType<typeof auth.useAuth>);
  vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES);
  vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(CARDS);
  vi.mocked(queries.fetchListingCovers).mockResolvedValue(new Map());
  vi.mocked(api.movePipelineCard).mockResolvedValue({ property_id: 42, stage_id: 2, stage_key: 'viewing' });
  vi.mocked(api.removePipelineCard).mockResolvedValue({ removed: true });
  vi.mocked(api.listTags).mockResolvedValue({
    data: [{ id: 5, name: 'k prohlídce', color: 'copper', created_at: '2026-05-01T00:00:00Z', listing_count: 1 }],
  });
  vi.mocked(queries.fetchPropertyTagMemberSet).mockResolvedValue(new Map([[42, [5]]]));
  vi.mocked(api.fetchArtifactLinks).mockResolvedValue(
    new Map([[42, { property_id: 42, url: 'https://claude.ai/artifact/abc123', created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z' }]]),
  );
});

afterEach(() => {
  resetActiveBoard();
  vi.useRealTimers();
  window.matchMedia = originalMatchMedia;
  vi.clearAllMocks();
});

function renderPipeline(entry = '/pipeline') {
  const qc = new QueryClient({
    mutationCache: createMutationCache(),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route element={<Shell />}>
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/property/:propertyId" element={<p>property body</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const panel = (name: RegExp) => screen.getByRole('tabpanel', { name });

describe('phone pipeline', () => {
  it('draws the stage tabs in position order, the closed stages set apart, and no kanban', async () => {
    renderPipeline();
    const tablist = await screen.findByRole('tablist', { name: 'Fáze' });
    const tabs = within(tablist).getAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('aria-label')?.replace(/ /g, ' '))).toEqual([
      'Zájem, 2 karty',
      'Prohlídka, 1 karta',
      'Koupeno, 0 karet',
      'Zamítnuto, 0 karet',
    ]);
    /* The badge is the stage's code, else its ordinal — never the position. */
    expect(tabs.map((t) => t.querySelector('span')?.textContent)).toEqual(['1', '2', '9', '9']);
    /* Opens on the entry stage. */
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(tabs[1]).toHaveAttribute('aria-selected', 'false');
    /* One hairline, and it stands before the first closed stage. */
    const separators = tablist.querySelectorAll(':scope > span > span[aria-hidden]');
    expect(separators).toHaveLength(1);
    expect(separators[0].nextElementSibling).toBe(tabs[2]);

    /* The header's title and count are the phone frame's. */
    const header = screen.getByRole('banner');
    expect(within(header).getByRole('heading', { name: 'Pipeline' })).toBeInTheDocument();
    /* The aside is set by an effect after the pages render, so it is awaited. */
    expect(await within(header).findByText(/^3\s+karty$/)).toBeInTheDocument();

    expect(screen.queryByRole('button', { name: 'Spravovat fáze' })).toBeNull();
    expect(screen.queryByLabelText('Přetáhnout kartu do jiné fáze')).toBeNull();
  });

  it('lays out a page per stage: its head, its cards, and the empty note', async () => {
    renderPipeline();
    const interest = await waitFor(() => panel(/^Zájem/));
    expect(within(interest).getByRole('heading', { name: 'Zájem' })).toBeInTheDocument();
    expect(within(interest).getByText('1 / 4')).toBeInTheDocument();

    const link = within(interest).getByRole('link', { name: /^5\s000\s000\sKč, 2\+kk · 55\sm² · Sadová 12, Praha$/ });
    expect(link).toHaveAttribute('href', '/property/42');
    const card = link.closest('article')!;
    expect(within(card).getByText(/^−5\s%$/)).toBeInTheDocument();
    expect(within(card).getByText(/^11\sdní ve fázi$/)).toBeInTheDocument();
    expect(within(card).getByText(/^Výnos MF 4,3\s%$/)).toBeInTheDocument();

    /* A rent reads per month, a missing place says so, a delisted ad is marked. */
    const rent = within(interest).getByRole('link', { name: /^31\s000\sKč \/měs/ }).closest('article')!;
    expect(within(rent).getByText('Lokalita neurčena')).toBeInTheDocument();
    expect(within(rent).getByText('ve fázi od dneška')).toBeInTheDocument();
    expect(within(rent).getByText('neaktivní')).toBeInTheDocument();
    expect(within(rent).queryByText(/^MF/)).toBeNull();

    const bought = panel(/^Koupeno/);
    expect(within(bought).getByText(/0\s+karet · uzavřené/)).toBeInTheDocument();
    expect(
      within(bought).getByText('V této fázi teď nic není. Kartu sem přesunete z jejího odznaku fáze.'),
    ).toBeInTheDocument();
  });

  /* A new account's board: every stage page, each with its own note — never a
     notice in their place. */
  it('draws every stage for an empty board', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([]);
    renderPipeline();
    const tablist = await screen.findByRole('tablist', { name: 'Fáze' });
    const tabs = within(tablist).getAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('aria-label')?.replace(/\u00a0/g, ' '))).toEqual([
      'Zájem, 0 karet',
      'Prohlídka, 0 karet',
      'Koupeno, 0 karet',
      'Zamítnuto, 0 karet',
    ]);
    expect(screen.queryByText(/Pipeline je zatím prázdná/)).toBeNull();
  });

  it('moves a card through its stage badge: the sheet, then usePipelineCard', async () => {
    renderPipeline();
    const interest = await waitFor(() => panel(/^Zájem/));
    const [badge] = within(interest).getAllByRole('button', { name: 'Fáze Zájem, změnit' });
    fireEvent.click(badge);

    const sheet = await screen.findByRole('dialog', { name: 'Přesunout do fáze' });
    expect(within(sheet).getByRole('menuitemradio', { name: /Zájem/ })).toHaveAttribute('aria-checked', 'true');
    /* The server's answer after the move, for the revalidation. */
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(
      CARDS.map((c) => (c.property_id === 42 ? { ...c, stage_id: 2 } : c)),
    );
    fireEvent.click(within(sheet).getByRole('menuitemradio', { name: /Prohlídka/ }));

    await waitFor(() => expect(api.movePipelineCard).toHaveBeenCalledWith(42, 2));
    expect(screen.queryByRole('dialog', { name: 'Přesunout do fáze' })).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /^Prohlídka, 2\skarty$/ })).toBeInTheDocument(),
    );
    expect(within(panel(/^Prohlídka/)).getByRole('link', { name: /Sadová 12/ })).toHaveAttribute(
      'href',
      '/property/42',
    );
    expect(within(panel(/^Zájem/)).queryByRole('link', { name: /Sadová 12/ })).toBeNull();
  });

  it('takes a card off the board only after the second step', async () => {
    renderPipeline();
    const interest = await waitFor(() => panel(/^Zájem/));
    fireEvent.click(within(interest).getAllByRole('button', { name: 'Fáze Zájem, změnit' })[0]);
    const sheet = await screen.findByRole('dialog', { name: 'Přesunout do fáze' });
    fireEvent.click(within(sheet).getByRole('menuitem', { name: 'Odebrat z pipeline' }));
    expect(within(sheet).getByText('Odebrat z pipeline?')).toBeInTheDocument();
    expect(api.removePipelineCard).not.toHaveBeenCalled();
    fireEvent.click(within(sheet).getByRole('menuitem', { name: 'Odebrat' }));
    await waitFor(() => expect(api.removePipelineCard).toHaveBeenCalledWith(42));
  });

  it('remembers the stage this device last looked at', async () => {
    const first = renderPipeline();
    fireEvent.click(await screen.findByRole('tab', { name: /^Prohlídka/ }));
    expect(screen.getByRole('tab', { name: /^Prohlídka/ })).toHaveAttribute('aria-selected', 'true');
    expect(localStorage.getItem(PHONE_PIPELINE_STAGE_KEY)).toBe('2');
    first.unmount();

    renderPipeline();
    expect(await screen.findByRole('tab', { name: /^Prohlídka/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /^Zájem/ })).toHaveAttribute('aria-selected', 'false');
  });

  it('says so when the board cannot be read, with a retry', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockRejectedValue(new Error('down'));
    renderPipeline();
    expect(await screen.findByText('Pipeline se nepodařilo načíst.')).toBeInTheDocument();
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(CARDS);
    fireEvent.click(screen.getByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByRole('tablist', { name: 'Fáze' })).toBeInTheDocument();
  });

  /* The board card's tags, in the phone's container: the chips row is the tap
     that opens the one assign list as a bottom sheet (rule #18). */
  it('prints a card\'s tags and opens the tag sheet from them', async () => {
    renderPipeline();
    const chips = await screen.findByRole('button', { name: 'Štítky: k prohlídce, upravit' });
    expect(within(chips).getByText('k prohlídce')).toBeInTheDocument();
    fireEvent.click(chips);
    const sheet = await screen.findByRole('dialog', { name: 'Štítky' });
    expect(within(sheet).getByRole('textbox', { name: 'Najít nebo vytvořit štítek' })).toBeInTheDocument();
    expect(within(sheet).getByRole('button', { name: /k prohlídce/, pressed: true })).toBeInTheDocument();
  });

  it('shows a card\'s artifact link as a small button, in a new tab', async () => {
    renderPipeline();
    const button = await screen.findByRole('link', { name: 'Artifact: claude.ai/artifact/abc123' });
    expect(button).toHaveAttribute('href', 'https://claude.ai/artifact/abc123');
    expect(button).toHaveAttribute('target', '_blank');
  });
});

/* Lokalita (the desktop bar's place filter): the same `districts` params and
 * the same matchesDistricts rule, so a desktop link opens the same cards. */
describe('phone pipeline, Lokalita', () => {
  const BEROUN = { name: 'Beroun', context: null, level: 'obec' as const, id: 531057 };
  const placeUrl = (chips: Array<typeof BEROUN & { excluded?: boolean }>) =>
    `/pipeline?${new URLSearchParams(districtChipsToCsvParams(chips))}`;

  it('offers the place chip unset, and every card', async () => {
    renderPipeline();
    expect(await screen.findByRole('button', { name: 'Lokalita: všechny' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /^Zájem, 2\skarty$/ })).toBeInTheDocument();
  });

  it('keeps every stage and counts only the cards in the place', async () => {
    renderPipeline(placeUrl([BEROUN]));
    expect(await screen.findByRole('button', { name: 'Lokalita: Beroun' })).toBeInTheDocument();
    const tabs = within(screen.getByRole('tablist', { name: 'Fáze' })).getAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('aria-label')?.replace(/\u00a0/g, ' '))).toEqual([
      'Zájem, 0 karet',
      'Prohlídka, 1 karta',
      'Koupeno, 0 karet',
      'Zamítnuto, 0 karet',
    ]);
    expect(within(panel(/^Prohlídka/)).getByRole('link', { name: /Lesní 3, Beroun/ })).toBeInTheDocument();
    expect(within(panel(/^Zájem/)).getByText('V této fázi v té lokalitě nic není.')).toBeInTheDocument();
    const header = screen.getByRole('banner');
    expect(await within(header).findByText('1 z 3 karet')).toBeInTheDocument();
  });

  it('drops an excluded place, as the desktop board does', async () => {
    renderPipeline(placeUrl([{ ...BEROUN, excluded: true }]));
    expect(await screen.findByRole('button', { name: 'Lokalita: Celá ČR − Beroun' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /^Zájem, 2\skarty$/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /^Prohlídka, 0\skaret$/ })).toBeInTheDocument();
  });

  it('opens the place typeahead in a sheet, and clearing it shows every card again', async () => {
    renderPipeline(placeUrl([BEROUN]));
    fireEvent.click(await screen.findByRole('button', { name: 'Lokalita: Beroun' }));
    const sheet = await screen.findByRole('dialog', { name: 'Lokalita' });
    expect(within(sheet).getByRole('textbox', { name: 'Lokalita' })).toBeInTheDocument();
    fireEvent.click(within(sheet).getByRole('button', { name: /^Remove Beroun/ }));
    await waitFor(() => expect(screen.getByRole('tab', { name: /^Zájem, 2\skarty$/ })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Lokalita: všechny' })).toBeInTheDocument();
  });
});

/* Shared boards on the phone (migration 641, ruling 6): the same per-device
 * active board, switched in a strip above the stages; a read board is
 * read-only, an edit board moves cards through the stage sheet as the owner. */
describe('phone pipeline, shared boards', () => {
  const READ = { account_id: 'owner-r', email: 'petr@example.cz', mode: 'read' as const };
  const EDIT = { account_id: 'owner-e', email: 'jana@example.cz', mode: 'edit' as const };

  beforeEach(() => {
    vi.mocked(api.listPipelineShares).mockResolvedValue({ shared_with: [], shared_with_me: [READ, EDIT] });
  });

  it('switches boards from the strip above the stages', async () => {
    renderPipeline();
    const strip = await screen.findByRole('group', { name: 'Zobrazený pipeline' });
    fireEvent.click(within(strip).getByRole('button', { name: 'petr@example.cz · jen ke čtení' }));
    await waitFor(() =>
      expect(queries.fetchPipelineBoard).toHaveBeenLastCalledWith(expect.objectContaining({ actAs: 'owner-r' })),
    );
  });

  it('shows a read board without the stage sheet, tags or artifact link', async () => {
    setActiveBoard(READ);
    renderPipeline();
    const interest = await waitFor(() => panel(/^Zájem/));
    expect(within(interest).getAllByText('Fáze Zájem').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Fáze Zájem, změnit' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Štítky:/ })).toBeNull();
    expect(screen.queryByRole('link', { name: /^Artifact:/ })).toBeNull();
    /* The owner's tags are not readable under a read share: never asked for. */
    expect(api.listTags).not.toHaveBeenCalledWith({ actAs: 'owner-r' });
  });

  it('moves a card on an edit board through the stage sheet, as the owner', async () => {
    setActiveBoard(EDIT);
    renderPipeline();
    const interest = await waitFor(() => panel(/^Zájem/));
    fireEvent.click(within(interest).getAllByRole('button', { name: 'Fáze Zájem, změnit' })[0]);
    const sheet = await screen.findByRole('dialog', { name: 'Přesunout do fáze' });
    fireEvent.click(within(sheet).getByRole('menuitemradio', { name: /Prohlídka/ }));
    await waitFor(() =>
      expect(api.movePipelineCard).toHaveBeenCalledWith(42, 2, undefined, { actAs: 'owner-e' }),
    );
  });

  it('stamps a card a delegate moved, with the time alone on a shared board', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(
      CARDS.map((c) => (c.property_id === 42 ? { ...c, edited_by_account_id: 'acc-x', edited_at: '2026-10-11T09:00:00Z' } : c)),
    );
    setActiveBoard(EDIT);
    renderPipeline();
    const interest = await waitFor(() => panel(/^Zájem/));
    expect(await within(interest).findByText(/^upraveno · /)).toBeInTheDocument();
  });

  it('remembers the stage per board', async () => {
    setActiveBoard(EDIT);
    renderPipeline();
    fireEvent.click(await screen.findByRole('tab', { name: /^Prohlídka/ }));
    expect(localStorage.getItem(`${PHONE_PIPELINE_STAGE_KEY}:owner-e`)).toBe('2');
    expect(localStorage.getItem(PHONE_PIPELINE_STAGE_KEY)).toBeNull();
  });
});
