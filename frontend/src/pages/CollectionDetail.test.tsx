/* CollectionDetail — the edit fields and the monitoring switch announce
 * themselves.
 *
 * Hermetic: mock getCollection. The three controls this pins were all nameless
 * — two inputs carrying only a placeholder, and a role="switch" whose only
 * child is a decorative knob, so a screen reader read it as "switch, off" with
 * nothing to say WHAT is off.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import CollectionDetail from './CollectionDetail';
import type { CollectionPropertyRow, CollectionWithProperties } from '@/lib/types';
import * as api from '@/lib/api';
import { curationKeys } from '@/lib/queries';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    getCollection: vi.fn(),
    removePropertyFromCollection: vi.fn(),
    shareCollection: vi.fn(),
    unshareCollection: vi.fn(),
  };
});

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

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidate = vi.spyOn(qc, 'invalidateQueries');
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/collections/1']}>
        <Routes>
          <Route path="/collections/:id" element={<CollectionDetail />} />
          <Route path="/collections" element={<p>collections list</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { invalidate };
}

describe('<CollectionDetail>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getCollection).mockResolvedValue(DATA);
    vi.mocked(api.removePropertyFromCollection).mockResolvedValue({
      removed: true,
    });
  });

  it('names both edit fields', async () => {
    renderPage();
    const name = await screen.findByRole('textbox', { name: 'Name' });
    expect(name).toHaveValue('Vinohrady watch');
    expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue(
      'Flats worth a second look.',
    );
  });

  it('says a failed read with a retry that reads again (MS16)', async () => {
    vi.mocked(api.getCollection).mockRejectedValueOnce(new Error('HTTP 500'));
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Zkusit znovu' }));
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('Vinohrady watch');
  });

  it('names the monitoring switch from the visible caption above it', async () => {
    renderPage();
    const toggle = await screen.findByRole('switch', { name: 'Monitoring' });
    expect(toggle).toHaveAccessibleName('Monitoring');
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  /* Removing a member here changes the SAME fact the Browse card glyph and the
     listing header paint, so it has to revalidate the shared member map — this
     row used to hand-type a key list that omitted it. */
  it('removes a member through the API and revalidates the shared member map', async () => {
    vi.mocked(api.getCollection).mockResolvedValue({
      ...DATA,
      properties: [MEMBER],
    });
    const { invalidate } = renderPage();

    fireEvent.click(
      await screen.findByRole('button', { name: 'Remove listing from collection' }),
    );

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

  /* Sharing (migration 610): the owner shares by email, sees every recipient,
   * and stops sharing in two steps; a refusal sits under the field. */
  describe('Sharing, as the owner', () => {
    const OWNED: CollectionWithProperties = {
      ...DATA,
      collection: {
        ...DATA.collection,
        shared_with: [{ account_id: 'acc-b', email: 'petr@example.cz' }],
      },
    };

    it('shares with a typed email and revalidates the collection', async () => {
      vi.mocked(api.shareCollection).mockResolvedValue(OWNED.collection);
      const { invalidate } = renderPage();
      expect(await screen.findByText('Sharing')).toBeInTheDocument();
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
      expect(await screen.findByText('Sharing')).toBeInTheDocument();
      expect(screen.queryByText('Access')).toBeNull();
      expect(screen.queryByRole('button', { name: 'Can edit' })).toBeNull();
    });

    it('shows a 422 inline under the field', async () => {
      vi.mocked(api.shareCollection).mockRejectedValue(
        new api.ApiError('No account uses this email.', 422, null),
      );
      renderPage();
      fireEvent.change(await screen.findByRole('textbox', { name: 'Email' }), {
        target: { value: 'nikdo@example.cz' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Share' }));
      expect(await screen.findByRole('alert')).toHaveTextContent('No account uses this email.');
    });

    it('lists the recipients and stops sharing only after a confirm', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(OWNED);
      vi.mocked(api.unshareCollection).mockResolvedValue({ deleted: true });
      renderPage();

      const list = await screen.findByRole('list', { name: 'Shared with' });
      expect(within(list).getByText('petr@example.cz')).toBeInTheDocument();
      fireEvent.click(within(list).getByRole('button', { name: 'Stop sharing with petr@example.cz' }));
      expect(api.unshareCollection).not.toHaveBeenCalled();
      fireEvent.click(within(list).getByRole('button', { name: 'Stop sharing' }));
      await waitFor(() => expect(api.unshareCollection).toHaveBeenCalledWith(1, 'acc-b'));
    });
  });

  /* The recipient's view: read-only, with one write — leaving. */
  describe('as a viewer of a collection shared with me', () => {
    const SHARED: CollectionWithProperties = {
      collection: {
        ...DATA.collection,
        owner_email: 'petr@example.cz',
        shared_with: [{ account_id: 'acc-me', email: null }],
      },
      properties: [MEMBER],
    };

    it('names the owner, hides every owner control, and keeps the table', async () => {
      vi.mocked(api.getCollection).mockResolvedValue(SHARED);
      renderPage();

      expect(await screen.findByText('petr@example.cz')).toBeInTheDocument();
      expect(screen.getByText(/view only/)).toBeInTheDocument();
      expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull();
      expect(screen.queryByRole('switch', { name: 'Monitoring' })).toBeNull();
      expect(screen.queryByText('Sharing')).toBeNull();
      expect(screen.queryByRole('button', { name: 'Remove listing from collection' })).toBeNull();
      expect(screen.getByRole('link', { name: '900' })).toBeInTheDocument();
    });

    it('says nothing about adding when the shared collection is empty', async () => {
      vi.mocked(api.getCollection).mockResolvedValue({ ...SHARED, properties: [] });
      renderPage();
      expect(await screen.findByText('No properties in this collection yet.')).toBeInTheDocument();
      expect(screen.queryByText(/Add to collection/)).toBeNull();
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
