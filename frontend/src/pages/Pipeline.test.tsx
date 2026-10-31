/* Pipeline kanban — drag-and-drop move resolution + board interaction.
 *
 * The real DnD gesture (pointer drag across columns) can't be faithfully
 * simulated in jsdom, so the bug-prone part — resolving a drag-end into a
 * stage move — is extracted into the pure `planMove` and unit-tested directly.
 * A render smoke test then pins the board's columns and the trash → confirm →
 * remove flow (stage moves are drag-only; the select fallback was removed).
 * A drop is driven through the board's own DndContext onDragEnd, recorded below.
 */

import { afterEach, describe, expect, it, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type {
  Active,
  ClientRect,
  DndContextProps,
  DragCancelEvent,
  DragEndEvent,
  DragStartEvent,
  DroppableContainer,
} from '@dnd-kit/core';

import Pipeline, { boardCollision, planMove } from './Pipeline';
import type { Collection, PipelineBoardCard, PipelineStage, PropertySource, Tag } from '@/lib/types';
import * as api from '@/lib/api';
import * as queries from '@/lib/queries';
import { pipelineKeys, type CityIndexDefinition, type CuratedCity } from '@/lib/queries';
import { PIPELINE_CLOSED_LANE_WIDTH } from '@/lib/pipelineCardSize';
import * as brokersApi from '@/lib/brokers';
import { createMutationCache } from '@/lib/mutationCache';
import * as toast from '@/lib/toast';
import {
  ACTIVE_BOARD_KEY,
  getActiveBoard,
  reloadActingStore,
  resetActiveBoard,
  setActiveBoard,
} from '@/lib/acting';
import { BOARD_GONE_MESSAGE } from '@/lib/pipelineShares';
import { NewPropertyProvider } from '@/components/NewPropertyProvider';
import { watchReads } from '@/test/reads';
import { revalidateTables } from '@/lib/curationSync';

/* Every read the page makes has to be named here: the factories spread the
   real module, so an unnamed one runs for real and fails SOFT (request()
   throws on an unset VITE_API_BASE_URL) — a green suite proving nothing. */
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    movePipelineCard: vi.fn(),
    removePipelineCard: vi.fn(),
    listCollections: vi.fn(),
    listTags: vi.fn(),
    attachTag: vi.fn(),
    detachTag: vi.fn(),
    fetchArtifactLinks: vi.fn(),
    listPipelineShares: vi.fn(),
    sharePipeline: vi.fn(),
    unsharePipeline: vi.fn(),
    createPipelineStage: vi.fn(),
    updatePipelineStage: vi.fn(),
    ingestListing: vi.fn(),
    addPipelineCard: vi.fn(),
  };
});

/* Only the "Nová nemovitost" provider asks who is signed in (admins only, v1). */
vi.mock('@/lib/auth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/auth')>()),
  useAuth: vi.fn(() => ({ isAdmin: true })),
}));

vi.mock('@/lib/queries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/queries')>();
  return {
    ...actual,
    fetchPipelineStages: vi.fn(),
    fetchPipelineBoard: vi.fn(),
    fetchListingCovers: vi.fn(),
    fetchPropertyCollectionMemberSet: vi.fn(),
    fetchPropertyTagMemberSet: vi.fn(),
    fetchPropertySourcesByPropertyIds: vi.fn(),
    fetchCuratedCities: vi.fn(),
    fetchCityIndexDefinitions: vi.fn(),
    fetchCityIndexValues: vi.fn(),
  };
});

/* The real drag gesture can't be driven in jsdom, so the board's two dnd-kit
   seams are recorded instead: what each droppable registers under which id, and
   the DndContext's props, whose onDragEnd a test then calls as a drop would. */
const dnd = vi.hoisted(() => ({
  droppables: new Map<string, unknown>(),
  context: null as DndContextProps | null,
}));
vi.mock('@dnd-kit/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@dnd-kit/core')>();
  const { createElement } = await import('react');
  return {
    ...actual,
    useDroppable: (args: Parameters<typeof actual.useDroppable>[0]) => {
      dnd.droppables.set(String(args.id), args.data);
      return actual.useDroppable(args);
    },
    DndContext: (props: DndContextProps) => {
      dnd.context = props;
      return createElement(actual.DndContext, props);
    },
  };
});

vi.mock('@/lib/toast', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/toast')>();
  return { ...actual, pushToast: vi.fn(() => 1) };
});

/* Decorations (cover photo, broker line) no longer come off the board query —
 * they load through lib/hydration. The broker line is the property's list
 * (MS7): the cards' ads, then their brokers. Mocking those batch readers rather
 * than the hooks means these tests drive the REAL provider, hooks, key
 * namespace and `propertyBrokers` rule, so what they pin is the path that
 * actually runs in the browser. */
vi.mock('@/lib/brokers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/brokers')>();
  return {
    ...actual,
    fetchListingBrokersByIds: vi.fn(),
  };
});

const CARDS: PipelineBoardCard[] = [
  {
    property_id: 42,
    stage_id: 1,
    board_position: 0,
    entered_stage_at: '2026-06-01T00:00:00Z',
    added_at: '2026-05-20T00:00:00Z',
    sreality_id: 111,
    source: 'sreality',
    source_id_native: '111',
    listing_id: 111,
    category_main: 'byt',
    category_type: 'prodej',
    price_per_m2: 90_909,
    price_per_m2_basis: 'sale_capital_czk_m2',
    display_label: 'Sadová, Praha',
    disposition: '2+kk',
    subtype: null,
    area_m2: 55,
    price_czk: 5_000_000,
    mf_gross_yield_pct: 4.3,
    total_price_change_pct: -4.2,
    price_change_count: 1,
    obec_id: 554782,
    cast_obce_id: null,
    ulice_id: null,
    okres_id: null,
    region_id: 19,
    obec: 'Praha',
    is_active: true,
  },
];

/* The decoration fixtures, keyed on listing_id like the real layer. */
/* W6: identity AND contact are one row now (migration 419), so there is no second
   fixture to pair with this one. */
const listingBroker = () => ({
  sreality_id: 111,
  listing_id: 111,
  broker_id: 7,
  broker_display_name: 'Jan Novák',
  broker_firm_label: 'RE/MAX',
  primary_email: 'jan@remax.cz',
  primary_phone: '420777123456',
});

/* Each card's ads, for its broker line (MS7). */
const ad = (id: number, property_id: number, is_active = true) =>
  ({ id, property_id, is_active }) as PropertySource;

// A second card of a different property type, for the type-filter test.
const CARD_DUM: PipelineBoardCard = {
  property_id: 43,
  stage_id: 3,
  board_position: 0,
  entered_stage_at: '2026-06-02T00:00:00Z',
  added_at: '2026-05-25T00:00:00Z',
  sreality_id: 222,
  source: 'idnes',
  source_id_native: null,
  listing_id: 222,
  category_main: 'dum',
  category_type: 'prodej',
  price_per_m2: 64_286,
  price_per_m2_basis: 'sale_capital_czk_m2',
  display_label: 'Lesní, Brno',
  disposition: '4+1',
  subtype: null,
  area_m2: 140,
  price_czk: 9_000_000,
  mf_gross_yield_pct: null,
  total_price_change_pct: null,
  price_change_count: 0,
  obec_id: 582786,
  cast_obce_id: null,
  ulice_id: null,
  okres_id: 3702,
  region_id: 116,
  obec: 'Brno',
  is_active: true,
};

// A delisted property, for the active/inactive status-filter test.
const CARD_INACTIVE: PipelineBoardCard = {
  ...CARD_DUM,
  property_id: 44,
  sreality_id: 333,
  listing_id: 333,
  display_label: 'Polní, Ostrava',
  // The card's place line is display_label alone now, but the remaining place
  // columns still feed the in-memory chip predicate, so an overriding fixture
  // must move them together or the card filters as if it were still in Brno.
  obec: 'Ostrava',
  is_active: false,
};

// A closed deal: its stage is terminal, so it sits behind the Uzavřené lane.
const CARD_LOST: PipelineBoardCard = {
  ...CARD_DUM,
  property_id: 45,
  stage_id: 9,
  sreality_id: 444,
  listing_id: 444,
  display_label: 'Luční, Plzeň',
  obec: 'Plzeň',
};

const collection = (id: number, name: string): Collection => ({
  id,
  name,
  description: null,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-01T00:00:00Z',
  listing_count: 1,
  monitoring_enabled: false,
  is_system: false,
  owner_email: null,
  shared_with: [],
});

// Šortlist holds a board property; Archiv holds none — only the first earns a chip.
const COLLECTIONS = [collection(7, 'Šortlist'), collection(8, 'Archiv')];

const TAGS: Tag[] = [
  { id: 5, name: 'k prohlídce', color: 'copper', created_at: '2026-05-01T00:00:00Z', listing_count: 1 },
  { id: 6, name: 'odmítnuto', color: 'brick', created_at: '2026-05-01T00:00:00Z', listing_count: 0 },
];

describe('planMove', () => {
  it('resolves a cross-column drop into a stage move', () => {
    expect(planMove('card:42', 'stage:3', CARDS)).toEqual({
      propertyId: 42,
      stageId: 3,
    });
  });

  it('is a no-op for a same-column drop', () => {
    expect(planMove('card:42', 'stage:1', CARDS)).toBeNull();
  });

  it('is a no-op when dropped outside any column', () => {
    expect(planMove('card:42', null, CARDS)).toBeNull();
  });

  it('is a no-op when over is not a stage droppable', () => {
    expect(planMove('card:42', 'card:99', CARDS)).toBeNull();
  });

  it('is a no-op for an unknown card', () => {
    expect(planMove('card:999', 'stage:3', CARDS)).toBeNull();
  });
});

const STAGES: PipelineStage[] = [
  { id: 1, key: 'interested', label: 'Zájem', position: 1, color: 'copper', is_terminal: false, is_entry: true, code: '1' },
  /* No `code` — exercises stageBadge's ordinal fallback (migration 377). */
  { id: 3, key: 'offer', label: 'Nabídka', position: 3, color: 'teal', is_terminal: false, is_entry: false, code: null },
];

/* P22: two terminal stages after the live ones, as the default board's are. */
const STAGES_CLOSED: PipelineStage[] = [
  ...STAGES,
  { id: 9, key: 'lost', label: '9. Lost', position: 9, color: 'sand', is_terminal: true, is_entry: false, code: '9' },
  { id: 10, key: 'bought', label: '9. Bought', position: 10, color: 'sage', is_terminal: true, is_entry: false, code: '9' },
];

/* The header row the stage headers are pinned in (BoardFrame). */
const headerRowOf = (label: string) => screen.getByText(label).parentElement!.parentElement!;

// The app's own MutationCache, so a failed write's global toast is real.
const boardClient = () =>
  new QueryClient({
    mutationCache: createMutationCache(),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

function renderBoard(entry = '/pipeline', qc: QueryClient = boardClient()) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Pipeline />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/* Nobody shares a board with me unless a test says so (migration 641). */
const NO_SHARES = { shared_with: [], shared_with_me: [] };

/* The bar's long sets open from a menu (variant A, 2026-10-22): its button names
   the set, then how many of its choices are on; the panel holds the set's control. */
const openMenu = async (label: string) => {
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(`^${label}( · \\d+)?$`) }));
  return screen.findByRole('group', { name: label });
};

describe('<Pipeline> board', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(api.listPipelineShares).mockResolvedValue(NO_SHARES);
    vi.mocked(api.listTags).mockResolvedValue({ data: TAGS });
    vi.mocked(api.attachTag).mockResolvedValue({ attached: true });
    vi.mocked(api.detachTag).mockResolvedValue({ detached: true });
    vi.mocked(queries.fetchPropertyTagMemberSet).mockResolvedValue(new Map());
    vi.mocked(api.fetchArtifactLinks).mockResolvedValue(new Map());
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES);
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(CARDS);
    vi.mocked(api.movePipelineCard).mockResolvedValue({
      property_id: 42,
      stage_id: 3,
      stage_key: 'offer',
    });
    vi.mocked(api.removePipelineCard).mockResolvedValue({ removed: true });
    vi.mocked(queries.fetchListingCovers).mockResolvedValue(new Map());
    vi.mocked(queries.fetchPropertySourcesByPropertyIds).mockResolvedValue(
      new Map([[42, [ad(111, 42)]]]),
    );
    vi.mocked(brokersApi.fetchListingBrokersByIds).mockResolvedValue(
      new Map([[111, listingBroker()]]),
    );
    vi.mocked(api.listCollections).mockResolvedValue({
      data: COLLECTIONS,
      total: COLLECTIONS.length,
    });
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
  });

  /* The card-size switch. jsdom lays nothing out, so what is pinned here is
     that the choice REACHES the board — column width, the card's own photo
     frame, and the stored preference — not how any of it looks; the geometry
     itself was measured in a real browser and is pinned in
     lib/pipelineCardSize.test.ts. */
  it('restyles the board and remembers the card size', async () => {
    renderBoard();
    await screen.findByLabelText('Přetáhnout kartu do jiné fáze');
    const column = () => screen.getByRole('list', { name: 'Zájem' });
    /* The header sits in its own pinned row, apart from its column, so the
       two only line up while they carry the same width at every size. */
    const header = () => screen.getByText('Zájem').parentElement;
    /* The cover read is mocked empty, so every card draws the placeholder
       frame — which carries exactly the geometry the photo would. */
    const thumb = () => document.querySelector('ul li div[aria-hidden]');

    expect(column().className).toContain('w-72');
    expect(header()?.className).toContain('w-72');
    expect(thumb()?.className).toContain('h-12 w-12');

    fireEvent.click(screen.getByRole('button', { name: 'Velké' }));
    expect(column().className).toContain('w-[26rem]');
    expect(header()?.className).toContain('w-[26rem]');
    /* lg is a different card design, not a scaled one: the photo spans the
       card instead of sitting in a fixed square beside the text. */
    expect(thumb()?.className).toContain('aspect-[16/10]');
    expect(localStorage.getItem('sreality.pipeline.cardSize')).toBe('lg');

    fireEvent.click(screen.getByRole('button', { name: 'Střední' }));
    expect(column().className).toContain('w-[24rem]');
    expect(header()?.className).toContain('w-[24rem]');
    expect(thumb()?.className).toContain('h-24 w-24');
    expect(localStorage.getItem('sreality.pipeline.cardSize')).toBe('md');
  });

  /* jsdom lays nothing out, so the pin itself was checked in a real browser;
     what this holds is the wiring. The header row must sit OUTSIDE the
     sideways scroller (inside it, `sticky` pins to the scroller, never the
     page), which means it has to be carried along by hand when the columns
     scroll. */
  it('pins the stage headers above the columns and scrolls them together', async () => {
    renderBoard();
    const headerRow = (await screen.findByText('Zájem')).parentElement!.parentElement!;
    const columns = screen.getByRole('list', { name: 'Zájem' }).parentElement!;

    expect(headerRow.className).toContain('sticky');
    expect(columns.contains(headerRow)).toBe(false);
    expect(headerRow).toHaveTextContent('Nabídka');

    Object.defineProperty(headerRow, 'scrollLeft', { value: 0, writable: true });
    Object.defineProperty(columns, 'scrollLeft', { value: 240, writable: true });
    fireEvent.scroll(columns);
    expect(headerRow.scrollLeft).toBe(240);
  });

  it('renders draggable cards with a drag handle + enriched content', async () => {
    renderBoard();
    // One card → one grip handle; proves the column + draggable card mounted.
    expect(
      await screen.findByLabelText('Přetáhnout kartu do jiné fáze'),
    ).toBeInTheDocument();
    // Both stage columns render their header label.
    expect(screen.getByText('Zájem')).toBeInTheDocument();
    expect(screen.getByText('Nabídka')).toBeInTheDocument();
    // No terminal stage, no folded lane: the board is its live columns only.
    expect(screen.queryByText('Uzavřené')).toBeNull();
    // Enriched card content: street + broker name linking to the broker page.
    expect(screen.getByText('Sadová')).toBeInTheDocument();
    /* The broker line is a DECORATION now: the card paints without it and it
       arrives on its own query, so this is findBy (async) rather than getBy.
       That asymmetry is the feature — the assertions above it all passed
       before this resolves. */
    const broker = await screen.findByText('Jan Novák');
    expect(broker.closest('a')).toHaveAttribute('href', '/brokers/7');
    expect(broker.closest('a')).toHaveAttribute('target', '_blank');
    expect(broker.closest('a')).toHaveClass('card-action');
    expect(broker.closest('a')).toHaveAttribute(
      'title',
      expect.stringContaining('jan@remax.cz'),
    );
  });

  /* The board is a triage surface worked a column at a time, so following a
     card must not unload it — the property link opens in a NEW TAB. `rel` rides
     along so the opened document can't reach back through `window.opener`.
     The link is the address, which leads the card, and it is stretched over
     the whole card (`.stretched-link`), so the card opens from anywhere; the
     controls sit above it (`card-action`). The price is a figure inside the
     card, never a second link. */
  it('opens the property in a new tab from anywhere on the card', async () => {
    renderBoard();
    const place = await screen.findByText('Sadová');
    const link = place.closest('a');
    expect(link).toHaveAttribute('href', '/property/42');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(link).toHaveClass('stretched-link');
    for (const name of ['Přetáhnout kartu do jiné fáze', 'Odebrat z pipeline']) {
      expect(screen.getAllByRole('button', { name })[0]).toHaveClass('card-action');
    }

    const price = screen.getByText(/5\s*000\s*000/);
    expect(price.closest('a')).toBeNull();
    expect(
      place.compareDocumentPosition(price) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  /* The address is the card's only link, so a property whose place never
     resolved must still offer something to click. */
  it('still links a card whose place is unresolved', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([
      { ...CARDS[0], display_label: null, obec: null },
    ]);
    renderBoard();
    const link = (await screen.findByText('Lokalita neurčena')).closest('a');
    expect(link).toHaveAttribute('href', '/property/42');
  });

  /* The town is the second row on EVERY card, on a line of its own so a long
     street can never truncate it away. */
  it('prints the town on its own second row, after the street link', async () => {
    renderBoard();
    const street = await screen.findByText('Sadová');
    const town = screen.getByText('Praha', { selector: 'p' });
    expect(town.closest('a')).toBeNull();
    expect(street.closest('p')).not.toBe(town);
    expect(street.closest('p')?.nextElementSibling).toBe(town);
  });

  it('keeps the town row on a card whose label is only the town', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([
      { ...CARDS[0], display_label: 'Praha' },
    ]);
    renderBoard();
    const street = await screen.findByText('Ulice neuvedena');
    expect(street.closest('a')).toHaveAttribute('href', '/property/42');
    expect(street.closest('p')?.nextElementSibling).toHaveTextContent('Praha');
  });

  /* The point of the split, pinned: with BOTH decoration reads hanging
     forever, the board is still fully rendered and interactive. Before the
     split this was six serialized round trips and a "Načítání…" string — a
     single slow broker call blanked the entire kanban. */
  it('paints cards while cover and broker reads are still in flight', async () => {
    vi.mocked(queries.fetchListingCovers).mockReturnValue(
      new Promise(() => {}) as ReturnType<typeof queries.fetchListingCovers>,
    );
    vi.mocked(brokersApi.fetchListingBrokersByIds).mockReturnValue(
      new Promise(() => {}) as ReturnType<typeof brokersApi.fetchListingBrokersByIds>,
    );
    renderBoard();

    // Structure: columns, the card, its price, place and drag handle.
    expect(
      await screen.findByLabelText('Přetáhnout kartu do jiné fáze'),
    ).toBeInTheDocument();
    expect(screen.getByText('Zájem')).toBeInTheDocument();
    expect(screen.getByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText(/5\s*000\s*000/)).toBeInTheDocument();
    // Decoration: absent, and no loading string stands in for the board.
    expect(screen.queryByText('Jan Novák')).not.toBeInTheDocument();
    expect(screen.queryByText('Načítání…')).not.toBeInTheDocument();
  });

  /* MS7: the property's list, compact — the canonical ad's broker first, "+N"
     for the rest (an inactive ad's hidden while one is active), from two batched
     reads for the whole board. */
  it('shows the first broker of the property and counts the others', async () => {
    const other = (listing_id: number, broker_id: number, broker_display_name: string) =>
      ({ ...listingBroker(), listing_id, broker_id, broker_display_name });
    vi.mocked(queries.fetchPropertySourcesByPropertyIds)
      .mockClear()
      .mockResolvedValue(new Map([[42, [ad(110, 42), ad(111, 42), ad(112, 42, false)]]]));
    vi.mocked(brokersApi.fetchListingBrokersByIds).mockClear().mockResolvedValue(
      new Map([[110, other(110, 8, 'Eva Malá')], [111, listingBroker()], [112, other(112, 9, 'Petr')]]),
    );
    renderBoard();
    expect((await screen.findByText('Jan Novák')).closest('a')).toHaveAttribute('href', '/brokers/7');
    expect(screen.getByText('+1')).toHaveAttribute('title', 'Eva Malá');
    expect(queries.fetchPropertySourcesByPropertyIds).toHaveBeenCalledTimes(1);
    expect(brokersApi.fetchListingBrokersByIds).toHaveBeenCalledWith([110, 111, 112]);
  });

  /* MS16: a failed board read says so and offers a retry that reads again. */
  it('offers a retry when the board cannot be read', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockRejectedValueOnce(new Error('HTTP 500'));
    renderBoard();
    const retry = await screen.findByRole('button', { name: 'Zkusit znovu' });
    expect(screen.queryByText('Sadová')).not.toBeInTheDocument();
    fireEvent.click(retry);
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
  });

  /* A new account's board is its stage columns, empty, under the bar a full
     board has — never a sentence in their place. */
  it('draws every stage column and the bar for an empty board', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([]);
    renderBoard();
    for (const s of STAGES) {
      expect(await screen.findByRole('list', { name: s.label })).toBeInTheDocument();
    }
    expect(screen.getByText(/nemovitostí/).textContent).toBe('0 nemovitostí');
    expect(screen.queryByText(/Zatím prázdné/)).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Lokalita' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Řazení karet ve fázi' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Karty' })).toBeInTheDocument();
  });

  /* The frame waits for BOTH reads: a board that lands before its stages
     would otherwise paint no columns at all. */
  it('holds the skeleton until the stages land too', async () => {
    vi.mocked(queries.fetchPipelineStages).mockReturnValue(new Promise(() => {}));
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([]);
    renderBoard();
    await waitFor(() =>
      expect(screen.getByText(/nemovitostí/).textContent).toBe('0 nemovitostí'),
    );
    expect(screen.getByText('Načítání pipeline…')).toBeInTheDocument();
  });

  it('holds the skeleton, with the stages drawn, while the board is in flight', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockReturnValue(new Promise(() => {}));
    renderBoard();
    for (const s of STAGES) expect(await screen.findByText(s.label)).toBeInTheDocument();
    expect(screen.getByText('Načítání pipeline…')).toBeInTheDocument();
    expect(screen.getByText(/nemovitostí/).textContent).toBe('— nemovitostí');
  });

  it('filters the board by property type', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    renderBoard();
    // Both cards render; the type chips appear (≥2 types present).
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText('Lesní')).toBeInTheDocument();
    await openMenu('Typ');
    const domy = screen.getByRole('button', { name: 'Domy' });
    expect(screen.getByRole('button', { name: 'Byty' })).toBeInTheDocument();
    // Filter to Domy → only the dům card remains.
    fireEvent.click(domy);
    await waitFor(() =>
      expect(screen.queryByText('Sadová')).not.toBeInTheDocument(),
    );
    expect(screen.getByText('Lesní')).toBeInTheDocument();
  });

  /* The board filters its cards in the BROWSER over the same five codes the
     server-side predicate uses: a street chip keeps the cards on that street,
     not every card in its town. */
  it('filters the board by a street chip', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([
      { ...CARDS[0], ulice_id: 700001 },
      { ...CARD_DUM, obec_id: 554782, ulice_id: null },
    ]);
    renderBoard(
      '/pipeline?districts=Sadov%C3%A1&districts_ctx=Praha&districts_lvl=ulice&districts_id=700001',
    );
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.queryByText('Lesní')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Sadová · Praha (Ulice)' })).toBeInTheDocument();
  });

  it('filters the board by active/inactive status', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_INACTIVE]);
    renderBoard();
    // Both cards render; the status pills appear (a delisted card is present).
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText('Polní')).toBeInTheDocument();
    const aktivni = screen.getByRole('button', { name: 'Aktivní' });
    const neaktivni = screen.getByRole('button', { name: 'Neaktivní' });
    // Filter to Aktivní → only the live card remains.
    fireEvent.click(aktivni);
    await waitFor(() =>
      expect(screen.queryByText('Polní')).not.toBeInTheDocument(),
    );
    expect(screen.getByText('Sadová')).toBeInTheDocument();
    // Switching to Neaktivní flips which card is visible.
    fireEvent.click(neaktivni);
    await waitFor(() =>
      expect(screen.queryByText('Sadová')).not.toBeInTheDocument(),
    );
    expect(screen.getByText('Polní')).toBeInTheDocument();
  });

  /* The collection lens (rule #18): the fail-open contract, on both ways the
     member map can be missing — in flight here, errored in the next case. */
  it('applies no collection constraint and offers no row while membership is unresolved', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockReturnValue(
      new Promise(() => {}),
    );
    renderBoard('/pipeline?collections=7');
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText('Lesní')).toBeInTheDocument();
    expect(screen.queryByText('Kolekce')).not.toBeInTheDocument();
    // Uncounted: the header reads the plain total, and there is nothing to reset.
    expect(screen.getByText(/nemovitostí/).textContent).toBe('2 nemovitostí');
    expect(screen.queryByRole('button', { name: 'Zrušit filtry' })).not.toBeInTheDocument();
  });

  /* ...and says so (MS16): a retry where the row sits, after which the
     constraint applies. */
  it('applies no collection constraint when the member map errors, and offers a retry', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockRejectedValue(new Error('403'));
    renderBoard('/pipeline?collections=7');
    await openMenu('Kolekce');
    const retry = await screen.findByRole('button', { name: /Kolekce se nepodařilo načíst/ });
    expect(screen.getByText(/nemovitostí/).textContent).toBe('2 nemovitostí');
    expect(screen.getByText('Sadová')).toBeInTheDocument();
    /* The retry stands in the row's place: no chip while the map is missing. */
    expect(screen.queryByRole('button', { name: 'V žádné kolekci' })).not.toBeInTheDocument();
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    fireEvent.click(retry);
    await waitFor(() =>
      expect(screen.getByText(/nemovitostí/).textContent).toBe('1 z 2 nemovitostí'),
    );
  });

  /* A failed collections LIST is a retry too — never "#7" chips filed under
     "No longer available", which would read as a deleted collection. */
  it('offers a retry, not placeholder chips, when the collections list fails', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    vi.mocked(api.listCollections).mockRejectedValue(new Error('HTTP 500'));
    renderBoard('/pipeline?collections=7');
    await openMenu('Kolekce');
    expect(await screen.findByRole('button', { name: /Kolekce se nepodařilo načíst/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '#7' })).not.toBeInTheDocument();
  });

  /* While the collections list is still on its way the row stands on the member
     map: a selection is a live constraint, so its chip renders (pressed, under
     a placeholder name) before the list arrives — otherwise the only way out is
     Reset. */
  it('offers the selected collection before the collections list resolves', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(
      new Map([[42, [7]]]),
    );
    vi.mocked(api.listCollections).mockReturnValue(
      new Promise(() => {}) as ReturnType<typeof api.listCollections>,
    );
    renderBoard('/pipeline?collections=7');
    await openMenu('Kolekce');
    expect(await screen.findByRole('button', { name: '#7' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Kolekce · 1' })).toBeInTheDocument();
    expect(screen.getByText(/nemovitostí/).textContent).toBe('1 z 2 nemovitostí');
  });

  it('narrows the board to a collection and counts it in the header', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(
      new Map([[42, [7]]]),
    );
    renderBoard();
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    // Only the collection with a member ON the board is offered.
    await openMenu('Kolekce');
    const chip = await screen.findByRole('button', { name: 'Šortlist' });
    expect(screen.queryByRole('button', { name: 'Archiv' })).not.toBeInTheDocument();
    fireEvent.click(chip);
    await waitFor(() =>
      expect(screen.queryByText('Lesní')).not.toBeInTheDocument(),
    );
    expect(screen.getByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText(/nemovitostí/).textContent).toBe('1 z 2 nemovitostí');
  });

  /* Ruling 10 (2026-10-19): the bar's Kolekce row is the one curated-set
     control, its "not in" chip included. */
  it('narrows the board to the cards in no collection through "V žádné kolekci"', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(
      new Map([[42, [7]]]),
    );
    renderBoard();
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    await openMenu('Kolekce');
    fireEvent.click(await screen.findByRole('button', { name: 'V žádné kolekci' }));
    await waitFor(() => expect(screen.queryByText('Sadová')).not.toBeInTheDocument());
    expect(screen.getByText('Lesní')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Šortlist' })).toBeDisabled();
    expect(screen.getByText(/nemovitostí/).textContent).toBe('1 z 2 nemovitostí');
  });

  it('names a collection shared with me by its owner', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [{ ...collection(9, 'Od Petra'), owner_email: 'petr@example.cz' }],
      total: 1,
    });
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(
      new Map([[42, [9]]]),
    );
    renderBoard();
    await openMenu('Kolekce');
    expect(
      await screen.findByRole('button', { name: 'Od Petra · petr@example.cz' }),
    ).toBeInTheDocument();
  });

  /* A resolved selection that matches nothing is ZERO cards, never the whole
     board — the failure mode that makes a filter silently meaningless. */
  it('shows no cards for a resolved collection nothing on the board is in', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(
      new Map([[42, [7]]]),
    );
    renderBoard('/pipeline?collections=8');
    await waitFor(() =>
      expect(screen.getByText(/nemovitostí/).textContent).toBe('0 z 2 nemovitostí'),
    );
    expect(screen.queryByText('Sadová')).not.toBeInTheDocument();
    expect(screen.queryByText('Lesní')).not.toBeInTheDocument();
    // Its chip renders pressed although nothing on the board is in it: a
    // constraint you cannot see is one you can only leave through Zrušit filtry.
    await openMenu('Kolekce');
    expect(screen.getByRole('button', { name: 'Archiv' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  /* Stav defaults to 'any' precisely so a delisted member of a collection is
     still in the cohort — a shortlist you can't see the dead deals in is a
     different question than the one the operator asked. */
  it('keeps a delisted member visible under a collection filter', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_INACTIVE]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(
      new Map([[44, [7]]]),
    );
    renderBoard('/pipeline?collections=7');
    expect(await screen.findByText('Polní')).toBeInTheDocument();
    expect(screen.queryByText('Sadová')).not.toBeInTheDocument();
  });

  it('clears every filter through the one Zrušit filtry', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(
      new Map([[42, [7]]]),
    );
    renderBoard('/pipeline?cat=dum&collections=7');
    // Type and collection disagree, so the board is empty until it is cleared.
    const reset = await screen.findByRole('button', { name: 'Zrušit filtry' });
    expect(screen.queryByText('Sadová')).not.toBeInTheDocument();
    fireEvent.click(reset);
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText('Lesní')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Zrušit filtry' })).not.toBeInTheDocument();
  });

  /* The menus only count their choices; the row under the bar names each one,
     and its × takes that one back. */
  it('names every active filter under the bar, each removable on its own', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [7]]]));
    renderBoard('/pipeline?cat=dum&collections=7');
    const row = await screen.findByRole('list', { name: 'Aktivní filtry' });
    await waitFor(() =>
      expect(within(row).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Domy', 'Kolekce: Šortlist']),
    );
    expect(screen.getByRole('button', { name: 'Typ · 1' })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: 'Zrušit filtr Domy' }));
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.queryByText('Lesní')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Typ' })).toBeInTheDocument();
  });

  /* D2: a stage edit re-reads what lib/curationSync's `pipeline_stages` row feeds —
     the members map too, whose stage label, colour and code every funnel badge renders. */
  it('a new stage re-reads the stages, the board and the members map', async () => {
    vi.mocked(api.createPipelineStage).mockResolvedValue(STAGES[0]);
    const qc = boardClient();
    qc.setQueryData(pipelineKeys.members, new Map());
    renderBoard('/pipeline', qc);
    fireEvent.click(await screen.findByRole('button', { name: 'Spravovat fáze' }));
    const reads = watchReads(qc);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Nová fáze' }), { target: { value: 'Nová' } });
    fireEvent.click(screen.getByRole('button', { name: 'Přidat' }));
    await waitFor(() => expect(reads.reread(pipelineKeys.members)).toBe(true));
    expect(reads.reread(pipelineKeys.stages)).toBe(true);
    expect(reads.reread(pipelineKeys.board)).toBe(true);
    reads.stop();
  });

  /* D3 (lib/useServerDraft): a stage field follows the server while untouched, so
     a rename made elsewhere shows in the open editor and a focus and blur of the
     untouched field writes nothing back over it. */
  it('an outside rename followed by focus and blur of an untouched field writes nothing (D3)', async () => {
    const qc = boardClient();
    renderBoard('/pipeline', qc);
    fireEvent.click(await screen.findByRole('button', { name: 'Spravovat fáze' }));
    const label = await screen.findByDisplayValue('Nabídka');
    const code = within(label.closest('li')!).getByRole('textbox', { name: 'Značka fáze' });

    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(
      STAGES.map((st) => (st.id === 3 ? { ...st, label: 'Nabídka (jinde)', code: '3' } : st)),
    );
    await act(async () => {
      await revalidateTables(qc, ['pipeline_stages']);
    });
    await waitFor(() => expect(label).toHaveValue('Nabídka (jinde)'));
    expect(code).toHaveValue('3');

    fireEvent.focus(label);
    fireEvent.blur(label);
    fireEvent.focus(code);
    fireEvent.blur(code);
    expect(api.updatePipelineStage).not.toHaveBeenCalled();
  });

  it('keeps a typed stage name over a change made elsewhere, and saves only that name', async () => {
    vi.mocked(api.updatePipelineStage).mockResolvedValue({ ...STAGES[1], label: 'Moje nabídka' });
    const qc = boardClient();
    renderBoard('/pipeline', qc);
    fireEvent.click(await screen.findByRole('button', { name: 'Spravovat fáze' }));
    const label = await screen.findByDisplayValue('Nabídka');
    fireEvent.focus(label);
    fireEvent.change(label, { target: { value: 'Moje nabídka ' } });

    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(
      STAGES.map((st) => (st.id === 3 ? { ...st, label: 'Nabídka (jinde)', code: '3' } : st)),
    );
    await act(async () => {
      await revalidateTables(qc, ['pipeline_stages']);
    });
    expect(label).toHaveValue('Moje nabídka ');

    fireEvent.blur(label);
    await waitFor(() => expect(api.updatePipelineStage).toHaveBeenCalledWith(3, { label: 'Moje nabídka' }));
    expect(api.updatePipelineStage).toHaveBeenCalledTimes(1);
  });

  it('names the stage-manager create field by its visible words', async () => {
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: 'Spravovat fáze' }));
    const create = await screen.findByRole('textbox', { name: 'Nová fáze' });
    expect(create).toHaveAccessibleName('Nová fáze');
  });

  /* Section cards (operator ruling 2026-10-28): the stage manager is one card like
     Sdílení — its title the region's name, the stages its count, never a small-caps label.
     Adding a stage stays the body's last row: a title bar holds buttons and links only (C6). */
  it('opens the stage manager as the Fáze pipeline section card, its count the stages', async () => {
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: 'Spravovat fáze' }));
    const card = await screen.findByRole('region', { name: 'Fáze pipeline' });
    expect(card.tagName).toBe('SECTION');
    expect(card).toHaveClass('border', 'bg-[var(--color-paper-2)]', 'rounded-[var(--radius-md)]');
    const title = within(card).getByRole('heading', { level: 2, name: 'Fáze pipeline' });
    expect(title).not.toHaveClass('uppercase');
    expect(title.nextElementSibling).toHaveTextContent(String(STAGES.length));
    const bar = title.parentElement!;
    expect(within(bar).queryByRole('textbox', { name: 'Nová fáze' })).toBeNull();
    expect(within(card).getByRole('textbox', { name: 'Nová fáze' })).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Přidat' })).not.toHaveClass('uppercase');
    expect(within(card).getByDisplayValue('Nabídka')).toBeInTheDocument();
  });

  it('trash → confirm removes the card via removePipelineCard', async () => {
    renderBoard();
    const trash = await screen.findByLabelText('Odebrat z pipeline');
    fireEvent.click(trash);
    // Inline two-step confirm appears; nothing removed until confirmed.
    expect(api.removePipelineCard).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Odebrat'));
    await waitFor(() =>
      expect(api.removePipelineCard).toHaveBeenCalledWith(42),
    );
  });

  /* The board writes through the same hook as every funnel, so a refused
   * removal snaps back from the rollback (the re-read is held open here, so
   * nothing else could restore it) and says why exactly once. */
  it('puts a card back when its removal fails, and says why once', async () => {
    vi.mocked(toast.pushToast).mockClear();
    let reject!: (e: Error) => void;
    vi.mocked(api.removePipelineCard).mockReturnValue(
      new Promise((_res, rej) => {
        reject = rej;
      }),
    );
    vi.mocked(queries.fetchPipelineBoard)
      .mockResolvedValueOnce(CARDS)
      .mockReturnValue(new Promise(() => {}));
    renderBoard();
    fireEvent.click(await screen.findByLabelText('Odebrat z pipeline'));
    fireEvent.click(screen.getByText('Odebrat'));
    await waitFor(() => expect(api.removePipelineCard).toHaveBeenCalledWith(42));
    await waitFor(() => expect(screen.queryByText('Sadová')).not.toBeInTheDocument());

    await act(async () => reject(new Error('refused')));
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(toast.pushToast).toHaveBeenCalledTimes(1);
    expect(toast.pushToast).toHaveBeenCalledWith('err', 'refused');
  });

  /* Tags on the board (rule #18, the collections' twin): the card prints its
     tags as chips, the chips open the one assign list, and the bar's tri-state
     chips narrow the board to a tag or to the cards without it. */
  it('prints a card\'s tags as chips, and the chips open the assign list', async () => {
    vi.mocked(queries.fetchPropertyTagMemberSet).mockResolvedValue(new Map([[42, [5]]]));
    renderBoard();
    const chips = await screen.findByRole('button', { name: 'Štítky: k prohlídce' });
    expect(within(chips).getByText('k prohlídce')).toBeInTheDocument();
    fireEvent.click(chips);
    const panel = await screen.findByRole('group', { name: 'Štítky' });
    expect(within(panel).getByRole('textbox', { name: 'Najít nebo vytvořit štítek' })).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole('button', { name: /k prohlídce/, pressed: true }));
    await waitFor(() => expect(api.detachTag).toHaveBeenCalledWith(42, 5));
    expect(await screen.findByRole('button', { name: 'Štítky: k prohlídce' })).toBeInTheDocument();
  });

  it('offers an untagged card the add verb', async () => {
    renderBoard();
    const add = await screen.findByRole('button', { name: 'Přidat štítek' });
    fireEvent.click(add);
    const panel = await screen.findByRole('group', { name: 'Štítky' });
    fireEvent.click(within(panel).getByRole('button', { name: /odmítnuto/, pressed: false }));
    await waitFor(() => expect(api.attachTag).toHaveBeenCalledWith(42, 6));
  });

  it('narrows the board to a tag, then to the cards without it, through one chip', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyTagMemberSet).mockResolvedValue(new Map([[42, [5]]]));
    renderBoard();
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    // Only the tag with a member ON the board is offered.
    const bar = await openMenu('Štítky');
    const chip = within(bar).getByRole('button', { name: 'k prohlídce' });
    expect(within(bar).queryByRole('button', { name: 'odmítnuto' })).toBeNull();

    fireEvent.click(chip);
    await waitFor(() => expect(screen.queryByText('Lesní')).not.toBeInTheDocument());
    expect(screen.getByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText(/nemovitostí/).textContent).toBe('1 z 2 nemovitostí');

    fireEvent.click(within(bar).getByRole('button', { name: 'k prohlídce' }));
    await waitFor(() => expect(screen.queryByText('Sadová')).not.toBeInTheDocument());
    expect(screen.getByText('Lesní')).toBeInTheDocument();
    expect(within(bar).getByRole('button', { name: 'k prohlídce (excluded)' })).toBeInTheDocument();

    fireEvent.click(within(bar).getByRole('button', { name: 'k prohlídce (excluded)' }));
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText('Lesní')).toBeInTheDocument();
  });

  it('applies no tag constraint while the member map is unresolved, and offers a retry when it fails', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(queries.fetchPropertyTagMemberSet).mockRejectedValue(new Error('HTTP 500'));
    renderBoard('/pipeline?tags=5');
    const retries = await screen.findAllByRole('button', { name: /Štítky se nepodařilo načíst/ });
    expect(screen.getByText('Sadová')).toBeInTheDocument();
    expect(screen.getByText('Lesní')).toBeInTheDocument();
    // Uncounted: the header reads the plain total while the constraint waits.
    expect(screen.getByText(/nemovitostí/).textContent).toBe('2 nemovitostí');
    vi.mocked(queries.fetchPropertyTagMemberSet).mockResolvedValue(new Map([[42, [5]]]));
    fireEvent.click(retries[0]);
    await waitFor(() => expect(screen.queryByText('Lesní')).not.toBeInTheDocument());
    expect(screen.getByText('Sadová')).toBeInTheDocument();
  });

  /* The property's Artifact link (migration 599) on its card: a small button to
     the page the operator keeps about it, in a new tab, only when there is one. */
  it('shows a card\'s artifact link as a small button that opens in a new tab', async () => {
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_DUM]);
    vi.mocked(api.fetchArtifactLinks).mockResolvedValue(
      new Map([[42, { property_id: 42, url: 'https://claude.ai/artifact/abc123', created_at: '2026-10-14T00:00:00Z', updated_at: '2026-10-14T00:00:00Z' }]]),
    );
    renderBoard();
    const button = await screen.findByRole('link', { name: 'Artifact: claude.ai/artifact/abc123' });
    expect(button).toHaveAttribute('href', 'https://claude.ai/artifact/abc123');
    expect(button).toHaveAttribute('target', '_blank');
    expect(button).toHaveAttribute('rel', 'noopener noreferrer');
    expect(button).toHaveTextContent('Artifact');
    // The card without a link offers nothing.
    expect(screen.getAllByRole('link', { name: /^Artifact:/ })).toHaveLength(1);
  });

  /* P19 (2026-10-27, widened by the operator): no city-index strip on the
     card at all, so the board stops reading the indexes too. Praha has
     readings here, so before the ruling the card in it drew the strip. */
  it('draws no city-index strip on a card in a curated city, and reads no city indexes', async () => {
    const city = {
      city_id: 1,
      name: 'Praha',
      kraj_name: 'Hlavní město Praha',
      lat: 50.08,
      lng: 14.43,
      default_radius_m: 10_000,
      population: 1_300_000,
      population_as_of_year: 2024,
      admin_boundary_id: 554782,
    } as CuratedCity;
    const def = {
      index_name: 'celkove_hodnoceni',
      label_cs: 'Celkové hodnocení',
      label_en: null,
      category: 'overall',
      scale_min: 0,
      scale_max: 10,
      higher_is_better: true,
      sort_order: 0,
    } as CityIndexDefinition;
    const fetchers = [
      queries.fetchCuratedCities,
      queries.fetchCityIndexDefinitions,
      queries.fetchCityIndexValues,
    ] as const;
    for (const fn of fetchers) vi.mocked(fn).mockClear();
    vi.mocked(queries.fetchCuratedCities).mockResolvedValue([city]);
    vi.mocked(queries.fetchCityIndexDefinitions).mockResolvedValue([def]);
    vi.mocked(queries.fetchCityIndexValues).mockResolvedValue([
      { city_id: 1, index_name: 'celkove_hodnoceni', value: 7.4 },
    ]);
    renderBoard();
    expect(await screen.findByText('Jan Novák')).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /Kvalita obce/ })).toBeNull();
    for (const fn of fetchers) expect(fn).not.toHaveBeenCalled();
  });

  /* P20: the MF yield beside the dims line is a Velké-card detail. */
  it('shows the MF yield on Velké cards only', async () => {
    renderBoard();
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.queryByText(/Výnos MF\s*4,3\s*%/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Střední' }));
    expect(screen.queryByText(/Výnos MF\s*4,3\s*%/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Velké' }));
    expect(screen.getByText(/Výnos MF\s*4,3\s*%/)).toHaveAttribute(
      'title',
      'Hrubý výnos dle cenové mapy nájemného MF (nájem ÷ cena)',
    );
  });

  /* P22: the terminal stages fold into ONE narrow lane — a row per closed
     stage with its count — after the live columns. */
  it('folds the terminal stages into one Uzavřené lane, a row and count per closed stage', async () => {
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES_CLOSED);
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_LOST]);
    renderBoard();
    expect(await screen.findByRole('list', { name: 'Zájem' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Nabídka' })).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: '9. Lost' })).toBeNull();
    expect(screen.queryByRole('list', { name: '9. Bought' })).toBeNull();

    const headerRow = headerRowOf('Zájem');
    expect(within(headerRow).getByText('Uzavřené')).toBeInTheDocument();
    expect(within(headerRow).queryByText('9. Lost')).toBeNull();
    expect(within(headerRow).queryByText('9. Bought')).toBeNull();

    const lane = screen.getByRole('list', { name: 'Uzavřené' });
    const rows = within(lane).getAllByRole('button');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAccessibleName('9. Lost · 1');
    expect(rows[1]).toHaveAccessibleName('9. Bought · 0');
    for (const row of rows) expect(row).toHaveAttribute('aria-expanded', 'false');
    // Folded: the closed card is counted, not drawn.
    expect(screen.queryByText('Luční')).toBeNull();
    // The page's own count still holds every card on the board.
    expect(screen.getByText(/nemovitostí/).textContent).toBe('2 nemovitostí');
  });

  /* A row opens its stage's cards in the lane, under the rows, which never
     move; one at a time. The lane widens to a column then, header and body
     from one value so the pinned header row still scrolls with the board. */
  it("opens a closed stage's cards from its row, one at a time, and folds them back", async () => {
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES_CLOSED);
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_LOST]);
    renderBoard();
    const lost = await screen.findByRole('button', { name: '9. Lost · 1' });
    const bought = screen.getByRole('button', { name: '9. Bought · 0' });
    const header = screen.getByText('Uzavřené').parentElement!;
    const body = screen.getByRole('list', { name: 'Uzavřené' }).parentElement!;
    expect(header.className).toContain(PIPELINE_CLOSED_LANE_WIDTH);
    expect(body.className).toContain(PIPELINE_CLOSED_LANE_WIDTH);
    expect(lost).not.toHaveAttribute('aria-controls');

    fireEvent.click(lost);
    expect(lost).toHaveAttribute('aria-expanded', 'true');
    const opened = screen.getByRole('list', { name: '9. Lost' });
    expect(lost).toHaveAttribute('aria-controls', opened.id);
    expect(within(opened).getByText('Luční')).toBeInTheDocument();
    // The same card as in any column: the handle, the trash, the broker line's slot.
    expect(within(opened).getByLabelText('Přetáhnout kartu do jiné fáze')).toBeInTheDocument();
    expect(within(opened).getByRole('button', { name: 'Odebrat z pipeline' })).toBeInTheDocument();
    expect(header.className).toContain('w-72');
    expect(body.className).toContain('w-72');
    // The rows stay first: a drop target never moves when a row opens.
    expect(
      screen.getByRole('list', { name: 'Uzavřené' }).compareDocumentPosition(opened) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    fireEvent.click(bought);
    expect(lost).toHaveAttribute('aria-expanded', 'false');
    expect(bought).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByRole('list', { name: '9. Lost' })).toBeNull();
    expect(screen.getByRole('list', { name: '9. Bought' })).toHaveTextContent('—');

    fireEvent.click(bought);
    expect(bought).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('list', { name: '9. Bought' })).toBeNull();
    expect(header.className).toContain(PIPELINE_CLOSED_LANE_WIDTH);
  });

  /* The rows count the FILTERED board, like the column headers do. */
  it('counts a closed row under the active filters', async () => {
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES_CLOSED);
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_LOST]);
    renderBoard();
    expect(await screen.findByRole('button', { name: '9. Lost · 1' })).toBeInTheDocument();
    await openMenu('Typ');
    fireEvent.click(screen.getByRole('button', { name: 'Byty' }));
    expect(await screen.findByRole('button', { name: '9. Lost · 0' })).toBeInTheDocument();
  });

  /* Rule #22: closing a deal is a MOVE into a terminal stage. Each row is a
     drop target under the stage's own droppable id, so a release on it goes
     through the same planMove → usePipelineCard.move as a column drop. */
  it('closes a deal by dropping it on a closed row: a stage move, never a remove', async () => {
    dnd.droppables.clear();
    vi.mocked(api.movePipelineCard).mockClear().mockResolvedValue({
      property_id: 42,
      stage_id: 10,
      stage_key: 'bought',
    });
    vi.mocked(api.removePipelineCard).mockClear();
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES_CLOSED);
    // The re-read is held open, so what the rows show is the move's own patch.
    vi.mocked(queries.fetchPipelineBoard)
      .mockResolvedValueOnce([CARDS[0], CARD_LOST])
      .mockReturnValue(new Promise(() => {}));
    renderBoard();
    expect(await screen.findByRole('button', { name: '9. Bought · 0' })).toBeInTheDocument();
    expect(dnd.droppables.get('stage:10')).toEqual({ closed: true });
    expect(dnd.droppables.get('stage:9')).toEqual({ closed: true });
    expect(dnd.droppables.has('stage:1')).toBe(true);
    expect(dnd.droppables.get('stage:1')).toBeUndefined();
    expect(dnd.context?.collisionDetection).toBe(boardCollision);

    await act(async () => {
      dnd.context?.onDragEnd?.({
        active: { id: 'card:42' },
        over: { id: 'stage:10' },
      } as unknown as DragEndEvent);
    });
    await waitFor(() => expect(api.movePipelineCard).toHaveBeenCalledWith(42, 10));
    expect(api.removePipelineCard).not.toHaveBeenCalled();
    expect(await screen.findByRole('button', { name: '9. Bought · 1' })).toBeInTheDocument();
    expect(screen.queryByText('Sadová')).toBeNull();
  });

  /* Decision 10 (docs/design/curation-sync.md): a drag holds the board, the
     members map and the stages from pick-up to drop, so nothing repaints under
     the card in hand; what was owed meanwhile lands after the drop. */
  describe('a drag holds the board', () => {
    const pickUp = () =>
      act(async () => {
        dnd.context?.onDragStart?.({ active: { id: 'card:42' } } as unknown as DragStartEvent);
      });

    it('a remote target during a drag lands after the drop', async () => {
      const qc = boardClient();
      renderBoard('/pipeline', qc);
      expect(await screen.findByText('Sadová')).toBeInTheDocument();
      await pickUp();

      /* Changed elsewhere: another card added, as the curation clock would re-read it. */
      const moved = { ...CARDS[0], stage_id: 3 };
      vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([moved, CARD_DUM]);
      const before = vi.mocked(queries.fetchPipelineBoard).mock.calls.length;
      await act(async () => {
        await revalidateTables(qc, ['property_pipeline', 'pipeline_stages']);
      });
      expect(queries.fetchPipelineBoard).toHaveBeenCalledTimes(before);
      expect(screen.queryByText('Lesní')).toBeNull();

      await act(async () => {
        dnd.context?.onDragEnd?.({
          active: { id: 'card:42' },
          over: { id: 'stage:3' },
        } as unknown as DragEndEvent);
      });
      await waitFor(() => expect(api.movePipelineCard).toHaveBeenCalledWith(42, 3));
      expect(await screen.findByText('Lesní')).toBeInTheDocument();
      /* The drag's parked read and the move's settle are one board read. */
      expect(queries.fetchPipelineBoard).toHaveBeenCalledTimes(before + 1);
    });

    it('a drag that starts while a board re-read is in flight shows no repaint mid-drag', async () => {
      const qc = boardClient();
      renderBoard('/pipeline', qc);
      expect(await screen.findByText('Sadová')).toBeInTheDocument();

      let land: (cards: PipelineBoardCard[]) => void = () => {};
      vi.mocked(queries.fetchPipelineBoard)
        .mockReturnValueOnce(new Promise((resolve) => (land = resolve)))
        .mockResolvedValue([CARDS[0], CARD_DUM]);
      act(() => {
        void revalidateTables(qc, ['property_pipeline']);
      });
      await pickUp();
      await act(async () => {
        land([CARDS[0], CARD_DUM]);
        await new Promise((resolve) => setTimeout(resolve, 0)); // query-core notifies on a timer
      });
      /* The read in flight at pick-up was cancelled and owed, never painted. */
      expect(qc.getQueryData<PipelineBoardCard[]>(pipelineKeys.board)?.some((c) => c.property_id === 43)).toBe(false);
      expect(screen.queryByText('Lesní')).toBeNull();

      await act(async () => {
        dnd.context?.onDragCancel?.({ active: { id: 'card:42' } } as unknown as DragCancelEvent);
      });
      expect(await screen.findByText('Lesní')).toBeInTheDocument();
    });
  });

  /* The skeleton promises "no reflow, no jump": with the stages warm it is
     already the live columns plus the folded lane, never a column per closed
     stage that then collapses. */
  it('draws the folded lane in the skeleton too', async () => {
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES_CLOSED);
    vi.mocked(queries.fetchPipelineBoard).mockReturnValue(new Promise(() => {}));
    renderBoard();
    expect(await screen.findByText('Uzavřené')).toBeInTheDocument();
    expect(screen.getByText('Načítání pipeline…')).toBeInTheDocument();
    expect(screen.getAllByText('9. Lost')).toHaveLength(1);
    expect(screen.getAllByText('9. Bought')).toHaveLength(1);
    const headerRow = headerRowOf('Uzavřené');
    expect(within(headerRow).getByText('Zájem')).toBeInTheDocument();
    expect(within(headerRow).queryByText('9. Lost')).toBeNull();
  });
});

/* P22's drop targeting. Rows are ~2rem tall and the drag ghost 4.5rem to
 * ~22rem, so an overlap rule would close a deal into whichever row the ghost
 * covered most. A row is hit by a POINT — the pointer, or on a keyboard drag
 * (no pointer) the ghost's centre; the columns keep their overlap rule. */
describe('boardCollision', () => {
  const box = (left: number, top: number, width: number, height: number): ClientRect => ({
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
  });
  const droppable = (id: string, closed: boolean) =>
    ({
      id,
      key: id,
      data: { current: closed ? { closed: true } : undefined },
      disabled: false,
      node: { current: null },
      rect: { current: null },
    }) as DroppableContainer;
  // The last live column, stretched to the board's height, then two rows of the lane.
  const containers = [droppable('stage:3', false), droppable('stage:9', true), droppable('stage:10', true)];
  const rects = new Map([
    ['stage:3', box(0, 0, 288, 1000)],
    ['stage:9', box(304, 0, 208, 32)],
    ['stage:10', box(304, 36, 208, 32)],
  ]);
  const active = { id: 'card:42', data: { current: undefined }, rect: { current: { initial: null, translated: null } } } as Active;
  const hit = (ghost: ClientRect, pointer: { x: number; y: number } | null) =>
    boardCollision({
      active,
      collisionRect: ghost,
      droppableRects: rects,
      droppableContainers: containers,
      pointerCoordinates: pointer,
    }).map((c) => c.id);

  it('closes into the row under the pointer, not the row the ghost covers most', () => {
    // The ghost covers row 9 for 32px and row 10 for 16px; the pointer is on row 10.
    expect(hit(box(310, -20, 256, 72), { x: 400, y: 50 })[0]).toBe('stage:10');
  });

  it('never picks a row the pointer is not on, falling back to the columns', () => {
    // The ghost spans the column's edge and both rows; the pointer is in the gap.
    expect(hit(box(250, 0, 256, 72), { x: 296, y: 50 })).toEqual(['stage:3']);
  });

  it('on a keyboard drag, closes into the row under the ghost\'s centre', () => {
    expect(hit(box(300, 16, 256, 72), null)[0]).toBe('stage:10');
  });

  it('on a keyboard drag, keeps the column a ghost mostly covers over a row its edge touches', () => {
    // Plain overlap would score row 9 (0.17) over the column (0.03).
    expect(hit(box(160, 0, 256, 72), null)).toEqual(['stage:3']);
  });
});

/* Pipeline sharing (migration 641, design §3.5): the switcher, a shared board
 * read through <ActingAs> (the owner's rows under the acting header), the read
 * board's gating, the edit board's tools, my own board's Sharing block, the
 * viewer's leave, and a revoked board falling back to my own. */
describe('<Pipeline> shared boards', () => {
  const READ = { account_id: 'owner-r', email: 'petr@example.cz', mode: 'read' as const };
  const EDIT = { account_id: 'owner-e', email: 'jana@example.cz', mode: 'edit' as const };

  beforeEach(() => {
    localStorage.clear();
    reloadActingStore();
    for (const fn of [
      toast.pushToast,
      api.listTags,
      api.fetchArtifactLinks,
      api.unsharePipeline,
      api.sharePipeline,
      api.createPipelineStage,
      api.removePipelineCard,
    ]) {
      vi.mocked(fn).mockClear();
    }
    vi.mocked(api.listPipelineShares).mockResolvedValue({ shared_with: [], shared_with_me: [READ, EDIT] });
    vi.mocked(api.listTags).mockResolvedValue({ data: TAGS });
    vi.mocked(queries.fetchPropertyTagMemberSet).mockResolvedValue(new Map([[42, [5]]]));
    vi.mocked(api.fetchArtifactLinks).mockResolvedValue(new Map());
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES);
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(CARDS);
    vi.mocked(queries.fetchListingCovers).mockResolvedValue(new Map());
    vi.mocked(queries.fetchPropertySourcesByPropertyIds).mockResolvedValue(new Map());
    vi.mocked(brokersApi.fetchListingBrokersByIds).mockResolvedValue(new Map());
    vi.mocked(api.listCollections).mockResolvedValue({ data: COLLECTIONS, total: COLLECTIONS.length });
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
  });
  afterEach(() => resetActiveBoard());

  it('lists my board and every board shared with me, and remembers the pick', async () => {
    renderBoard();
    const switcher = await screen.findByRole('group', { name: 'Zobrazený pipeline' });
    expect(within(switcher).getByRole('button', { name: 'Můj pipeline' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(switcher).getByRole('button', { name: 'jana@example.cz · úpravy' }));
    /* The page starts afresh on the picked board (keyed by it), switcher included. */
    const after = await screen.findByRole('group', { name: 'Zobrazený pipeline' });
    expect(within(after).getByRole('button', { name: 'jana@example.cz · úpravy' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(after).getByRole('button', { name: 'Můj pipeline' })).toHaveAttribute('aria-pressed', 'false');
    expect(JSON.parse(localStorage.getItem(ACTIVE_BOARD_KEY) ?? 'null')).toEqual(EDIT);
    /* The owner's board is read as the owner: the header rides every read. */
    await waitFor(() =>
      expect(queries.fetchPipelineBoard).toHaveBeenLastCalledWith(expect.objectContaining({ actAs: 'owner-e' })),
    );
  });

  it('drops my tag / collection scope on a switch — its ids are my sets, not the owner\'s', async () => {
    vi.mocked(queries.fetchPropertyTagMemberSet).mockImplementation(async (opts) =>
      opts?.actAs ? new Map() : new Map([[42, [5]]]),
    );
    renderBoard('/pipeline?tags=5');
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    const switcher = await screen.findByRole('group', { name: 'Zobrazený pipeline' });
    fireEvent.click(within(switcher).getByRole('button', { name: 'jana@example.cz · úpravy' }));
    await waitFor(() =>
      expect(queries.fetchPropertyTagMemberSet).toHaveBeenCalledWith(expect.objectContaining({ actAs: 'owner-e' })),
    );
    /* Kept, my tag 5 would match none of the owner's cards: an empty board. */
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Zrušit filtry' })).toBeNull();
  });

  it('shows a read board without drag, trash, stage editor, tags or sharing', async () => {
    setActiveBoard(READ);
    renderBoard();
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(queries.fetchPipelineStages).toHaveBeenLastCalledWith(expect.objectContaining({ actAs: 'owner-r' }));
    expect(screen.queryByLabelText('Přetáhnout kartu do jiné fáze')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Odebrat z pipeline' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Spravovat fáze' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Sdílení/ })).toBeNull();
    expect(screen.queryByText('k prohlídce')).toBeNull();
    /* The owner's curation is not readable under a read share: not even asked for. */
    expect(api.listTags).not.toHaveBeenCalledWith({ actAs: 'owner-r' });
    expect(api.fetchArtifactLinks).not.toHaveBeenCalledWith({ actAs: 'owner-r' });
    expect(screen.getByText('petr@example.cz')).toBeInTheDocument();
    expect(screen.getByText(/jen ke čtení/, { selector: 'span' })).toBeInTheDocument();
  });

  /* P22 on a read board: the rows open for reading, the cards in them carry
     no handle or trash, like every card on it. */
  it('opens a closed row on a read board for reading, with no drag or trash in it', async () => {
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES_CLOSED);
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue([CARDS[0], CARD_LOST]);
    setActiveBoard(READ);
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: '9. Lost · 1' }));
    const opened = screen.getByRole('list', { name: '9. Lost' });
    expect(within(opened).getByText('Luční')).toBeInTheDocument();
    expect(within(opened).queryByLabelText('Přetáhnout kartu do jiné fáze')).toBeNull();
    expect(within(opened).queryByRole('button', { name: 'Odebrat z pipeline' })).toBeNull();
  });

  it('gives an edit board drag, trash, the stage editor and the owner\'s tags — all as the owner', async () => {
    vi.mocked(api.createPipelineStage).mockResolvedValue(STAGES[0]);
    setActiveBoard(EDIT);
    renderBoard();
    expect(await screen.findByLabelText('Přetáhnout kartu do jiné fáze')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Odebrat z pipeline' })).toBeInTheDocument();
    expect(await screen.findByText('k prohlídce')).toBeInTheDocument();
    expect(api.listTags).toHaveBeenCalledWith({ actAs: 'owner-e' });
    expect(screen.queryByRole('button', { name: /^Sdílení/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Spravovat fáze' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Nová fáze' }), { target: { value: 'Prohlídka' } });
    fireEvent.click(screen.getByRole('button', { name: 'Přidat' }));
    await waitFor(() =>
      expect(api.createPipelineStage).toHaveBeenCalledWith({ label: 'Prohlídka' }, { actAs: 'owner-e' }),
    );
  });

  it('removes a card from an edit board as the owner', async () => {
    vi.mocked(api.removePipelineCard).mockResolvedValue({ removed: true });
    setActiveBoard(EDIT);
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: 'Odebrat z pipeline' }));
    fireEvent.click(screen.getByRole('button', { name: 'Odebrat' }));
    await waitFor(() => expect(api.removePipelineCard).toHaveBeenCalledWith(42, { actAs: 'owner-e' }));
  });

  it('leaves a shared board in two steps and lands back on my own', async () => {
    vi.mocked(api.unsharePipeline).mockResolvedValue({ deleted: true });
    setActiveBoard(READ);
    renderBoard();
    fireEvent.click(await screen.findByRole('button', { name: 'Odebrat z mého seznamu' }));
    expect(api.unsharePipeline).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Odebrat' }));
    await waitFor(() => expect(api.unsharePipeline).toHaveBeenCalledWith('owner-r', 'shared_with_me'));
    await waitFor(() => expect(getActiveBoard()).toBeNull());
  });

  it('falls back to my own board, with a toast, when the server no longer lists it', async () => {
    setActiveBoard({ account_id: 'gone', email: 'x@example.cz', mode: 'edit' });
    renderBoard();
    await waitFor(() => expect(getActiveBoard()).toBeNull());
    expect(toast.pushToast).toHaveBeenCalledWith('info', BOARD_GONE_MESSAGE);
  });

  it('falls back to my own board when the shared board reads no stages', async () => {
    vi.mocked(queries.fetchPipelineStages).mockImplementation(async ({ actAs } = {}) =>
      actAs ? [] : STAGES,
    );
    setActiveBoard(READ);
    renderBoard();
    await waitFor(() => expect(getActiveBoard()).toBeNull());
    expect(toast.pushToast).toHaveBeenCalledWith('info', BOARD_GONE_MESSAGE);
  });

  /* Ruling 5: a card or stage last changed by a delegate is stamped. My own
   * board names the delegate from my recipients; a shared board, where only my
   * own grant is known, shows the time alone. */
  describe('delegated stamps', () => {
    const STAMPED = [{ ...CARDS[0], edited_by_account_id: 'acc-b', edited_at: '2026-10-20T10:00:00Z' }];
    const STAMPED_STAGES = STAGES.map((st, i) =>
      i === 0 ? { ...st, edited_by_account_id: 'acc-b', edited_at: '2026-10-20T11:00:00Z' } : st,
    );

    it('names my recipient on my own board — on the card and in the stage editor', async () => {
      vi.mocked(api.listPipelineShares).mockResolvedValue({
        shared_with: [{ account_id: 'acc-b', email: 'eva@example.cz', mode: 'edit' }],
        shared_with_me: [],
      });
      vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(STAMPED);
      vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAMPED_STAGES);
      renderBoard();
      expect(await screen.findByText(/^upravil eva@example\.cz · /)).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Spravovat fáze' }));
      expect(await screen.findAllByText(/^upravil eva@example\.cz · /)).toHaveLength(2);
    });

    it('shows the time alone on a board shared with me', async () => {
      vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(STAMPED);
      setActiveBoard(READ);
      renderBoard();
      expect(await screen.findByText(/^upraveno · /)).toBeInTheDocument();
      expect(screen.queryByText(/^upravil /)).toBeNull();
    });
  });

  describe('my own board\'s Sharing block', () => {
    beforeEach(() => {
      vi.mocked(api.listPipelineShares).mockResolvedValue({
        shared_with: [{ account_id: 'acc-b', email: 'eva@example.cz', mode: 'edit' }],
        shared_with_me: [],
      });
    });

    it('shares with an email at a chosen mode, and shows a 422 under the field', async () => {
      vi.mocked(api.sharePipeline).mockRejectedValueOnce(new api.ApiError('No account uses this email.', 422, null));
      renderBoard();
      fireEvent.click(await screen.findByRole('button', { name: 'Sdílení · 1' }));
      fireEvent.change(screen.getByRole('textbox', { name: 'E-mail' }), { target: { value: 'nikdo@example.cz' } });
      fireEvent.click(screen.getByRole('button', { name: 'Úpravy' }));
      fireEvent.click(screen.getByRole('button', { name: 'Sdílet' }));
      expect(await screen.findByRole('alert')).toHaveTextContent('No account uses this email.');
      expect(api.sharePipeline).toHaveBeenCalledWith('nikdo@example.cz', 'edit');
    });

    it('lists recipients with their mode and stops sharing only after a confirm', async () => {
      vi.mocked(api.unsharePipeline).mockResolvedValue({ deleted: true });
      renderBoard();
      fireEvent.click(await screen.findByRole('button', { name: 'Sdílení · 1' }));
      const list = screen.getByRole('list', { name: 'Sdíleno s' });
      expect(within(list).getByText('Úpravy')).toBeInTheDocument();
      fireEvent.click(within(list).getByRole('button', { name: 'Přestat sdílet s eva@example.cz' }));
      expect(api.unsharePipeline).not.toHaveBeenCalled();
      fireEvent.click(within(list).getByRole('button', { name: 'Přestat sdílet' }));
      await waitFor(() => expect(api.unsharePipeline).toHaveBeenCalledWith('acc-b', 'shared_with'));
    });

    /* Section cards (operator ruling 2026-10-28): the block is one card, its
       title the region's name and the recipients its count. */
    it('opens as the Sdílení section card, its count the recipients', async () => {
      renderBoard();
      fireEvent.click(await screen.findByRole('button', { name: 'Sdílení · 1' }));
      const card = screen.getByRole('region', { name: 'Sdílení' });
      expect(card.tagName).toBe('SECTION');
      expect(card).toHaveClass('border', 'bg-[var(--color-paper-2)]', 'rounded-[var(--radius-md)]');
      expect(within(card).getByRole('heading', { level: 2, name: 'Sdílení' }).nextElementSibling)
        .toHaveTextContent('1');
      expect(card.parentElement).not.toHaveClass('border');
    });
  });
});

/* "Nová nemovitost" (docs/design/new-property-from-link.md §3.6): the header's
 * paste box, mounted under the Shell's provider. The card lands on the board on
 * screen — the owner's on an edit share — and the page stays, with a toast. A
 * read board offers no button. */
describe('<Pipeline> Nová nemovitost', () => {
  const READ = { account_id: 'owner-r', email: 'petr@example.cz', mode: 'read' as const };
  const EDIT = { account_id: 'owner-e', email: 'jana@example.cz', mode: 'edit' as const };
  const URL_IDNES = 'https://reality.idnes.cz/detail/prodej/byt/praha/6ad7ca9d45dd870c0a0b152a/';

  function renderWithDialog() {
    const qc = new QueryClient({
      mutationCache: createMutationCache(),
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/pipeline']}>
          <NewPropertyProvider>
            <Pipeline />
          </NewPropertyProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  async function addFromHeader() {
    fireEvent.click(await screen.findByRole('button', { name: 'Nová nemovitost' }));
    const dialog = await screen.findByRole('dialog', { name: 'Nová nemovitost' });
    fireEvent.change(within(dialog).getByLabelText('Odkaz na inzerát'), { target: { value: URL_IDNES } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Přidat' }));
  }

  beforeEach(() => {
    localStorage.clear();
    reloadActingStore();
    vi.mocked(toast.pushToast).mockClear();
    vi.mocked(api.addPipelineCard).mockReset().mockResolvedValue({ property_id: 456, stage_key: 'interested', added: true });
    vi.mocked(api.ingestListing).mockReset().mockResolvedValue({
      state: 'held',
      message_cs: null,
      source: 'idnes',
      source_id_native: '6ad7ca9d45dd870c0a0b152a',
      listing_id: 123,
      is_active: true,
      property_id: 456,
      queue: null,
    });
    vi.mocked(api.listPipelineShares).mockResolvedValue({ shared_with: [], shared_with_me: [READ, EDIT] });
    vi.mocked(api.listTags).mockResolvedValue({ data: TAGS });
    vi.mocked(api.fetchArtifactLinks).mockResolvedValue(new Map());
    vi.mocked(queries.fetchPropertyTagMemberSet).mockResolvedValue(new Map());
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue(STAGES);
    vi.mocked(queries.fetchPipelineBoard).mockResolvedValue(CARDS);
    vi.mocked(queries.fetchListingCovers).mockResolvedValue(new Map());
    vi.mocked(queries.fetchPropertySourcesByPropertyIds).mockResolvedValue(new Map());
    vi.mocked(brokersApi.fetchListingBrokersByIds).mockResolvedValue(new Map());
    vi.mocked(api.listCollections).mockResolvedValue({ data: COLLECTIONS, total: COLLECTIONS.length });
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map());
  });
  afterEach(() => resetActiveBoard());

  it('adds the pasted ad to my board and stays, with a toast that opens it', async () => {
    renderWithDialog();
    await addFromHeader();
    await waitFor(() => expect(api.addPipelineCard).toHaveBeenCalledWith(456));
    await waitFor(() =>
      expect(toast.pushToast).toHaveBeenCalledWith('ok', 'Přidáno do pipeline', 0, expect.objectContaining({ label: 'Otevřít' })),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Pipeline obchodů' })).toBeInTheDocument();
  });

  it('adds it to the owner\'s board on an edit share', async () => {
    setActiveBoard(EDIT);
    renderWithDialog();
    await addFromHeader();
    await waitFor(() => expect(api.addPipelineCard).toHaveBeenCalledWith(456, { actAs: 'owner-e' }));
  });

  it('offers no button on a read board', async () => {
    setActiveBoard(READ);
    renderWithDialog();
    expect(await screen.findByText('Sadová')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nová nemovitost' })).toBeNull();
  });
});
