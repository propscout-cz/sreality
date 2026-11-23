/* Poloha when the map's chunk cannot download because the phone is offline: lazyChunk hands
 * that to MapSlot instead of reloading into the browser's offline page, and the place, its
 * note and the three map apps stay with a quiet line. Only the chunk's download is stood in
 * for (vitest cannot make a real import() fail, and React caches a lazy's first answer, so
 * this case has its own file); lazyChunk, Suspense and the boundary are real. */
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { __resetChunkLoaderForTests } from '@/lib/lazyChunk';

import LocationBody from './LocationBody';
import type { PropertyLocation } from './propertyLocation';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiGet: vi.fn(),
}));
vi.mock('./PhoneMap', async () => {
  throw new TypeError('Failed to fetch dynamically imported module');
});

const LOC: PropertyLocation = {
  lat: 50.105123,
  lon: 14.5,
  granularity: 'obec',
  granularity_rank: 40,
  label: 'Dvory',
  precision: 'town',
  note: 'Poloha jen na úrovni obce',
};

const reload = vi.fn();

beforeEach(() => {
  __resetChunkLoaderForTests();
  reload.mockClear();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...window.location, reload },
  });
  Object.defineProperty(window.navigator, 'onLine', { configurable: true, value: false });
  vi.mocked(api.apiGet).mockReset().mockResolvedValue(LOC);
});

it('keeps the place, its note and the links, with a quiet line, when the map cannot download offline', async () => {
  const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}>
      <LocationBody propertyId={7} />
    </QueryClientProvider>,
  );
  const line = await screen.findByText('Mapu se nepodařilo načíst');
  const note = screen.getByText('Poloha jen na úrovni obce');
  expect(line.parentElement).toBe(screen.getByText('Dvory').parentElement);
  expect(note.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByRole('link')).toHaveLength(3);
  expect(container.querySelector('.aspect-\\[4\\/3\\]')).toBeNull();
  /* A reload would trade the working page for the browser's offline one. */
  expect(reload).not.toHaveBeenCalled();
  quiet.mockRestore();
});
