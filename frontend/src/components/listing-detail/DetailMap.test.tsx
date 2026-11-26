/* The property page's header map and its large map (operator 2026-11-20): the minimap's
 * Vrstvy button opens the large map on its layer list, a pick there is every map's one
 * remembered selection, so the minimap behind it draws it too. MapLibre is faked (jsdom
 * has no WebGL): createMap is the one thing replaced. */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fakeCreateMap, fakeMaps, resetFakeMaps } from '@/test/fakeMap';

import DetailMap from './DetailMap';

vi.mock('@/lib/basemap', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/basemap')>()),
  createMap: (await import('@/test/fakeMap')).fakeCreateMap,
}));

function renderMap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DetailMap lat={50.09} lng={14.45} isActive heightClass="h-[170px]" label="Praha 8 – Karlín" />
    </QueryClientProvider>,
  );
}

const large = () => screen.getByRole('dialog', { name: 'Mapa · Praha 8 – Karlín' });

beforeEach(() => {
  localStorage.clear();
  resetFakeMaps();
});

describe('<DetailMap>', () => {
  it('pins the point at zoom 12.5 with no scale, its own rail holding Vrstvy and zoom', () => {
    renderMap();
    expect(fakeCreateMap).toHaveBeenCalledTimes(1);
    expect(fakeCreateMap.mock.calls[0][1]).toMatchObject({
      center: [14.45, 50.09],
      zoom: 12.5,
      navigationControl: false,
      scaleControl: false,
    });
    act(() => fakeMaps[0].fire('load'));
    expect(fakeMaps[0].getLayersOrder()).toEqual(['relief', 'contours', 'cadastre', 'cadastre-photo', 'aerial', 'pin']);
    expect(screen.getByRole('button', { name: 'Vrstvy (větší mapa)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Přiblížit' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens the large map on the minimap’s view and its layer list', async () => {
    renderMap();
    act(() => fakeMaps[0].fire('load'));
    await userEvent.click(screen.getByRole('button', { name: 'Vrstvy (větší mapa)' }));
    expect(large()).toBeInTheDocument();
    await waitFor(() => expect(fakeMaps).toHaveLength(2));
    expect(fakeCreateMap.mock.calls[1][1]).toMatchObject({ center: [14.45, 50.09], zoom: 12.5, navigationControl: false });
    const panel = within(large()).getByRole('region', { name: 'Vrstvy' });
    expect(within(panel).getByRole('group', { name: 'Podklad mapy' })).toBeInTheDocument();
    expect(within(panel).getByRole('checkbox', { name: 'Reliéf a vrstevnice' })).toBeInTheDocument();
  });

  it('draws a pick made on the large map on the minimap too, and keeps the minimap on close', async () => {
    renderMap();
    act(() => fakeMaps[0].fire('load'));
    await userEvent.click(screen.getByRole('button', { name: 'Vrstvy (větší mapa)' }));
    await waitFor(() => expect(fakeMaps).toHaveLength(2));
    act(() => fakeMaps[1].fire('load'));
    const bar = within(large()).getByRole('group', { name: 'Podklad mapy' });
    await userEvent.click(within(bar).getByRole('button', { name: 'Letecká' }));
    expect(fakeMaps[1].layout.get('aerial')).toEqual({ visibility: 'visible' });
    expect(fakeMaps[0].layout.get('aerial')).toEqual({ visibility: 'visible' });

    await userEvent.click(within(large()).getByRole('button', { name: 'Zavřít mapu' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(fakeMaps[1].removed).toBe(true);
    expect(fakeMaps[0].removed).toBe(false);
    expect(fakeMaps[0].layout.get('aerial')).toEqual({ visibility: 'visible' });
  });
});
