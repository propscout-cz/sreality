/* Poloha on the phone: the feed card's sheet and the property page share LocationBody.
 * The map is the API's streamed Mapy image, fetched with the JWT as bytes and shown from
 * a blob URL; the place and the three map apps survive any failure of the image, and a
 * property with no point says "Bez polohy" and nothing else. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';

import LocationBody from './LocationBody';
import LocationSheet from './LocationSheet';
import type { PropertyLocation } from './propertyLocation';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiGet: vi.fn(),
  apiGetBlob: vi.fn(),
}));

const LOC: PropertyLocation = {
  lat: 50.105123,
  lon: 14.5,
  granularity: 'obec',
  granularity_rank: 40,
  label: 'Dvory',
  zoom: 12,
  note: 'Poloha jen na úrovni obce',
};

const PNG = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: 'image/png' });

const refused = (status: number, reason: string) =>
  new ApiError(reason, status, { detail: { reason, message: reason } });

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const createObjectURL = vi.fn(() => 'blob:map');
const revokeObjectURL = vi.fn();

beforeEach(() => {
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  vi.mocked(api.apiGet).mockReset().mockResolvedValue(LOC);
  vi.mocked(api.apiGetBlob).mockReset().mockResolvedValue(PNG);
});

afterEach(() => {
  vi.mocked(api.apiGet).mockReset();
});

const SUBJECT = { property_id: 7, text: '2+kk · 54 m² · Dvory', figure: '7 490 000 Kč' };

describe('<LocationSheet>', () => {
  it('draws the map from the blob, the place, its note and the three map apps', async () => {
    wrap(<LocationSheet subject={SUBJECT} onClose={() => {}} />);
    const sheet = screen.getByRole('dialog', { name: 'Poloha' });
    expect(within(sheet).getByText('2+kk · 54 m² · Dvory')).toBeInTheDocument();

    const img = await within(sheet).findByRole('img', { name: 'Mapa: Dvory' });
    expect(img).toHaveAttribute('src', 'blob:map');
    expect(img).toHaveAttribute('width', '1080');
    expect(img).toHaveAttribute('height', '810');
    expect(createObjectURL).toHaveBeenCalledWith(PNG);
    expect(api.apiGet).toHaveBeenCalledWith('/properties/7/location', undefined, expect.anything());
    expect(api.apiGetBlob).toHaveBeenCalledWith(
      '/properties/7/static-map.png',
      { w: 1080 },
      expect.anything(),
    );

    expect(within(sheet).getByText('Dvory')).toBeInTheDocument();
    expect(within(sheet).getByText('Poloha jen na úrovni obce')).toBeInTheDocument();
    const links = within(within(sheet).getByRole('navigation', { name: 'Otevřít v mapách' })).getAllByRole('link');
    expect(links.map((a) => a.textContent)).toEqual(['Apple Maps', 'Google Maps', 'Mapy.cz']);
    expect(links[0]).toHaveAttribute('href', 'https://maps.apple.com/?ll=50.105123,14.5&q=Dvory&z=12');
    expect(links[1]).toHaveAttribute('href', 'https://www.google.com/maps/search/?api=1&query=50.105123,14.5');
    expect(links[2]).toHaveAttribute(
      'href',
      'https://mapy.com/fnc/v1/showmap?center=14.5,50.105123&zoom=12&marker=true',
    );
    /* Same window: the phone hands the link to the map app. */
    for (const a of links) expect(a).not.toHaveAttribute('target');
  });

  it('closes on Hotovo', async () => {
    const onClose = vi.fn();
    wrap(<LocationSheet subject={SUBJECT} onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: 'Hotovo' }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe('<LocationBody>', () => {
  it('holds the 4:3 box while the map loads, so nothing shifts', async () => {
    vi.mocked(api.apiGetBlob).mockReturnValue(new Promise(() => {}));
    const { container } = wrap(<LocationBody propertyId={7} />);
    const plate = container.querySelector('[aria-busy="true"]') as HTMLElement;
    expect(plate).toHaveClass('aspect-[4/3]');
    expect(await screen.findByText('Dvory')).toBeInTheDocument();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('keeps the place and the links, with a quiet line, when the map fails', async () => {
    vi.mocked(api.apiGetBlob).mockRejectedValue(refused(502, 'map_unavailable'));
    wrap(<LocationBody propertyId={7} />);
    expect(await screen.findByText('Mapu se nepodařilo načíst')).toBeInTheDocument();
    expect(screen.getByText('Dvory')).toBeInTheDocument();
    expect(screen.getAllByRole('link')).toHaveLength(3);
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('leaves the place and the links alone when the map is switched off', async () => {
    vi.mocked(api.apiGetBlob).mockRejectedValue(refused(404, 'disabled'));
    const { container } = wrap(<LocationBody propertyId={7} />);
    expect(await screen.findByText('Dvory')).toBeInTheDocument();
    await waitFor(() => expect(container.querySelector('.aspect-\\[4\\/3\\]')).toBeNull());
    expect(screen.queryByText('Mapu se nepodařilo načíst')).toBeNull();
    expect(screen.getAllByRole('link')).toHaveLength(3);
  });

  it('says "Bez polohy" and nothing else for a property with no point', async () => {
    vi.mocked(api.apiGet).mockRejectedValue(refused(404, 'no_location'));
    vi.mocked(api.apiGetBlob).mockRejectedValue(refused(404, 'no_location'));
    const { container } = wrap(<LocationBody propertyId={7} />);
    expect(await screen.findByText('Bez polohy')).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.queryByRole('img')).toBeNull();
    expect(container.textContent).toBe('Bez polohy');
  });

  it('prints no note for a street-level point', async () => {
    vi.mocked(api.apiGet).mockResolvedValue({
      ...LOC,
      granularity: 'address_point',
      granularity_rank: 100,
      label: 'Freyova 534/2, Praha',
      zoom: 16,
      note: null,
    });
    wrap(<LocationBody propertyId={7} />);
    expect(await screen.findByText('Freyova 534/2, Praha')).toBeInTheDocument();
    expect(screen.queryByText(/Poloha jen/)).toBeNull();
  });

  it('revokes the blob URL when it goes away', async () => {
    const { unmount } = wrap(<LocationBody propertyId={7} />);
    await screen.findByRole('img');
    unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:map');
  });
});
