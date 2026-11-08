/* Poloha while the map's chunk is still downloading: the place, its note and the three map
 * apps are already there, under the empty 4:3 box the map will fill, so nothing shifts when
 * it arrives. Only the chunk's download is stood in for, one that never finishes (React
 * caches a lazy's first answer, so this case has its own file). */
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';

import * as api from '@/lib/api';

import LocationBody from './LocationBody';
import type { PropertyLocation } from './propertyLocation';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiGet: vi.fn(),
}));
vi.mock('./PhoneMap', () => new Promise(() => {}));

const LOC: PropertyLocation = {
  lat: 50.105123,
  lon: 14.5,
  granularity: 'obec',
  granularity_rank: 40,
  label: 'Dvory',
  precision: 'town',
  note: 'Poloha jen na úrovni obce',
};

it('names the place, its note and the three map apps while the map is still on its way', async () => {
  vi.mocked(api.apiGet).mockResolvedValue(LOC);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}>
      <LocationBody propertyId={7} />
    </QueryClientProvider>,
  );
  const label = await screen.findByText('Dvory');
  expect(screen.getByText('Poloha jen na úrovni obce')).toBeInTheDocument();
  const nav = screen.getByRole('navigation', { name: 'Otevřít v mapách' });
  expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['Apple Maps', 'Google Maps', 'Mapy.cz']);

  const plate = container.querySelector('.aspect-\\[4\\/3\\]') as HTMLElement;
  expect(plate).toBeInTheDocument();
  expect(plate.compareDocumentPosition(label) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByText('Mapu se nepodařilo načíst')).toBeNull();
});
