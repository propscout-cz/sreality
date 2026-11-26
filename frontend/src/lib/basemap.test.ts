/* The basemap picks on a map with a basemap of its own (lib/basemap): ČÚZK's terrain sits
 * under the roads, the cadastral map under the labels, the photo over the whole basemap,
 * and the terrain and the cadastre ride over the photo while it shows. Then the one popup
 * a click on reference data opens. The fake map draws nothing; it keeps the layers' order
 * and layout. The popup is a fake too, and ČÚZK a stubbed `fetch`. */

import type { Map as MaplibreMap } from 'maplibre-gl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FakeMap } from '@/test/fakeMap';

const { FakePopup, popups } = vi.hoisted(() => {
  const popups: Array<InstanceType<typeof FakePopup>> = [];
  class FakePopup {
    html = '';
    private readonly closers: Array<() => void> = [];
    constructor(readonly options: Record<string, unknown>) {
      popups.push(this);
    }
    setLngLat(): this {
      return this;
    }
    setHTML(html: string): this {
      this.html = html;
      return this;
    }
    addTo(): this {
      return this;
    }
    on(type: string, fn: () => void): this {
      if (type === 'close') this.closers.push(fn);
      return this;
    }
    remove(): this {
      this.closers.forEach((fn) => fn());
      return this;
    }
  }
  return { FakePopup, popups };
});

vi.mock('maplibre-gl', async (importOriginal) => {
  const real = await importOriginal<{ default: Record<string, unknown> }>();
  return { ...real, default: { ...real.default, Popup: FakePopup } };
});

import { addBasemapLayers, openReferencePopup, setBasemapPicks } from './basemap';

/* A basemap in miniature: land, a road, a label; then a layer of ours. */
function mapWithBasemap(): { fake: FakeMap; map: MaplibreMap } {
  const fake = new FakeMap(document.createElement('div'), {});
  fake.addLayer({ id: 'land', type: 'fill' });
  fake.addLayer({ id: 'road', type: 'line', 'source-layer': 'transportation' });
  fake.addLayer({ id: 'label', type: 'symbol' });
  const map = fake as unknown as MaplibreMap;
  addBasemapLayers(map);
  fake.addLayer({ id: 'pin', type: 'circle' });
  return { fake, map };
}

const NONE = { aerial: false, relief: false, contours: false, cadastre: false };
const ALL = { aerial: true, relief: true, contours: true, cadastre: true };
const ON_MAP = ['land', 'relief', 'contours', 'road', 'cadastre', 'cadastre-photo', 'label', 'aerial', 'pin'];
const ON_PHOTO = ['land', 'road', 'label', 'aerial', 'relief', 'contours', 'cadastre', 'cadastre-photo', 'pin'];

const visible = (fake: FakeMap, id: string): boolean =>
  (fake.layout.get(id) as { visibility?: string } | undefined)?.visibility === 'visible';

describe('addBasemapLayers', () => {
  it('lays the terrain under the roads, the cadastre under the labels and the photo over the whole basemap, all hidden', () => {
    const { fake } = mapWithBasemap();
    expect(fake.getLayersOrder()).toEqual(ON_MAP);
    for (const id of ['relief', 'contours', 'cadastre', 'cadastre-photo', 'aerial']) {
      expect(fake.layout.get(id)).toEqual({ visibility: 'none' });
    }
  });
});

describe('setBasemapPicks', () => {
  it('shows only what is picked', () => {
    const { fake, map } = mapWithBasemap();
    setBasemapPicks(map, { ...NONE, contours: true });
    expect(visible(fake, 'contours')).toBe(true);
    for (const id of ['relief', 'aerial', 'cadastre', 'cadastre-photo']) expect(visible(fake, id)).toBe(false);
  });

  it('lifts the terrain and the cadastre over the photo while it shows, under our layers, and back down', () => {
    const { fake, map } = mapWithBasemap();
    setBasemapPicks(map, ALL);
    expect(fake.getLayersOrder()).toEqual(ON_PHOTO);
    setBasemapPicks(map, { ...ALL, aerial: false });
    expect(fake.getLayersOrder()).toEqual(ON_MAP);
  });

  it("draws ČÚZK's black parcel lines on the map and its yellow ones on the photo", () => {
    const { fake, map } = mapWithBasemap();
    setBasemapPicks(map, { ...NONE, cadastre: true });
    expect([visible(fake, 'cadastre'), visible(fake, 'cadastre-photo')]).toEqual([true, false]);
    setBasemapPicks(map, { ...NONE, cadastre: true, aerial: true });
    expect([visible(fake, 'cadastre'), visible(fake, 'cadastre-photo')]).toEqual([false, true]);
    setBasemapPicks(map, { ...NONE, aerial: true });
    expect([visible(fake, 'cadastre'), visible(fake, 'cadastre-photo')]).toEqual([false, false]);
  });

  it('does nothing on a map without the basemap picks', () => {
    const fake = new FakeMap(document.createElement('div'), {});
    fake.addLayer({ id: 'pin', type: 'circle' });
    setBasemapPicks(fake as unknown as MaplibreMap, ALL);
    expect(fake.getLayersOrder()).toEqual(['pin']);
  });
});

describe('openReferencePopup', () => {
  const mapAt = (zoom: number) => new FakeMap(document.createElement('div'), { zoom }) as unknown as MaplibreMap;
  const AT = { lng: 14.4207, lat: 50.087 } as Parameters<typeof openReferencePopup>[1];
  const AREA = '<div class="lp">Q100</div>';
  /* ČÚZK's parcel query (RÚIAN layer 5) finds parcel 1/1; the k.ú. query (7) nothing. */
  const parcelFound = () =>
    vi.fn(async (url: string) => {
      const features = url.includes('/MapServer/5/')
        ? [{ attributes: { id: 2098760101, cisloparcely: '1/1', druhcislovanikod: 2 } }]
        : [];
      return new Response(JSON.stringify({ features }));
    });

  beforeEach(() => {
    vi.stubGlobal('fetch', parcelFound());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    popups.length = 0;
  });

  it('opens nothing when neither an area nor a parcel answers', () => {
    expect(openReferencePopup(mapAt(18), AT, null, 'aerial')).toBeNull();
    expect(openReferencePopup(mapAt(16), AT, null, 'cadastre')).toBeNull();
    expect(popups).toHaveLength(0);
  });

  it("opens an area's card alone without the cadastre, and asks ČÚZK nothing", () => {
    expect(openReferencePopup(mapAt(18), AT, AREA, 'flood')).not.toBeNull();
    expect(popups[0].html).toBe(AREA);
    expect(popups[0].options).toMatchObject({ className: 'listing-popup overlay-popup' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("puts the parcel under the area's card, filled in when ČÚZK answers", async () => {
    openReferencePopup(mapAt(18), AT, AREA, 'cadastre,flood');
    const [popup] = popups;
    expect(popup.html.startsWith(AREA)).toBe(true);
    expect(popup.html).toContain('Hledám parcelu…');
    await vi.waitFor(() => expect(popup.html).toContain('Parcela 1/1'));
    expect(popup.html.startsWith(AREA)).toBe(true);
    expect(popup.html).not.toContain('Hledám parcelu…');
  });

  it('says so when ČÚZK fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))));
    openReferencePopup(mapAt(18), AT, null, 'cadastre');
    await vi.waitFor(() => expect(popups[0].html).toContain('Katastr teď neodpovídá'));
  });

  it('drops the question when the popup closes first', async () => {
    const pending: Array<() => void> = [];
    const answer = parcelFound();
    const fetch = vi.fn(
      (url: string, init?: RequestInit) =>
        new Promise<Response>((resolve) => {
          void init;
          pending.push(() => void answer(url).then(resolve));
        }),
    );
    vi.stubGlobal('fetch', fetch);
    openReferencePopup(mapAt(18), AT, null, 'cadastre');
    const [popup] = popups;
    popup.remove();
    expect(fetch.mock.calls.map(([, init]) => init?.signal?.aborted)).toEqual([true, true]);
    pending.forEach((resolve) => resolve());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(popup.html).toContain('Hledám parcelu…');
  });
});
