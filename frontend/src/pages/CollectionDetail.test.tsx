/* CollectionDetail — one collection on the desktop.
 *
 * Hermetic: mock getCollection and Browse's card reads. Since the redesign
 * (rulings P26, P31, P32) the header carries the owner's three actions — the
 * "Sledovat" switch, "Sdílet · N" and a ⋯ menu (Přejmenovat, Smazat kolekci) —
 * each opening what its page block held, and the members are Browse's cards
 * first, with the table as the second tab. The controls this pinned before stay
 * named: the edit inputs carried only a placeholder, and the switch's only child
 * is a decorative knob.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import CollectionDetail from './CollectionDetail';
import type { CollectionPropertyRow, CollectionWithProperties, EstimationRun, PipelineShare } from '@/lib/types';
import * as api from '@/lib/api';
import * as queries from '@/lib/queries';
import { CARD_PAGE_SIZE, curationKeys, type CardRow, type PipelineMembers } from '@/lib/queries';
import { ActingAs } from '@/lib/acting';
import { collectionCardFilters } from '@/lib/collectionCards';
import { familyTargets, revalidateTables } from '@/lib/curationSync';
import { revalidate } from '@/lib/revalidate';
import { propertyPath } from '@/lib/listingUrl';
import { cardRow } from '@/test/cardRow';
import { watchReads } from '@/test/reads';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    getCollection: vi.fn(),
    listCollections: vi.fn(),
    updateCollection: vi.fn(),
    deleteCollection: vi.fn(),
    removePropertyFromCollection: vi.fn(),
    addPropertiesToCollection: vi.fn(),
    shareCollection: vi.fn(),
    unshareCollection: vi.fn(),
    createEstimation: vi.fn(),
    latestEstimationsByListing: vi.fn(),
  };
});
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchListingsForCards: vi.fn(),
  fetchBrowseCount: vi.fn(),
  fetchPipelineMembers: vi.fn(),
  fetchPipelineStages: vi.fn(),
  fetchPropertyCollectionMemberSet: vi.fn(),
  fetchIsDismissed: vi.fn(),
  fetchImagesForListingIds: vi.fn(),
  fetchListingCovers: vi.fn(),
  fetchListingFloors: vi.fn(),
  fetchNoteCounts: vi.fn(),
}));
vi.mock('@/lib/brokers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/brokers')>()),
  fetchListingBrokersByIds: vi.fn(async () => new Map()),
}));

const DATA: CollectionWithProperties = {
  collection: {
    id: 1,
    name: 'Vinohrady watch',
    description: 'Flats worth a second look.',
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-20T10:00:00Z',
    listing_count: 0,
    monitoring_enabled: false,
    is_system: false,
    owner_email: null,
    shared_with: [],
  },
  properties: [],
};

const MEMBER: CollectionPropertyRow = {
  property_id: 42,
  sreality_id: 900,
  source: 'sreality',
  display_label: 'Praha 5',
  disposition: '2+kk',
  subtype: null,
  area_m2: 55,
  price_czk: 8_900_000,
  last_seen_at: '2026-08-20T10:00:00Z',
  is_active: true,
  added_at: '2026-08-01T10:00:00Z',
};
/* A member Browse does not serve (no confirmed location): no card. */
const UNCARDED: CollectionPropertyRow = {
  ...MEMBER,
  property_id: 43,
  sreality_id: 901,
  display_label: 'Bez polohy',
};
const WITH_MEMBER: CollectionWithProperties = {
  collection: { ...DATA.collection, listing_count: 1 },
  properties: [MEMBER],
};
const CARD = cardRow(42, { sreality_id: 900, display_label: 'Praha 5' });

const ACTING: PipelineShare = { account_id: 'owner-e', email: 'jana@example.cz', mode: 'edit' };

function renderPage({ path = '/collections/1', acting = null }: { path?: string; acting?: PipelineShare | null } = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const reads = watchReads(qc);
  render(
    <QueryClientProvider client={qc}>
      <ActingAs share={acting}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/collections/:id" element={<CollectionDetail />} />
            <Route path="/collections" element={<p>collections list</p>} />
          </Routes>
        </MemoryRouter>
      </ActingAs>
    </QueryClientProvider>,
  );
  return { ...reads, qc };
}

/* jsdom has no IntersectionObserver; this one lets a test bring the end of the
 * card grid into view by hand. */
class FakeIO {
  static instances: FakeIO[] = [];
  constructor(readonly callback: IntersectionObserverCallback) {
    FakeIO.instances.push(this);
  }
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
  reach(): void {
    this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
  }
}

const heading = () => screen.findByRole('heading', { level: 1, name: 'Vinohrady watch' });
const actions = () => screen.getByRole('button', { name: 'Collection actions' });
async function choose(item: string) {
  await heading();
  fireEvent.click(actions());
  fireEvent.click(screen.getByRole('menuitem', { name: item }));
}
const openSharing = async () => fireEvent.click(await screen.findByRole('button', { name: /^Sdílet/ }));
/* The card's one link: its property, opened on the card's advert. */
const cardLink = (row: CardRow) =>
  screen.queryAllByRole('link').find((a) => a.getAttribute('href') === propertyPath(row.property_id, row.listing_id));

describe('<CollectionDetail>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getCollection).mockResolvedValue(DATA);
    vi.mocked(api.listCollections).mockResolvedValue({ data: [DATA.collection], total: 1 });
    vi.mocked(api.removePropertyFromCollection).mockResolvedValue({ removed: true });
    vi.mocked(api.latestEstimationsByListing).mockResolvedValue({});
    vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [CARD], nextCursor: null });
    vi.mocked(queries.fetchBrowseCount).mockResolvedValue(1);
    vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map() as PipelineMembers);
    vi.mocked(queries.fetchPipelineStages).mockResolvedValue([]);
    vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[42, [1]], [43, [1]]]));
    vi.mocked(queries.fetchIsDismissed).mockResolvedValue(false);
    vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map());
    vi.mocked(queries.fetchListingCovers).mockResolvedValue(new Map());
    vi.mocked(queries.fetchListingFloors).mockResolvedValue(new Map());
    vi.mocked(queries.fetchNoteCounts).mockResolvedValue(new Map());
  });

  /* Rulings P26 + P31: the h1 alone, and no page block left for what the header now holds. */
  it('draws the h1 alone, with no Edit, Monitoring or Sharing block under it', async () => {
    renderPage();
    expect(await heading()).toBeInTheDocument();
    expect(screen.queryByText('Collection')).toBeNull();
    expect(screen.queryByText('Edit')).toBeNull();
    expect(screen.queryByText('Monitoring')).toBeNull();
    expect(screen.queryByText('Sharing')).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull();
  });

  /* Section cards (operator ruling 2026-10-28): an empty collection keeps its
     Properties card with count 0 and its one muted line (C10); no hairline
     separates the header from it any more. */
  it('keeps an empty collection as the Properties card, count 0, under the header with no hairline', async () => {
    renderPage();
    const card = await screen.findByRole('region', { name: 'Properties' });
    expect(within(card).getByRole('heading', { level: 2, name: 'Properties' }).nextElementSibling)
      .toHaveTextContent('0');
    expect(within(card).getByText(/^No properties/)).toBeInTheDocument();
    expect(document.body.querySelector('.h-px')).toBeNull();
  });

  it('says a failed read with a retry that reads again (MS16)', async () => {
    vi.mocked(api.getCollection).mockRejectedValueOnce(new Error('HTTP 500'));
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Zkusit znovu' }));
    expect(await heading()).toBeInTheDocument();
  });

  describe('the header Sledovat switch', () => {
    it('is named by its visible label, described by its sentence, and writes monitoring', async () => {
      vi.mocked(api.updateCollection).mockResolvedValue({ ...DATA.collection, monitoring_enabled: true });
      const { reread, clear } = renderPage();
      const toggle = await screen.findByRole('switch', { name: 'Sledovat' });
      clear();
      expect(toggle).toHaveAttribute('aria-checked', 'false');
      expect(toggle).toHaveAccessibleDescription(
        'Off — properties here are not monitored for changes. Alerts appear in the Notifications feed.',
      );
      fireEvent.click(toggle);
      await waitFor(() => expect(api.updateCollection).toHaveBeenCalledWith(1, { monitoring_enabled: true }));
      await waitFor(() => expect(reread(curationKeys.collection(1))).toBe(true));
    });

    it('says a failed write under the header', async () => {
      vi.mocked(api.updateCollection).mockRejectedValue(new Error('Monitoring is off for this plan'));
      renderPage();
      fireEvent.click(await screen.findByRole('switch', { name: 'Sledovat' }));
      expect(await screen.findByText('Monitoring is off for this plan')).toBeInTheDocument();
    });

    /* Acting as a pipeline's owner the API refuses monitoring (403); sharing and
       the ⋯ menu stay, as their blocks did. */
    it('is hidden while acting as a pipeline owner', async () => {
      renderPage({ acting: ACTING });
      expect(await heading()).toBeInTheDocument();
      expect(screen.queryByRole('switch')).toBeNull();
      expect(screen.getByRole('button', { name: 'Sdílet' })).toBeInTheDocument();
      expect(actions()).toBeInTheDocument();
    });
  });

  describe('the ⋯ menu', () => {
    it('offers Přejmenovat and Smazat kolekci', async () => {
      renderPage();
      await heading();
      fireEvent.click(actions());
      expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual(['Přejmenovat', 'Smazat kolekci']);
    });

    it('Přejmenovat flips the header into the named edit fields', async () => {
      renderPage();
      await choose('Přejmenovat');
      const name = screen.getByRole('textbox', { name: 'Name' });
      expect(name).toHaveValue('Vinohrady watch');
      expect(name).toHaveFocus();
      expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Flats worth a second look.');
      expect(screen.queryByRole('heading', { level: 1 })).toBeNull();
    });

    it('Save changes writes and flips back, focus on ⋯', async () => {
      vi.mocked(api.updateCollection).mockResolvedValue({ ...DATA.collection, name: 'Vinohrady 2' });
      const { reread, clear } = renderPage();
      await choose('Přejmenovat');
      clear();
      fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: ' Vinohrady 2 ' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      /* Only what the user changed (lib/useServerDraft): the description stays out. */
      await waitFor(() => expect(api.updateCollection).toHaveBeenCalledWith(1, { name: 'Vinohrady 2' }));
      await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull());
      expect(actions()).toHaveFocus();
      await waitFor(() => expect(reread(curationKeys.collection(1))).toBe(true));
    });

    /* D3 (lib/useServerDraft): the open editor follows the collection while a field
       is untouched, and saves only what the user changed. */
    it('follows a rename made elsewhere, and a description edit saves the description alone', async () => {
      vi.mocked(api.updateCollection).mockResolvedValue({ ...DATA.collection, description: 'Jiný popis' });
      const { qc } = renderPage();
      await choose('Přejmenovat');
      const name = screen.getByRole('textbox', { name: 'Name' });
      vi.mocked(api.getCollection).mockResolvedValue({
        ...DATA,
        collection: { ...DATA.collection, name: 'Vinohrady (jinde)' },
      });
      await act(async () => {
        await revalidateTables(qc, ['collections']);
      });
      await waitFor(() => expect(name).toHaveValue('Vinohrady (jinde)'));
      fireEvent.blur(name);
      fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'Jiný popis' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await waitFor(() => expect(api.updateCollection).toHaveBeenCalledWith(1, { description: 'Jiný popis' }));
    });

    /* PATCH /collections reads null as "not given": a description cleared alone
       goes as '', or the API refuses it ("nothing to update"). */
    it('clears the description alone', async () => {
      vi.mocked(api.updateCollection).mockResolvedValue({ ...DATA.collection, description: '' });
      renderPage();
      await choose('Přejmenovat');
      fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: '  ' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await waitFor(() => expect(api.updateCollection).toHaveBeenCalledWith(1, { description: '' }));
    });

    it('keeps a typed name over a rename made elsewhere', async () => {
      vi.mocked(api.updateCollection).mockResolvedValue({ ...DATA.collection, name: 'Můj název' });
      const { qc } = renderPage();
      await choose('Přejmenovat');
      const name = screen.getByRole('textbox', { name: 'Name' });
      fireEvent.change(name, { target: { value: 'Můj název' } });
      vi.mocked(api.getCollection).mockResolvedValue({
        ...DATA,
        collection: { ...DATA.collection, name: 'Vinohrady (jinde)', description: 'Jinde' },
      });
      await act(async () => {
        await revalidateTables(qc, ['collections']);
      });
      expect(name).toHaveValue('Můj název');
      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Jinde'));
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await waitFor(() => expect(api.updateCollection).toHaveBeenCalledWith(1, { name: 'Můj název' }));
    });

    it('Cancel writes nothing and flips back, focus on ⋯', async () => {
      renderPage();
      await choose('Přejmenovat');
      fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: 'Jinak' } });
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull();
      expect(await heading()).toBeInTheDocument();
      expect(actions()).toHaveFocus();
      expect(api.updateCollection).not.toHaveBeenCalled();
    });

    it('Smazat kolekci keeps its confirm: Cancel disarms, a double-click does not delete, the confirm does', async () => {
      vi.mocked(api.deleteCollection).mockResolvedValue({ deleted: true });
      const { reread, clear, qc } = renderPage();
      // The list page's read, cached from an earlier visit.
      qc.setQueryData(curationKeys.collections, { data: [DATA.collection], total: 1 });
      await choose('Smazat kolekci');
      clear();
      expect(screen.queryByRole('menu')).toBeNull();
      // The delete is offered once: the confirm, never a second idle button.
      expect(screen.queryByRole('button', { name: 'Delete collection' })).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByRole('button', { name: 'Delete "Vinohrady watch"' })).toBeNull();

      await choose('Smazat kolekci');
      const confirm = screen.getByRole('button', { name: 'Delete "Vinohrady watch"' });
      fireEvent.click(confirm, { detail: 2 });
      expect(api.deleteCollection).not.toHaveBeenCalled();
      fireEvent.click(confirm, { detail: 1 });
      await waitFor(() => expect(api.deleteCollection).toHaveBeenCalledWith(1));
      expect(await screen.findByText('collections list')).toBeInTheDocument();
      expect(reread(curationKeys.collections)).toBe(true);
    });

    it('says a failed delete under the header and stays', async () => {
      vi.mocked(api.deleteCollection).mockRejectedValue(new Error('Delete refused'));
      renderPage();
      await choose('Smazat kolekci');
      fireEvent.click(screen.getByRole('button', { name: 'Delete "Vinohrady watch"' }));
      expect(await screen.findByText('Delete refused')).toBeInTheDocument();
      expect(screen.queryByText('collections list')).toBeNull();
    });

    it('the default collection: no Smazat kolekci, and the name locked in the editor', async () => {
      vi.mocked(api.getCollection).mockResolvedValue({
        ...DATA,
        collection: { ...DATA.collection, is_system: true },
      });
      renderPage();
      await heading();
      fireEvent.click(actions());
      expect(screen.getAllByRole('menuitem').map((i) => i.textContent)).toEqual(['Přejmenovat']);
      fireEvent.click(screen.getByRole('menuitem', { name: 'Přejmenovat' }));
      const name = screen.getByRole('textbox', { name: 'Name' });
      expect(name).toBeDisabled();
      expect(name).toHaveAttribute('title', "The default collection can't be renamed.");
      expect(screen.getByRole('textbox', { name: 'Description' })).toHaveFocus();
      expect(screen.getByText("Default collection — can't be renamed or deleted.")).toBeInTheDocument();
    });
  });

  /* Sharing (migration 610): the owner shares by email, sees every recipient,
   * and stops sharing in two steps; a refusal sits under the field. Since P31
   * the block opens from "Sdílet · N" in the header. */
  describe('Sharing, as the owner', () => {
    const OWNED: CollectionWithProperties = {
      ...DATA,
      collection: {
        ...DATA.collection,
        shared_with: [{ account_id: 'acc-b', email: 'petr@example.cz' }],
      },
    };

    it('"Sdílet · 1" opens the sharing block in place and closes it again', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(OWNED);
      renderPage();
      const button = await screen.findByRole('button', { name: 'Sdílet · 1' });
      expect(button).toHaveAttribute('aria-expanded', 'false');
      expect(button).not.toHaveAttribute('aria-pressed');
      expect(screen.queryByText('Sharing')).toBeNull();

      fireEvent.click(button);
      expect(button).toHaveAttribute('aria-expanded', 'true');
      const panel = document.getElementById(button.getAttribute('aria-controls') ?? '');
      expect(panel).not.toBeNull();
      /* The sharing panel is a section card (system.md § Section cards): its
         title names the region, its count is the recipients. */
      const card = screen.getByRole('region', { name: 'Sharing' });
      expect(card).toBe(panel);
      expect(card).toHaveClass('border', 'bg-[var(--color-paper-2)]', 'rounded-[var(--radius-md)]');
      expect(within(card).getByRole('heading', { level: 2, name: 'Sharing' }).nextElementSibling)
        .toHaveTextContent('1');

      fireEvent.click(button);
      expect(button).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByText('Sharing')).toBeNull();
    });

    it('shares with a typed email and revalidates the collection', async () => {
      vi.mocked(api.shareCollection).mockResolvedValue(OWNED.collection);
      const { reread, clear } = renderPage();
      expect(await screen.findByRole('button', { name: 'Sdílet' })).toBeInTheDocument();
      clear();
      await openSharing();
      expect(screen.getByText('Sharing')).toBeInTheDocument();
      expect(screen.getByText('Not shared with anyone.')).toBeInTheDocument();

      fireEvent.change(screen.getByRole('textbox', { name: 'Email' }), {
        target: { value: ' petr@example.cz ' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Share' }));
      await waitFor(() =>
        expect(api.shareCollection).toHaveBeenCalledWith(1, 'petr@example.cz'),
      );
      await waitFor(() => expect(reread(curationKeys.collection(1))).toBe(true));
    });

    /* The one ShareBlock (components/ShareBlock) — a collection share has no
       mode, so no access choice is offered (the Pipeline tab's has one). */
    it('offers no access mode for a collection share', async () => {
      renderPage();
      await openSharing();
      expect(screen.getByText('Sharing')).toBeInTheDocument();
      expect(screen.queryByText('Access')).toBeNull();
      expect(screen.queryByRole('button', { name: 'Can edit' })).toBeNull();
    });

    it('shows a 422 inline under the field', async () => {
      vi.mocked(api.shareCollection).mockRejectedValue(
        new api.ApiError('No account uses this email.', 422, null),
      );
      renderPage();
      await openSharing();
      fireEvent.change(screen.getByRole('textbox', { name: 'Email' }), {
        target: { value: 'nikdo@example.cz' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Share' }));
      expect(await screen.findByRole('alert')).toHaveTextContent('No account uses this email.');
    });

    it('lists the recipients and stops sharing only after a confirm', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(OWNED);
      vi.mocked(api.unshareCollection).mockResolvedValue({ deleted: true });
      renderPage();
      await openSharing();

      const list = screen.getByRole('list', { name: 'Shared with' });
      expect(within(list).getByText('petr@example.cz')).toBeInTheDocument();
      fireEvent.click(within(list).getByRole('button', { name: 'Stop sharing with petr@example.cz' }));
      expect(api.unshareCollection).not.toHaveBeenCalled();
      fireEvent.click(within(list).getByRole('button', { name: 'Stop sharing' }));
      await waitFor(() => expect(api.unshareCollection).toHaveBeenCalledWith(1, 'acc-b'));
    });
  });

  /* Ruling P32: Browse's cards first, the table as the second tab. */
  describe('the members', () => {
    it('shows them as Browse cards by default, read through the collection scope', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(WITH_MEMBER);
      renderPage();
      await waitFor(() => expect(cardLink(CARD)).toBeDefined());
      expect(vi.mocked(queries.fetchListingsForCards).mock.calls[0][0]).toEqual(collectionCardFilters(1));
      expect(cardLink(CARD)?.closest('li')).not.toBeNull();
      expect(screen.getByRole('tab', { name: 'Cards' })).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'false');
      expect(screen.queryByRole('table')).toBeNull();
      // The page has no merge mode, so the card header offers none.
      expect(screen.queryByRole('button', { name: 'Sloučit…' })).toBeNull();
    });

    it('an apartment card offers the on-card estimate, as on Browse', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(WITH_MEMBER);
      vi.mocked(api.createEstimation).mockResolvedValue({
        id: 5, status: 'pending', estimate_kind: 'rent', gross_yield_pct: null,
        estimated_monthly_rent_czk: null, created_at: null,
      } as unknown as EstimationRun);
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: 'Odhad' }));
      await waitFor(() =>
        expect(api.createEstimation).toHaveBeenCalledWith(expect.objectContaining({
          sreality_id: 900, source: 'ui', estimate_kind: 'rent',
        })),
      );
    });

    /* Removing a member here changes the SAME fact the Browse card glyph and
       the listing header paint, so it has to revalidate the shared member map. */
    it('keeps the table as the second tab, with Remove revalidating the shared member map', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(WITH_MEMBER);
      const { reread, clear } = renderPage();
      fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
      expect(screen.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true');
      /* The table tab is the Properties card, its count the members; the table
         that scrolls keeps its own box (C9). */
      const card = screen.getByRole('region', { name: 'Properties' });
      expect(card).toHaveClass('border', 'bg-[var(--color-paper-2)]');
      expect(within(card).getByRole('heading', { level: 2, name: 'Properties' }).nextElementSibling)
        .toHaveTextContent('1');
      expect(within(card).getByRole('table').closest('.overflow-hidden')).toHaveClass('border');

      clear();
      fireEvent.click(screen.getByRole('button', { name: 'Remove listing from collection' }));
      await waitFor(() =>
        expect(api.removePropertyFromCollection).toHaveBeenCalledWith(1, 42),
      );
      await waitFor(() => expect(reread(curationKeys.propertyCollectionMembers)).toBe(true));
      expect(reread(curationKeys.collection(1))).toBe(true);
    });

    /* The tab-strip ruling (2026-10-27, option A): arrow keys on every strip. */
    it('moves to Table with ArrowRight, focus following, the content its panel', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(WITH_MEMBER);
      renderPage();
      const cards = await screen.findByRole('tab', { name: 'Cards' });
      expect(screen.getByRole('tablist', { name: 'Zobrazení nemovitostí' })).toContainElement(cards);
      fireEvent.keyDown(cards, { key: 'ArrowRight' });
      const table = screen.getByRole('tab', { name: 'Table' });
      expect(table).toHaveAttribute('aria-selected', 'true');
      expect(document.activeElement).toBe(table);
      expect(await screen.findByRole('table')).toBeInTheDocument();
      const panel = screen.getByRole('tabpanel');
      expect(panel).toHaveAttribute('id', table.getAttribute('aria-controls'));
      expect(panel).toHaveAttribute('aria-labelledby', table.id);
    });

    it('opens on the table when the address says ?tab=table', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(WITH_MEMBER);
      renderPage({ path: '/collections/1?tab=table' });
      expect(await screen.findByRole('table')).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true');
    });

    it('says how many members have no card and opens the table from that line', async () => {
      vi.mocked(api.getCollection).mockResolvedValue({
        collection: { ...DATA.collection, listing_count: 2 },
        properties: [MEMBER, UNCARDED],
      });
      renderPage();
      const line = await screen.findByText(/more without a card/);
      expect(line.textContent?.replace(/ /g, ' ')).toContain('1 more without a card');
      fireEvent.click(within(line).getByRole('button', { name: 'Open the table' }));
      expect(screen.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByText('Bez polohy')).toBeInTheDocument();
    });

    it('says so in place of the grid when no member has a card', async () => {
      vi.mocked(api.getCollection).mockResolvedValue({
        collection: { ...DATA.collection, listing_count: 1 },
        properties: [UNCARDED],
      });
      vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [], nextCursor: null });
      renderPage();
      expect(await screen.findByText(/None of these properties has a card yet/)).toBeInTheDocument();
      expect(screen.queryByText('No listings match these filters.')).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Open the table' }));
      expect(screen.getByText('Bez polohy')).toBeInTheDocument();
    });

    /* MS16: a failed card read is a retry, never an empty grid. */
    it('says a failed card read with a retry that reads again', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(WITH_MEMBER);
      vi.mocked(queries.fetchListingsForCards).mockRejectedValueOnce(new Error('statement timeout'));
      renderPage();
      const banner = await screen.findByRole('alert');
      expect(banner).toHaveTextContent('Failed to load: statement timeout');
      expect(screen.getByRole('tab', { name: 'Table' })).toBeInTheDocument();
      fireEvent.click(within(banner).getByRole('button', { name: 'Zkusit znovu' }));
      await waitFor(() => expect(cardLink(CARD)).toBeDefined());
      expect(screen.queryByRole('alert')).toBeNull();
    });

    it('keeps the drawn cards when a later page fails, its retry asking for that page alone', async () => {
      FakeIO.instances = [];
      vi.stubGlobal('IntersectionObserver', FakeIO);
      try {
        const first = Array.from({ length: CARD_PAGE_SIZE }, (_, i) => cardRow(100 + i));
        const next = cardRow(200);
        const CURSOR = { value: 5_000_123, id: 123 };
        let failing = true;
        vi.mocked(queries.fetchListingsForCards).mockImplementation(async (_f, _s, cursor) => {
          if (cursor == null) return { rows: first, nextCursor: CURSOR };
          if (failing) throw new Error('statement timeout');
          return { rows: [next], nextCursor: null };
        });
        vi.mocked(api.getCollection).mockResolvedValue(WITH_MEMBER);
        renderPage();
        await waitFor(() => expect(cardLink(first[0])).toBeDefined());

        act(() => FakeIO.instances[FakeIO.instances.length - 1].reach());
        const banner = await screen.findByRole('alert');
        expect(banner).toHaveTextContent('Failed to load: statement timeout');
        expect(cardLink(first[0])).toBeDefined();
        expect(cardLink(first[CARD_PAGE_SIZE - 1])).toBeDefined();
        // The end of the grid is still in view, yet the failed page is not asked again on its own.
        await act(async () => new Promise((r) => setTimeout(r, 50)));
        const reads = vi.mocked(queries.fetchListingsForCards).mock.calls;
        expect(reads.map((c) => c[2])).toEqual([null, CURSOR]);

        failing = false;
        fireEvent.click(within(banner).getByRole('button', { name: 'Zkusit znovu' }));
        await waitFor(() => expect(cardLink(next)).toBeDefined());
        expect(reads.map((c) => c[2])).toEqual([null, CURSOR, CURSOR]);
        expect(cardLink(first[0])).toBeDefined();
        expect(screen.queryByRole('alert')).toBeNull();
      } finally {
        vi.unstubAllGlobals();
      }
    });

    /* Ruling Q3(c) with the pointer hold (2026-11-01): a member removed on another
     * device leaves the cards and the table within one clock read, but not under
     * the pointer; the counts (beside the cards, in the header) follow at once:
     * a count is never held (review repair: the header rides the collection's
     * own read, so that read is not held, only the rows drawn from it). */
    const SECOND: CollectionPropertyRow = { ...MEMBER, property_id: 44, sreality_id: 902, display_label: 'Praha 6' };
    const CARD2 = cardRow(44, { sreality_id: 902, display_label: 'Praha 6' });
    const TWO: CollectionWithProperties = {
      collection: { ...DATA.collection, listing_count: 2 },
      properties: [MEMBER, SECOND],
    };
    const ONE: CollectionWithProperties = {
      collection: { ...DATA.collection, listing_count: 1 },
      properties: [SECOND],
    };
    const headerCount = () => screen.getByText(/· updated/).textContent?.replace(/\s+/g, ' ').match(/^(\d+) propert/)?.[1];

    it('drops a member removed elsewhere once the pointer leaves the members, the counts at once', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(TWO);
      vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [CARD, CARD2], nextCursor: null });
      vi.mocked(queries.fetchBrowseCount).mockResolvedValue(2);
      const { qc } = renderPage();
      await waitFor(() => expect(cardLink(CARD)).toBeDefined());
      await waitFor(() => expect(screen.getByText('2 of 2')).toBeInTheDocument());
      fireEvent.pointerOver(cardLink(CARD)!);
      const reads = {
        collection: vi.mocked(api.getCollection).mock.calls.length,
        cards: vi.mocked(queries.fetchListingsForCards).mock.calls.length,
      };

      vi.mocked(api.getCollection).mockResolvedValue(ONE);
      vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [CARD2], nextCursor: null });
      vi.mocked(queries.fetchBrowseCount).mockResolvedValue(1);
      vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[44, [1]]]));
      await act(async () => {
        void revalidate(qc, familyTargets(qc, 'collections', null));
      });
      await waitFor(() => expect(screen.getByText('2 of 1')).toBeInTheDocument());
      await waitFor(() => expect(headerCount()).toBe('1'));
      expect(api.getCollection).toHaveBeenCalledTimes(reads.collection + 1);
      expect(queries.fetchListingsForCards).toHaveBeenCalledTimes(reads.cards);
      expect(cardLink(CARD)).toBeDefined();

      fireEvent.pointerLeave(cardLink(CARD)!);
      await waitFor(() => expect(cardLink(CARD)).toBeUndefined());
      expect(api.getCollection).toHaveBeenCalledTimes(reads.collection + 1);
      expect(screen.getByText('1 of 1')).toBeInTheDocument();
    });

    it("holds the table's rows under the pointer, never its counts", async () => {
      vi.mocked(api.getCollection).mockResolvedValue(TWO);
      const { qc } = renderPage({ path: '/collections/1?tab=table' });
      const row = await screen.findByRole('link', { name: 'Praha 5' });
      fireEvent.pointerOver(row);

      vi.mocked(api.getCollection).mockResolvedValue(ONE);
      vi.mocked(queries.fetchPropertyCollectionMemberSet).mockResolvedValue(new Map([[44, [1]]]));
      await act(async () => {
        void revalidate(qc, familyTargets(qc, 'collections', null));
      });
      await waitFor(() => expect(headerCount()).toBe('1'));
      const card = screen.getByRole('region', { name: 'Properties' });
      expect(within(card).getByRole('heading', { level: 2, name: 'Properties' }).nextElementSibling).toHaveTextContent('1');
      expect(screen.getByRole('link', { name: 'Praha 5' })).toBeInTheDocument();

      fireEvent.pointerLeave(row);
      await waitFor(() => expect(screen.queryByRole('link', { name: 'Praha 5' })).toBeNull());
      expect(screen.getByRole('link', { name: 'Praha 6' })).toBeInTheDocument();
    });

    /* Review repair (W6): my own Remove is not held. The mouse on its way to the
     * button is a hand on the members, and had parked the very re-read that takes
     * the row out: it stayed, its button back to "Remove", until the pointer left. */
    it('drops the row I remove at once, the pointer still over the members', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(TWO);
      renderPage({ path: '/collections/1?tab=table' });
      await screen.findByRole('link', { name: 'Praha 5' });
      const remove = within(screen.getByRole('link', { name: 'Praha 5' }).closest('tr')!).getByRole('button', {
        name: 'Remove listing from collection',
      });
      fireEvent.pointerOver(remove);
      vi.mocked(api.getCollection).mockResolvedValue(ONE);
      fireEvent.click(remove);

      await waitFor(() => expect(api.removePropertyFromCollection).toHaveBeenCalledWith(1, 42));
      await waitFor(() => expect(screen.queryByRole('link', { name: 'Praha 5' })).toBeNull());
      expect(screen.getByRole('link', { name: 'Praha 6' })).toBeInTheDocument();
      await waitFor(() => expect(headerCount()).toBe('1'));
    });

    it('shows a row I removed again once it is re-added elsewhere', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(TWO);
      const { qc } = renderPage({ path: '/collections/1?tab=table' });
      await screen.findByRole('link', { name: 'Praha 5' });
      const remove = within(screen.getByRole('link', { name: 'Praha 5' }).closest('tr')!).getByRole('button', {
        name: 'Remove listing from collection',
      });
      fireEvent.pointerOver(remove);
      vi.mocked(api.getCollection).mockResolvedValue(ONE);
      fireEvent.click(remove);
      await waitFor(() => expect(screen.queryByRole('link', { name: 'Praha 5' })).toBeNull());

      vi.mocked(api.getCollection).mockResolvedValue({
        ...TWO,
        properties: [{ ...MEMBER, added_at: '2026-11-01T10:00:00Z' }, SECOND],
      });
      await act(async () => {
        void revalidate(qc, familyTargets(qc, 'collections', null));
      });
      await waitFor(() => expect(headerCount()).toBe('2'));
      fireEvent.pointerLeave(screen.getByRole('table'));
      expect(await screen.findByRole('link', { name: 'Praha 5' })).toBeInTheDocument();
    });

    it('an empty collection shows its sentence and no tabs', async () => {
      renderPage();
      expect(
        await screen.findByText(/No properties yet\. Use the "Add to collection" picker/),
      ).toBeInTheDocument();
      expect(screen.queryByRole('tablist')).toBeNull();
      expect(queries.fetchListingsForCards).not.toHaveBeenCalled();
    });
  });

  /* The recipient's view: read-only, with one write — leaving. */
  describe('as a viewer of a collection shared with me', () => {
    const SHARED: CollectionWithProperties = {
      collection: {
        ...DATA.collection,
        listing_count: 1,
        owner_email: 'petr@example.cz',
        shared_with: [{ account_id: 'acc-me', email: null }],
      },
      properties: [MEMBER],
    };

    it('names the owner, hides every owner control, and shows the cards and the table', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(SHARED);
      renderPage();

      expect(await screen.findByText('petr@example.cz')).toBeInTheDocument();
      expect(screen.getByText(/view only/)).toBeInTheDocument();
      expect(screen.queryByRole('switch')).toBeNull();
      expect(screen.queryByRole('button', { name: /^Sdílet/ })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Collection actions' })).toBeNull();
      await waitFor(() => expect(cardLink(CARD)).toBeDefined());

      fireEvent.click(screen.getByRole('tab', { name: 'Table' }));
      expect(screen.queryByRole('button', { name: 'Remove listing from collection' })).toBeNull();
      /* The place is the row's link, opening in a new tab; the id is text. */
      const row = screen.getByRole('link', { name: 'Praha 5' });
      expect(row).toHaveAttribute('href', '/property/' + MEMBER.property_id);
      expect(row).toHaveAttribute('target', '_blank');
      expect(screen.queryByRole('link', { name: '900' })).toBeNull();
    });

    it('says nothing about adding when the shared collection is empty', async () => {
      vi.mocked(api.getCollection).mockResolvedValue({ ...SHARED, properties: [] });
      renderPage();
      expect(await screen.findByText('No properties in this collection yet.')).toBeInTheDocument();
      expect(screen.queryByText(/Add to collection/)).toBeNull();
      expect(screen.queryByRole('tablist')).toBeNull();
    });

    it('leaves through its own grant, after a confirm', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(SHARED);
      vi.mocked(api.unshareCollection).mockResolvedValue({ deleted: true });
      const { reread, clear } = renderPage();

      fireEvent.click(await screen.findByRole('button', { name: 'Remove from my list' }));
      clear();
      expect(api.unshareCollection).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
      await waitFor(() => expect(api.unshareCollection).toHaveBeenCalledWith(1, 'acc-me'));
      // Back to the list, which re-reads without the collection.
      expect(await screen.findByText('collections list')).toBeInTheDocument();
      expect(reread(curationKeys.collections)).toBe(true);
    });

    it('does not let a double-click through the confirm', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(SHARED);
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: 'Remove from my list' }), { detail: 1 });
      // The second click of the double-click lands on the confirm that just appeared.
      fireEvent.click(screen.getByRole('button', { name: 'Remove' }), { detail: 2 });
      expect(api.unshareCollection).not.toHaveBeenCalled();
    });

    it('says why leaving failed and stays on the page', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(SHARED);
      vi.mocked(api.unshareCollection).mockRejectedValue(new api.ApiError('Not found', 404, null));
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: 'Remove from my list' }));
      fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
      expect(await screen.findByText('Not found')).toBeInTheDocument();
      expect(screen.queryByText('collections list')).toBeNull();
    });
  });

  it('says a missing collection may no longer be shared with you', async () => {
    vi.mocked(api.getCollection).mockRejectedValue(new api.ApiError('Not found', 404, null));
    renderPage();
    expect(
      await screen.findByText(/or it is no longer shared with you/),
    ).toBeInTheDocument();
  });
});
