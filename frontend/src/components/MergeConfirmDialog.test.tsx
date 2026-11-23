/* The last look before a merge: every ad of every property a letter names, the
 * inactive ones and the ones nobody clicked included, under the property it
 * sits on today; the dialog reads, and only its button sends. */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import MergeConfirmDialog from './MergeConfirmDialog';
import * as queries from '@/lib/queries';
import type { ListingPublic } from '@/lib/types';

vi.mock('@/lib/queries', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/queries')>()),
  fetchListingsForPropertyIds: vi.fn(),
  fetchImagesForListingIds: vi.fn(async () => new Map()),
}));

const ad = (id: number, property_id: number, over: Partial<ListingPublic> = {}): ListingPublic =>
  ({
    id, property_id, sreality_id: null, source: 'idnes', source_id_native: `n${id}`,
    is_active: true, category_main: 'byt', category_type: 'prodej', disposition: '2+kk',
    area_m2: 50, price_czk: 5_000_000, first_seen_at: '2026-10-01T00:00:00Z',
    last_seen_at: '2026-11-20T00:00:00Z', source_url: `https://example.test/${id}`,
    ...over,
  }) as ListingPublic;

const GROUPS = [
  ['A', [10, 11]],
  ['B', [20, 21]],
] as const;

function open(over: { onConfirm?: () => void; onClose?: () => void; merging?: boolean } = {}) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <MergeConfirmDialog
          groups={GROUPS}
          merging={over.merging ?? false}
          onConfirm={over.onConfirm ?? vi.fn()}
          onClose={over.onClose ?? vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  vi.mocked(queries.fetchListingsForPropertyIds).mockReset();
});

describe('MergeConfirmDialog', () => {
  it('reads every ad of every named property in one request', async () => {
    vi.mocked(queries.fetchListingsForPropertyIds).mockResolvedValue([]);
    open();
    await waitFor(() => expect(queries.fetchListingsForPropertyIds).toHaveBeenCalledTimes(1));
    expect(vi.mocked(queries.fetchListingsForPropertyIds).mock.calls[0][0]).toEqual([10, 11, 20, 21]);
  });

  it('shows each letter as one property to be, with the ads of each property it joins', async () => {
    vi.mocked(queries.fetchListingsForPropertyIds).mockResolvedValue([
      ad(1, 10), ad(2, 10, { is_active: false }), ad(3, 11), ad(4, 20), ad(5, 21),
    ]);
    open();
    const a = await screen.findByRole('region', { name: 'Písmeno A' });
    const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s+/g, ' ');
    expect(text(within(a).getByRole('heading', { level: 3 }))).toContain(
      'z 2 nemovitostí jedna · 3 inzeráty',
    );
    const ten = within(a).getByRole('link', { name: '#10' });
    expect(ten.getAttribute('href')).toBe('/property/10');
    expect(text(ten.parentElement as HTMLElement)).toContain('#10 · 2 inzeráty');
    const b = screen.getByRole('region', { name: 'Písmeno B' });
    expect(text(within(b).getByRole('heading', { level: 3 }))).toContain(
      'z 2 nemovitostí jedna · 2 inzeráty',
    );
  });

  it('sends nothing until the ads are read, then only on its own button', async () => {
    let land: (rows: ListingPublic[]) => void = () => {};
    vi.mocked(queries.fetchListingsForPropertyIds).mockReturnValue(
      new Promise((resolve) => { land = resolve; }),
    );
    const onConfirm = vi.fn();
    open({ onConfirm });
    const merge = screen.getByRole('button', { name: 'Sloučit' }) as HTMLButtonElement;
    expect(merge.disabled).toBe(true);
    land([ad(1, 10), ad(3, 11), ad(4, 20), ad(5, 21)]);
    await waitFor(() => expect(merge.disabled).toBe(false));
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(merge);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('goes back without merging', async () => {
    vi.mocked(queries.fetchListingsForPropertyIds).mockResolvedValue([]);
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    open({ onClose, onConfirm });
    fireEvent.click(screen.getByRole('button', { name: 'Zpět' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
