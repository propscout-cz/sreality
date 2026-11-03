/* `/maps/resolve` has ONE answer shape and takes ONE request body; the SPA
 * neither re-shapes the answer nor guesses a level of its own. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', async (orig) => ({
  ...(await orig<typeof import('./api')>()),
  apiPost: vi.fn(),
}));

import { apiPost } from './api';
import { resolveSuggestion, typeBadge, type MapySuggestion } from './maps';

const ADDRESS: MapySuggestion = {
  name: 'Na Rybníčkách 12',
  label: 'Adresa',
  type: 'regional.address',
  location: 'Pardubice',
  position: { lon: 15.77, lat: 50.03 },
  regionalStructure: [
    { name: 'Na Rybníčkách 12', type: 'regional.address' },
    { name: 'Na Rybníčkách', type: 'regional.street' },
    { name: 'Pardubice', type: 'regional.municipality' },
  ],
  extra_mapy_field: 'not sent',
};
const STRUCTURE = ADDRESS.regionalStructure!.map(({ name, type }) => ({ name, type }));

beforeEach(() => vi.mocked(apiPost).mockReset());

describe('resolveSuggestion', () => {
  it('posts the pick as {lat, lng, type, name, regional_structure} and nothing else', async () => {
    vi.mocked(apiPost).mockResolvedValue({
      level: 'ulice', id: 666742, name: 'Na Rybníčkách', context: 'Pardubice',
    });
    await resolveSuggestion({
      ...ADDRESS,
      regionalStructure: ADDRESS.regionalStructure!.map((e) => ({ ...e, isoCode: 'CZ' })),
    });
    expect(apiPost).toHaveBeenCalledWith('/maps/resolve', {
      lat: 50.03,
      lng: 15.77,
      type: 'regional.address',
      name: 'Na Rybníčkách 12',
      regional_structure: STRUCTURE,
    });
  });

  it('asks for the door only for the address picker (W3b); Browse never sends the flag', async () => {
    vi.mocked(apiPost).mockResolvedValue({ level: 'adresni_misto', id: 1, name: 'x', context: null });
    await resolveSuggestion(ADDRESS, { addressPoint: true });
    expect(vi.mocked(apiPost).mock.calls[0][1]).toMatchObject({ address_point: true });
    await resolveSuggestion(ADDRESS);
    expect(vi.mocked(apiPost).mock.calls[1][1]).not.toHaveProperty('address_point');
  });

  /* The request is strict — no null coordinates — so such a pick fails like a
   * failed call, and the typeahead says "Couldn't verify this place". */
  it('never sends a pick without a position', async () => {
    await expect(resolveSuggestion({ ...ADDRESS, position: undefined })).rejects.toThrow();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it('returns the server answer as it came', async () => {
    const answer = { level: 'ulice', id: null, name: null, context: null };
    vi.mocked(apiPost).mockResolvedValue(answer);
    expect(await resolveSuggestion(ADDRESS)).toEqual(answer);
  });

  it('lets a failed call throw, so the typeahead can say so', async () => {
    vi.mocked(apiPost).mockRejectedValueOnce(new Error('HTTP 422'));
    await expect(resolveSuggestion(ADDRESS)).rejects.toThrow('HTTP 422');
  });
});

describe('typeBadge', () => {
  it('labels the regional suggestion types', () => {
    expect(typeBadge('regional.address')).toBe('Adresa');
    expect(typeBadge('regional.street')).toBe('Ulice');
    expect(typeBadge('regional.municipality_part')).toBe('Část obce');
    expect(typeBadge('regional.municipality')).toBe('Obec');
    expect(typeBadge('regional.region.district')).toBe('Okres');
    expect(typeBadge('regional.region')).toBe('Kraj');
    expect(typeBadge('regional.country')).toBe('Stát');
  });

  it('has no POI label: the suggest proxy asks Mapy for regional places only', () => {
    expect(typeBadge('poi')).toBe('poi');
  });
});
