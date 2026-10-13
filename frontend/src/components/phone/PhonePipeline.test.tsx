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
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchPipelineStages: vi.fn(),
  fetchPipelineBoard: vi.fn(),
  fetchListingCovers: vi.fn(),
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
  setNarrow(true);
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
  vi.mocked(api.getNotificationUnreadCount).mockResolvedValue({
    watchdog: 0,
    collection_monitor: 0,
    system_health: 0,
    total: 0,
    unread_count: 0,
  });
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
});

afterEach(() => {
  vi.useRealTimers();
  window.matchMedia = originalMatchMedia;
  vi.clearAllMocks();
});

function renderPipeline() {
  const qc = new QueryClient({
    mutationCache: createMutationCache(),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/pipeline']}>
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
    expect(within(header).getByText(/^3\s+karty$/)).toBeInTheDocument();

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
    expect(within(card).getByText(/^MF 4,3\s%$/)).toBeInTheDocument();

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
});
