/* Poloha on the phone: the feed card's sheet and the property page share LocationBody.
 * The map is a lazy MapLibre map built through createMap, which is the one thing faked here
 * (jsdom has no WebGL); the place and the three map apps never wait for it, and a property
 * with no point says "Bez polohy" and nothing else. */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { fakeCreateMap, fakeMaps, resetFakeMaps } from '@/test/fakeMap';

import LocationBody from './LocationBody';
import LocationSheet from './LocationSheet';
import type { PropertyLocation } from './propertyLocation';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiGet: vi.fn(),
  apiGetBlob: vi.fn(),
}));
vi.mock('@/lib/basemap', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/basemap')>()),
  createMap: (await import('@/test/fakeMap')).fakeCreateMap,
}));

const LOC: PropertyLocation = {
  lat: 50.105123,
  lon: 14.5,
  granularity: 'obec',
  granularity_rank: 40,
  label: 'Dvory',
  precision: 'town',
  note: 'Poloha jen na úrovni obce',
};

const STREET: PropertyLocation = {
  ...LOC,
  granularity: 'address_point',
  granularity_rank: 100,
  label: 'Freyova 534/2, Praha',
  precision: 'street',
  note: null,
};

const refused = (status: number, reason: string) =>
  new ApiError(reason, status, { detail: { reason, message: reason } });

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

/* The map is a lazy chunk over the real basemap module: load it once up front so no test
 * pays MapLibre's module load inside a findBy window on a loaded runner. */
beforeAll(async () => {
  await import('./PhoneMap');
}, 30_000);

const LOADED = { timeout: 5_000 };

beforeEach(() => {
  resetFakeMaps();
  vi.mocked(api.apiGet).mockReset().mockResolvedValue(LOC);
  vi.mocked(api.apiGetBlob).mockReset();
});

afterEach(() => {
  vi.mocked(api.apiGet).mockReset();
});

/* What a failed map leaves: the place, its note and the three map apps, a quiet line under
 * the note, and no plate. */
async function expectMapFailed(container: HTMLElement) {
  const line = await screen.findByText('Mapu se nepodařilo načíst', {}, LOADED);
  const label = screen.getByText('Dvory');
  const note = screen.getByText('Poloha jen na úrovni obce');
  expect(line.parentElement).toBe(label.parentElement);
  expect(note.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByRole('link')).toHaveLength(3);
  expect(container.querySelector('.aspect-\\[4\\/3\\]')).toBeNull();
}

/* React and the boundary log a caught map failure; keep the run quiet. */
const quietConsole = () => vi.spyOn(console, 'error').mockImplementation(() => {});

const SUBJECT = { property_id: 7, text: '2+kk · 54 m² · Dvory', figure: '7 490 000 Kč' };

describe('<LocationSheet>', () => {
  it('names the place and its three map apps, then builds the map at the point', async () => {
    vi.mocked(api.apiGet).mockResolvedValue(STREET);
    wrap(<LocationSheet subject={SUBJECT} onClose={() => {}} />);
    const sheet = screen.getByRole('dialog', { name: 'Poloha' });
    expect(within(sheet).getByText('2+kk · 54 m² · Dvory')).toBeInTheDocument();

    expect(await within(sheet).findByText('Freyova 534/2, Praha')).toBeInTheDocument();
    expect(api.apiGet).toHaveBeenCalledWith('/properties/7/location', undefined, expect.anything());
    const links = within(within(sheet).getByRole('navigation', { name: 'Otevřít v mapách' })).getAllByRole('link');
    expect(links.map((a) => a.textContent)).toEqual(['Apple Maps', 'Google Maps', 'Mapy.cz']);
    expect(links[0]).toHaveAttribute(
      'href',
      'https://maps.apple.com/?ll=50.105123,14.5&q=Freyova%20534%2F2%2C%20Praha&z=16',
    );
    expect(links[1]).toHaveAttribute('href', 'https://www.google.com/maps/search/?api=1&query=50.105123,14.5');
    expect(links[2]).toHaveAttribute(
      'href',
      'https://mapy.com/fnc/v1/showmap?center=14.5,50.105123&zoom=16&marker=true',
    );
    /* Same window: the phone hands the link to the map app. */
    for (const a of links) expect(a).not.toHaveAttribute('target');

    await waitFor(() => expect(fakeCreateMap).toHaveBeenCalledTimes(1), LOADED);
    const [container, options] = fakeCreateMap.mock.calls[0];
    expect(sheet).toContainElement(container);
    expect(options).toMatchObject({ center: [14.5, 50.105123], zoom: 15, cooperativeGestures: true });
  }, 15_000);

  it('closes on Hotovo', async () => {
    const onClose = vi.fn();
    wrap(<LocationSheet subject={SUBJECT} onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: 'Hotovo' }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe('<LocationBody>', () => {
  it('says "Bez polohy" and nothing else for a property with no point', async () => {
    vi.mocked(api.apiGet).mockRejectedValue(refused(404, 'no_location'));
    const { container } = wrap(<LocationBody propertyId={7} />);
    expect(await screen.findByText('Bez polohy')).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
    expect(container.textContent).toBe('Bez polohy');
    expect(fakeCreateMap).not.toHaveBeenCalled();
  });

  it('pins the point once the map has loaded', async () => {
    wrap(<LocationBody propertyId={7} />);
    await waitFor(() => expect(fakeMaps).toHaveLength(1), LOADED);
    fakeMaps[0].fire('load');
    expect(fakeMaps[0].sources.get('pin')).toMatchObject({
      type: 'geojson',
      data: { geometry: { type: 'Point', coordinates: [14.5, 50.105123] } },
    });
    expect(fakeMaps[0].layers).toEqual([expect.objectContaining({ id: 'pin', type: 'circle', source: 'pin' })]);
  }, 15_000);

  /* iOS caps the WebGL contexts a page may hold. */
  it('releases the map when the user leaves', async () => {
    const { unmount } = wrap(<LocationBody propertyId={7} />);
    await waitFor(() => expect(fakeMaps).toHaveLength(1), LOADED);
    expect(fakeMaps[0].removed).toBe(false);
    unmount();
    expect(fakeMaps[0].removed).toBe(true);
  }, 15_000);

  it('keeps the place, its note and the links, with a quiet line, when the map cannot be built', async () => {
    const quiet = quietConsole();
    fakeCreateMap.mockImplementationOnce(() => {
      throw new Error('Failed to initialize WebGL');
    });
    const { container } = wrap(<LocationBody propertyId={7} />);
    await expectMapFailed(container);
    quiet.mockRestore();
  }, 15_000);

  it('falls back the same way, and releases the map, when its WebGL context is lost', async () => {
    const quiet = quietConsole();
    const { container } = wrap(<LocationBody propertyId={7} />);
    await waitFor(() => expect(fakeMaps).toHaveLength(1), LOADED);
    act(() => fakeMaps[0].fire('webglcontextlost'));
    await expectMapFailed(container);
    expect(fakeMaps[0].removed).toBe(true);
    quiet.mockRestore();
  }, 15_000);

  it('prints no note for a street-level point', async () => {
    vi.mocked(api.apiGet).mockResolvedValue(STREET);
    wrap(<LocationBody propertyId={7} />);
    expect(await screen.findByText('Freyova 534/2, Praha')).toBeInTheDocument();
    expect(screen.queryByText(/Poloha jen/)).toBeNull();
  });

  it.each([
    [
      'street',
      15,
      null,
      'https://maps.apple.com/?ll=50.105123,14.5&q=Dvory&z=16',
      'https://mapy.com/fnc/v1/showmap?center=14.5,50.105123&zoom=16&marker=true',
    ],
    [
      'quarter',
      13,
      'Poloha jen na úrovni části obce',
      'https://maps.apple.com/?ll=50.105123,14.5&q=Dvory&z=14',
      'https://mapy.com/fnc/v1/showmap?center=14.5,50.105123&zoom=14&marker=true',
    ],
    [
      'town',
      11,
      'Poloha jen na úrovni obce',
      'https://maps.apple.com/?ll=50.105123,14.5&q=Dvory&z=12',
      'https://mapy.com/fnc/v1/showmap?center=14.5,50.105123&zoom=12&marker=true',
    ],
    [
      'coarse',
      9,
      'Poloha jen přibližná, hrubší než obec',
      'https://maps.apple.com/?ll=50.105123,14.5&q=Dvory&z=10',
      'https://mapy.com/fnc/v1/showmap?center=14.5,50.105123&zoom=10&marker=true',
    ],
  ] as const)(
    'a %s point opens the map at zoom %i, and Apple Maps and Mapy.cz one step closer',
    async (precision, zoom, note, apple, mapy) => {
      vi.mocked(api.apiGet).mockResolvedValue({ ...LOC, precision, note });
      wrap(<LocationBody propertyId={7} />);
      const nav = await screen.findByRole('navigation', { name: 'Otevřít v mapách' });
      const links = within(nav).getAllByRole('link');
      expect(links[0]).toHaveAttribute('href', apple);
      expect(links[2]).toHaveAttribute('href', mapy);
      expect(screen.queryByText(/^Poloha jen/)?.textContent ?? null).toBe(note);
      await waitFor(() => expect(fakeCreateMap).toHaveBeenCalledTimes(1), LOADED);
      expect(fakeCreateMap.mock.calls[0][1]).toMatchObject({ zoom });
    },
    15_000,
  );
});
