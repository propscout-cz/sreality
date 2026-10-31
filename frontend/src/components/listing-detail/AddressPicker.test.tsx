/* The address picker (location W3b): a pick is bound to ONE RÚIAN unit before anything can be
 * saved, a save writes it on every advert and re-reads the page's place, a refused or unbound
 * pick offers no save, and a save the portal's words defeated says so instead of closing. */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { LocationResolution, MapySuggestion } from '@/lib/maps';
import type { PickResult } from '@/lib/locationQuality';

const fetchSuggest = vi.hoisted(() => vi.fn());
const resolveSuggestion = vi.hoisted(() => vi.fn());
const submitLocationPick = vi.hoisted(() => vi.fn());
vi.mock('@/lib/maps', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/maps')>()),
  fetchSuggest,
  resolveSuggestion,
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
const ADDRESS: MapySuggestion = {
  name: 'Lipová 12', label: 'Adresa', type: 'regional.address', location: 'Betov',
  position: { lon: 14.5, lat: 50.1 },
  regionalStructure: [
    { name: '12', type: 'regional.address' },
    { name: 'Lipová', type: 'regional.street' },
    { name: 'Betov', type: 'regional.municipality' },
  ],
};
const DOOR: LocationResolution = { level: 'adresni_misto', id: 21000001, name: 'Lipová 12, Betov', context: null };

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

async function pick(answer: LocationResolution | null) {
  resolveSuggestion.mockImplementation(() => (answer ? Promise.resolve(answer) : Promise.reject(new Error('x'))));
  fireEvent.click(screen.getByRole('button', { name: PENCIL }));
  fireEvent.change(await screen.findByRole('textbox', { name: 'Adresa, ulice nebo obec' }), {
    target: { value: 'Lipová 12' },
  });
  fireEvent.click(await screen.findByRole('button', { name: /Lipová 12/ }));
}

const panel = () => screen.getByRole('group', { name: PENCIL });

beforeEach(() => {
  vi.clearAllMocks();
  fetchSuggest.mockResolvedValue([ADDRESS]);
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

  it('binds the pick as a door, previews it and saves it on every advert', async () => {
    const invalidated = renderPicker();
    await pick(DOOR);
    expect(resolveSuggestion).toHaveBeenCalledWith(ADDRESS, { addressPoint: true });
    const status = await within(panel()).findByRole('status');
    await waitFor(() => expect(status).toHaveTextContent('Adresní místoLipová 12, Betov'));

    fireEvent.click(screen.getByRole('button', { name: 'Uložit pro všechny inzeráty (2)' }));
    await waitFor(() => expect(screen.queryByRole('group', { name: PENCIL })).toBeNull());
    expect(submitLocationPick).toHaveBeenCalledWith({ property_id: 7, pick: { level: 'adresni_misto', code: 21000001 } });
    for (const key of [propertyKeys.row(7), propertyLocationKeys.location(7), propertyLocationKeys.staticMap(7)]) {
      expect(invalidated).toHaveBeenCalledWith({ queryKey: key });
    }
  });

  it.each([
    [{ level: 'cast_obce', id: 5, name: 'Žižkov', context: 'Praha' }, 'Vyberte adresu, ulici nebo obec'],
    [{ ...DOOR, id: null, name: null }, 'V RÚIAN nelze jednoznačně najít'],
    [null, 'Místo se nepodařilo ověřit — zkuste to znovu'],
  ])('offers no save for %j', async (answer, said) => {
    renderPicker();
    await pick(answer as LocationResolution | null);
    expect(await within(panel()).findByText(said)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Uložit/ })).toBeNull();
  });

  it('stays open and says so when the portals’ words defeated the pick', async () => {
    submitLocationPick.mockResolvedValue(saved({ held: false, location: { label: 'Sadová, Alfov', granularity: 'street' } }));
    renderPicker();
    await pick({ level: 'ulice', id: 9002, name: 'Lipová', context: 'Betov' });
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
