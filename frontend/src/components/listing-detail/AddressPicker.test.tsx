/* The address picker (location W3b) on own place search (#2453): a row is ONE RÚIAN unit, so a
 * door, street or town row is saved as it is on every advert and the page's place is re-read; any
 * other level offers no save, and a save the portal's words defeated says so instead of closing. */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { PLACES_UNAVAILABLE, type PlaceRow } from '@/lib/places';
import type { PickResult } from '@/lib/locationQuality';

const fetchPlaces = vi.hoisted(() => vi.fn());
const submitLocationPick = vi.hoisted(() => vi.fn());
vi.mock('@/lib/places', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/places')>()),
  fetchPlaces,
}));
vi.mock('@/lib/locationQuality', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/locationQuality')>()),
  submitLocationPick,
}));

import AddressPicker from './AddressPicker';
import { PropertyIdentity } from './ListingOverview';
import { propertyKeys } from '@/lib/mergedAdverts';
import { propertyLocationKeys } from '@/components/phone/propertyLocation';
import type { ListingPublic } from '@/lib/types';
import { expectNoNestedInteractive } from '@/test/a11y';

const PENCIL = 'Opravit adresu (RÚIAN)';
const DOOR: PlaceRow = {
  level: 'adresni_misto', id: 21000001, name: 'Lipová 12, Betov', context: 'Alfov', point: { lat: 50.1, lng: 14.5 },
  street: { level: 'ulice', id: 9002, name: 'Lipová', context: 'Betov' },
};
const STREET: PlaceRow = { level: 'ulice', id: 9002, name: 'Lipová', context: 'Betov', point: { lat: 50.1, lng: 14.5 } };
const PART: PlaceRow = { level: 'cast_obce', id: 5, name: 'Žižkov', context: 'Praha', point: null };

const saved = (over: Partial<PickResult> = {}): { data: PickResult } => ({
  data: {
    property_id: 7, pick: { level: 'adresni_misto', code: 21000001 }, ads: [], written: 2, enqueued: 2,
    resolved: true, location: { label: 'Lipová 12, Betov', granularity: 'address_point' }, held: true,
    ...over,
  },
});

function renderPicker() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidated = vi.spyOn(qc, 'invalidateQueries');
  render(
    <QueryClientProvider client={qc}>
      <AddressPicker propertyId={7} label="Sadová, Alfov" adCount={2} />
    </QueryClientProvider>,
  );
  return invalidated;
}

async function pick(row: PlaceRow) {
  fetchPlaces.mockResolvedValue([row]);
  fireEvent.click(screen.getByRole('button', { name: PENCIL }));
  fireEvent.change(await screen.findByRole('textbox', { name: 'Adresa, ulice nebo obec' }), {
    target: { value: 'Lipová 12' },
  });
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(`^${row.name}`) }));
}

const panel = () => screen.getByRole('group', { name: PENCIL });

beforeEach(() => {
  vi.clearAllMocks();
  submitLocationPick.mockResolvedValue(saved());
});

describe('<AddressPicker>', () => {
  it('is a slot: the identity column draws no pencil unless the page passes one', () => {
    const listing = { id: 1, source: 'bazos', display_label: 'Sadová, Alfov', is_active: true } as unknown as ListingPublic;
    const { unmount } = render(<PropertyIdentity listing={listing} showStatus={false} />);
    expect(screen.queryByRole('button', { name: PENCIL })).toBeNull();
    unmount();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PropertyIdentity listing={listing} showStatus={false} addressAction={<AddressPicker propertyId={7} label="x" adCount={1} />} />
      </QueryClientProvider>,
    );
    expect(screen.getByText('Sadová, Alfov')).toContainElement(screen.getByRole('button', { name: PENCIL }));
  });

  it('previews a door row and saves it on every advert, as {adresni_misto, door code}', async () => {
    const invalidated = renderPicker();
    await pick(DOOR);
    const status = await within(panel()).findByRole('status');
    await waitFor(() => expect(status).toHaveTextContent('Adresní místoLipová 12, Betov · Alfov'));

    fireEvent.click(screen.getByRole('button', { name: 'Uložit pro všechny inzeráty (2)' }));
    await waitFor(() => expect(screen.queryByRole('group', { name: PENCIL })).toBeNull());
    expect(submitLocationPick).toHaveBeenCalledWith({ property_id: 7, pick: { level: 'adresni_misto', code: 21000001 } });
    for (const key of [propertyKeys.row(7), propertyLocationKeys.location(7)]) {
      expect(invalidated).toHaveBeenCalledWith({ queryKey: key });
    }
  });

  it('offers no save for a row at any other level', async () => {
    renderPicker();
    await pick(PART);
    expect(await within(panel()).findByText('Vyberte adresu, ulici nebo obec')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Uložit/ })).toBeNull();
  });

  it('says own search is unavailable and keeps the box', async () => {
    fetchPlaces.mockRejectedValue(new Error(PLACES_UNAVAILABLE));
    renderPicker();
    fireEvent.click(screen.getByRole('button', { name: PENCIL }));
    const box = await screen.findByRole('textbox', { name: 'Adresa, ulice nebo obec' });
    fireEvent.change(box, { target: { value: 'Lipová 12' } });
    expect(await within(panel()).findByText('Vyhledávání míst je teď nedostupné; zkuste psát dál.')).toBeInTheDocument();
    expect(box).toBeEnabled();
  });

  it('stays open and says so when the portals’ words defeated the pick', async () => {
    submitLocationPick.mockResolvedValue(saved({ held: false, location: { label: 'Sadová, Alfov', granularity: 'street' } }));
    renderPicker();
    await pick(STREET);
    fireEvent.click(await screen.findByRole('button', { name: 'Uložit pro všechny inzeráty (2)' }));
    expect(await within(panel()).findByText(/Uloženo, ale poloha je stále „Sadová, Alfov“/)).toBeInTheDocument();
  });

  it('hands every advert back to its own words', async () => {
    submitLocationPick.mockResolvedValue(saved({ pick: null, held: null }));
    renderPicker();
    fireEvent.click(screen.getByRole('button', { name: PENCIL }));
    fireEvent.click(await screen.findByRole('button', { name: 'Vrátit údaje z inzerátů' }));
    await waitFor(() => expect(submitLocationPick).toHaveBeenCalledWith({ property_id: 7, pick: null }));
  });

  it('names its controls and nests none', async () => {
    renderPicker();
    expect(screen.getByRole('button', { name: PENCIL })).toHaveAttribute('aria-expanded', 'false');
    await pick(DOOR);
    expect(screen.getByRole('button', { name: PENCIL })).toHaveAttribute('aria-expanded', 'true');
    expect(within(panel()).getByText('Sadová, Alfov')).toBeInTheDocument();
    expectNoNestedInteractive(document.body);
  });
});
