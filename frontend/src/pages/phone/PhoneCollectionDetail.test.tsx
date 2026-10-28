/* One collection on the phone (#1769): the members are Browse's cards scoped to
 * the collection, and the card's bookmark opens the shared collection sheet,
 * where unchecking this collection is the removal — the desktop row's own
 * `removePropertyFromCollection` — and the card leaves the roll. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { collectionCardFilters } from '@/lib/collectionCards';
import * as queries from '@/lib/queries';
import type { PipelineMembers } from '@/lib/queries';
import type { Collection, CollectionPropertyRow } from '@/lib/types';
import { cardRow } from '@/test/cardRow';

import CollectionDetail from '../CollectionDetail';
import { MANAGE_NOTE, VIEWER_NOTE } from './PhoneCollections';

vi.mock('@/lib/phoneShell', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/phoneShell')>()),
  usePhoneShell: () => true,
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getCollection: vi.fn(),
  listCollections: vi.fn(),
  removePropertyFromCollection: vi.fn(),
  addPropertiesToCollection: vi.fn(),
  updateCollection: vi.fn(),
  deleteCollection: vi.fn(),
}));
vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchListingsForCards: vi.fn(),
  fetchPipelineMembers: vi.fn(),
  fetchPipelineStages: vi.fn(),
  fetchPropertyCollectionMemberSet: vi.fn(),
  fetchIsDismissed: vi.fn(),
  fetchImagesForListingIds: vi.fn(),
}));

const COLLECTION: Collection = {
  id: 7,
  name: 'K prohlídce',
  description: 'Vysočany, do 8 mil.',
  created_at: '2026-10-03T09:00:00Z',
  updated_at: '2026-10-11T09:00:00Z',
  listing_count: 3,
  monitoring_enabled: true,
  is_system: false,
  owner_email: null,
  shared_with: [],
};

const member = (property_id: number, extra: Partial<CollectionPropertyRow> = {}): CollectionPropertyRow => ({
  property_id,
  sreality_id: 0,
  source: 'idnes',
  display_label: `Místo ${property_id}`,
  disposition: '3+1',
  subtype: null,
  area_m2: 70,
  price_czk: 6_900_000,
  last_seen_at: '2026-10-11T09:00:00Z',
  is_active: true,
  added_at: '2026-10-04T09:00:00Z',
  ...extra,
});

let memberMap: Map<number, number[]>;

beforeEach(() => {
  memberMap = new Map([
    [1, [7]],
    [2, [7, 9]],
    [3, [7]],
  ]);
  vi.mocked(api.getCollection).mockResolvedValue({
    collection: COLLECTION,
    properties: [member(1), member(2), member(3, { display_label: 'Bez polohy', is_active: false })],
  });
  vi.mocked(api.listCollections).mockResolvedValue({
    data: [COLLECTION, { ...COLLECTION, id: 9, name: 'Archiv', monitoring_enabled: false }],
    total: 2,
  });
  vi.mocked(api.removePropertyFromCollection).mockImplementation(async (cid, pid) => {
    memberMap.set(pid, (memberMap.get(pid) ?? []).filter((c) => c !== cid));
    return { removed: true };
  });
  /* Property 3 has no served location, so Browse has no card for it. */
  vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [cardRow(1), cardRow(2)], nextCursor: null });
  vi.mocked(queries.fetchPipelineMembers).mockResolvedValue(new Map() as PipelineMembers);
  vi.mocked(queries.fetchPipelineStages).mockResolvedValue([]);
  vi.mocked(queries.fetchPropertyCollectionMemberSet).mockImplementation(async () => new Map(memberMap));
  vi.mocked(queries.fetchIsDismissed).mockResolvedValue(false);
  vi.mocked(queries.fetchImagesForListingIds).mockResolvedValue(new Map());
});

afterEach(() => vi.clearAllMocks());

function renderPage(path = '/collection/7') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/collection/:id" element={<CollectionDetail />} />
          <Route path="/collections" element={<p>Seznam sbírek</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const cardFor = (place: string) => screen.getByRole('link', { name: new RegExp(place) }).closest('article')!;

describe('<CollectionDetail> on the phone', () => {
  it("reads Browse's cards scoped to the collection — any status, hidden included", async () => {
    renderPage();
    await screen.findByRole('link', { name: /Místo 1/ });
    const [filters] = vi.mocked(queries.fetchListingsForCards).mock.calls[0];
    expect(filters).toEqual(collectionCardFilters(7));
    expect(filters.collections).toEqual({ set: null, in: [7], out: [] });
    expect(filters.status).toBe('any');
    expect(filters.showDismissed).toBe(true);
    expect(filters.categoryType).toBeNull();
  });

  it('heads the roll with the name, the count and the monitored mark', async () => {
    renderPage();
    const heading = await screen.findByRole('heading', { name: 'K prohlídce' });
    const header = heading.closest('header')!;
    const text = header.textContent?.replace(/\u00a0/g, ' ');
    expect(text).toContain('3 nemovitosti');
    expect(text).toContain('založena 3. 10.');
    expect(within(header).getByText('sledováno')).toBeInTheDocument();
  });

  it('removes a property by unchecking this collection in the card sheet', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: /Místo 1/ });
    const bookmark = await within(cardFor('Místo 1')).findByRole('button', { name: 'Ve sbírce — upravit' });
    await user.click(bookmark);

    const sheet = screen.getByRole('dialog', { name: 'Uložit do sbírky' });
    const here = await within(sheet).findByRole('button', { name: /K prohlídce/ });
    expect(here).toHaveAttribute('aria-pressed', 'true');
    await user.click(here);

    await waitFor(() => expect(api.removePropertyFromCollection).toHaveBeenCalledWith(7, 1));
    await waitFor(() => expect(screen.queryByRole('link', { name: /Místo 1/ })).toBeNull());
    expect(screen.getByRole('link', { name: /Místo 2/ })).toBeInTheDocument();
  });

  it('keeps a member Browse cannot serve reachable as a plain line', async () => {
    renderPage();
    const section = await screen.findByRole('region', { name: 'Bez karty' });
    const line = within(section).getByRole('link');
    expect(line).toHaveAttribute('href', expect.stringContaining('3'));
    expect(line.textContent).toContain('Bez polohy');
    expect(within(line).getByText('neaktivní')).toBeInTheDocument();
  });

  it('shows the empty state for a collection with no members', async () => {
    vi.mocked(api.getCollection).mockResolvedValue({ collection: { ...COLLECTION, listing_count: 0 }, properties: [] });
    vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [], nextCursor: null });
    renderPage();
    expect(await screen.findByText('Sbírka je prázdná.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Otevřít Nové' })).toBeInTheDocument();
  });

  it('answers a missing collection as missing, not as a failure', async () => {
    vi.mocked(api.getCollection).mockRejectedValue(new api.ApiError('not found', 404, null));
    renderPage();
    expect(await screen.findByText('Taková sbírka není.')).toBeInTheDocument();
    expect(screen.getByText('Odkaz je špatně, sbírka byla na počítači smazána, nebo už s vámi není sdílená.')).toBeInTheDocument();
  });

  it('heads a collection shared with me as read-only, naming its owner', async () => {
    vi.mocked(api.getCollection).mockResolvedValue({
      collection: {
        ...COLLECTION,
        monitoring_enabled: false,
        owner_email: 'jana@example.com',
        shared_with: [{ account_id: 'me', email: null }],
      },
      properties: [member(1)],
    });
    renderPage();
    const heading = await screen.findByRole('heading', { name: 'K prohlídce' });
    const header = heading.closest('header');
    expect(header?.textContent).toContain('sdílí jana@example.com · jen ke čtení');
    expect(header?.textContent).not.toContain('založena');
    expect(within(heading.closest('header') as HTMLElement).queryByText('sledováno')).toBeNull();
    // A viewer manages nothing: no switch, no sharing, no ⋯.
    expect(within(header as HTMLElement).queryByRole('switch')).toBeNull();
    expect(within(header as HTMLElement).queryByRole('button', { name: /Sdílet/ })).toBeNull();
    expect(within(header as HTMLElement).queryByRole('button', { name: 'Další akce' })).toBeNull();
    expect(screen.queryByText(MANAGE_NOTE)).toBeNull();
    expect(screen.getByText(VIEWER_NOTE)).toBeInTheDocument();
  });

  it('does not invite adding to an empty collection shared with me', async () => {
    vi.mocked(api.getCollection).mockResolvedValue({
      collection: { ...COLLECTION, listing_count: 0, owner_email: 'jana@example.com', shared_with: [] },
      properties: [],
    });
    vi.mocked(queries.fetchListingsForCards).mockResolvedValue({ rows: [], nextCursor: null });
    renderPage();
    expect(await screen.findByText('Sbírka je zatím prázdná.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Otevřít Nové' })).toBeNull();
  });

  it("offers no removal from a collection shared with me: the card's sheet lists only my own", async () => {
    const shared = { ...COLLECTION, owner_email: 'jana@example.com', shared_with: [] };
    vi.mocked(api.getCollection).mockResolvedValue({ collection: shared, properties: [member(1), member(2)] });
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [shared, { ...COLLECTION, id: 9, name: 'Archiv', monitoring_enabled: false }],
      total: 2,
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('link', { name: /Místo 2/ });
    // Saved means MINE: property 1 sits only in the shared collection, so its mark is empty.
    expect(await within(cardFor('Místo 1')).findByRole('button', { name: 'Uložit do sbírky' }))
      .toBeInTheDocument();
    const bookmark = await within(cardFor('Místo 2')).findByRole('button', { name: 'Ve sbírce — upravit' });
    await user.click(bookmark);
    const sheet = screen.getByRole('dialog', { name: 'Uložit do sbírky' });
    await within(sheet).findByRole('button', { name: /Archiv/ });
    expect(within(sheet).queryByRole('button', { name: /K prohlídce/ })).toBeNull();
  });
});

/* Ruling P31, the phone following desktop (operator 2026-10-27): my collection's
   header carries the Sledovat switch, "Sdílet · N" and ⋯ (Přejmenovat, Smazat
   sbírku), each opening what the desktop's opens, over the same writes. */
describe('<CollectionDetail> on the phone: the header actions (P31)', () => {
  const header = async () =>
    (await screen.findByRole('heading', { name: 'K prohlídce' })).closest('header') as HTMLElement;

  it('carries Sledovat, Sdílet · N and ⋯, and no footer sends management to the desktop', async () => {
    vi.mocked(api.getCollection).mockResolvedValue({
      collection: { ...COLLECTION, shared_with: [{ account_id: 'a1', email: 'jana@example.com' }, { account_id: 'a2', email: 'petr@example.com' }] },
      properties: [member(1)],
    });
    renderPage();
    const h = await header();
    expect(within(h).getByRole('switch', { name: 'Sledovat' })).toHaveAttribute('aria-checked', 'true');
    expect(within(h).getByRole('button', { name: 'Sdílet · 2' })).toHaveAttribute('aria-expanded', 'false');
    expect(within(h).getByRole('button', { name: 'Další akce' })).toHaveAttribute('aria-haspopup', 'menu');
    expect(screen.queryByText(MANAGE_NOTE)).toBeNull();
    expect(screen.queryByText(/na počítači/)).toBeNull();
  });

  it('switches monitoring off through the same write as the desktop', async () => {
    vi.mocked(api.updateCollection).mockResolvedValue({ ...COLLECTION, monitoring_enabled: false });
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await header()).getByRole('switch', { name: 'Sledovat' }));
    expect(api.updateCollection).toHaveBeenCalledWith(7, { monitoring_enabled: false });
  });

  it('opens the sharing card under the header', async () => {
    const user = userEvent.setup();
    renderPage();
    const share = within(await header()).getByRole('button', { name: 'Sdílet' });
    await user.click(share);
    expect(share).toHaveAttribute('aria-expanded', 'true');
    const panel = document.getElementById(share.getAttribute('aria-controls') as string) as HTMLElement;
    expect(panel).not.toBeNull();
    expect(within(panel).getByText('Sdílení')).toBeInTheDocument();
  });

  it('renames in place from ⋯ → Přejmenovat', async () => {
    vi.mocked(api.updateCollection).mockResolvedValue({ ...COLLECTION, name: 'Na prohlídku' });
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await header()).getByRole('button', { name: 'Další akce' }));
    await user.click(screen.getByRole('menuitem', { name: 'Přejmenovat' }));
    const name = screen.getByLabelText('Název');
    expect(name).toHaveValue('K prohlídce');
    expect(name).toHaveFocus();
    await user.clear(name);
    await user.type(name, 'Na prohlídku');
    await user.click(screen.getByRole('button', { name: 'Uložit' }));
    expect(api.updateCollection).toHaveBeenCalledWith(7, { name: 'Na prohlídku', description: 'Vysočany, do 8 mil.' });
  });

  it('deletes after the two-step confirm from ⋯ → Smazat sbírku', async () => {
    vi.mocked(api.deleteCollection).mockResolvedValue({ deleted: true });
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await header()).getByRole('button', { name: 'Další akce' }));
    await user.click(screen.getByRole('menuitem', { name: 'Smazat sbírku' }));
    expect(api.deleteCollection).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Smazat „K prohlídce“' }));
    await waitFor(() => expect(api.deleteCollection).toHaveBeenCalledWith(7));
    // Gone, so back to the list, as on the desktop.
    expect(await screen.findByText('Seznam sbírek')).toBeInTheDocument();
  });

  it('offers no delete for the default collection', async () => {
    vi.mocked(api.getCollection).mockResolvedValue({
      collection: { ...COLLECTION, is_system: true },
      properties: [member(1)],
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await header()).getByRole('button', { name: 'Další akce' }));
    expect(screen.getByRole('menuitem', { name: 'Přejmenovat' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Smazat sbírku' })).toBeNull();
  });
});
