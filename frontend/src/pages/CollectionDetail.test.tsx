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
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import CollectionDetail from './CollectionDetail';
import type { CollectionPropertyRow, CollectionWithProperties, EstimationRun, PipelineShare } from '@/lib/types';
import * as api from '@/lib/api';
import * as queries from '@/lib/queries';
import { curationKeys, type CardRow, type PipelineMembers } from '@/lib/queries';
import { ActingAs } from '@/lib/acting';
import { collectionCardFilters } from '@/lib/collectionCards';
import { propertyPath } from '@/lib/listingUrl';
import { cardRow } from '@/test/cardRow';

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
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
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
  return { invalidate };
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

  it('says a failed read with a retry that reads again (MS16)', async () => {
    vi.mocked(api.getCollection).mockRejectedValueOnce(new Error('HTTP 500'));
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Zkusit znovu' }));
    expect(await heading()).toBeInTheDocument();
  });

  describe('the header Sledovat switch', () => {
    it('is named by its visible label, described by its sentence, and writes monitoring', async () => {
      vi.mocked(api.updateCollection).mockResolvedValue({ ...DATA.collection, monitoring_enabled: true });
      const { invalidate } = renderPage();
      const toggle = await screen.findByRole('switch', { name: 'Sledovat' });
      expect(toggle).toHaveAttribute('aria-checked', 'false');
      expect(toggle).toHaveAccessibleDescription(
        'Off — properties here are not monitored for changes. Alerts appear in the Notifications feed.',
      );
      fireEvent.click(toggle);
      await waitFor(() => expect(api.updateCollection).toHaveBeenCalledWith(1, { monitoring_enabled: true }));
      await waitFor(() =>
        expect(invalidate).toHaveBeenCalledWith({ queryKey: curationKeys.collection(1) }),
      );
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
      const { invalidate } = renderPage();
      await choose('Přejmenovat');
      fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), { target: { value: ' Vinohrady 2 ' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await waitFor(() =>
        expect(api.updateCollection).toHaveBeenCalledWith(1, {
          name: 'Vinohrady 2',
          description: 'Flats worth a second look.',
        }),
      );
      await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull());
      expect(actions()).toHaveFocus();
      expect(invalidate).toHaveBeenCalledWith({ queryKey: curationKeys.collection(1) });
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
      const { invalidate } = renderPage();
      await choose('Smazat kolekci');
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
      expect(invalidate).toHaveBeenCalledWith({ queryKey: curationKeys.collections });
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
      expect(within(panel as HTMLElement).getByText('Sharing')).toBeInTheDocument();

      fireEvent.click(button);
      expect(button).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByText('Sharing')).toBeNull();
    });

    it('shares with a typed email and revalidates the collection', async () => {
      vi.mocked(api.shareCollection).mockResolvedValue(OWNED.collection);
      const { invalidate } = renderPage();
      expect(await screen.findByRole('button', { name: 'Sdílet' })).toBeInTheDocument();
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
      await waitFor(() =>
        expect(invalidate).toHaveBeenCalledWith({ queryKey: curationKeys.collection(1) }),
      );
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
      const { invalidate } = renderPage();
      fireEvent.click(await screen.findByRole('tab', { name: 'Table' }));
      expect(screen.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByText('Properties')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Remove listing from collection' }));
      await waitFor(() =>
        expect(api.removePropertyFromCollection).toHaveBeenCalledWith(1, 42),
      );
      await waitFor(() =>
        expect(invalidate).toHaveBeenCalledWith({
          queryKey: curationKeys.propertyCollectionMembers,
        }),
      );
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: curationKeys.collection(1),
      });
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
      expect(screen.getByRole('link', { name: '900' })).toBeInTheDocument();
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
      const { invalidate } = renderPage();

      fireEvent.click(await screen.findByRole('button', { name: 'Remove from my list' }));
      expect(api.unshareCollection).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
      await waitFor(() => expect(api.unshareCollection).toHaveBeenCalledWith(1, 'acc-me'));
      // Back to the list, which re-reads without the collection.
      expect(await screen.findByText('collections list')).toBeInTheDocument();
      expect(invalidate).toHaveBeenCalledWith({ queryKey: curationKeys.collections });
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
