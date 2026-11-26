/* The cadastral map's click (lib/cadastre): ČÚZK's RÚIAN parcel row read into the popup's
 * words, the two point queries, and when a click asks at all. ČÚZK is a stubbed `fetch`;
 * the popup that carries the card is lib/basemap's (basemap.test.ts). */

import type { Map as MaplibreMap } from 'maplibre-gl';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { FakeMap } from '@/test/fakeMap';

import {
  asksParcel,
  fetchParcelAt,
  lookUpParcel,
  nahlizeniUrl,
  parcelFromAttributes,
  parcelPopupHtml,
  type Parcel,
} from './cadastre';

const OLD_TOWN_SQUARE = {
  id: 2098760101,
  cisloparcely: '1/1',
  kmenovecislo: 1,
  poddelenicisla: 1,
  druhcislovanikod: 2,
  vymeraparcely: 1511,
  druhpozemkukod: 13,
  zpusobyvyuzitipozemku: null,
  katastralniuzemi: 727024,
};
const STARE_MESTO = { kod: 727024, nazev: 'Staré Město' };

/* ČÚZK's answer to a point query on `layer`, by the layer the URL names. */
function ruian(byLayer: Record<number, unknown>) {
  return vi.fn(async (url: string) => {
    const layer = Number(/MapServer\/(\d+)\/query/.exec(url)?.[1]);
    const body = byLayer[layer];
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body ?? { features: [] }), { status: 200 });
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('parcelFromAttributes', () => {
  it('reads a parcel and its katastrální území', () => {
    expect(parcelFromAttributes(OLD_TOWN_SQUARE, STARE_MESTO)).toEqual<Parcel>({
      id: '2098760101',
      number: '1/1',
      area: 1511,
      landType: 'zastavěná plocha a nádvoří',
      landUse: null,
      ku: 'Staré Město',
    });
  });

  it('writes a building plot as "st." where the k.ú. numbers them apart', () => {
    const plot = parcelFromAttributes({ ...OLD_TOWN_SQUARE, cisloparcely: '64', druhcislovanikod: 1 }, null);
    expect(plot?.number).toBe('st. 64');
  });

  it('composes the number from its stem and part when the written one is missing', () => {
    const parcel = parcelFromAttributes({ ...OLD_TOWN_SQUARE, cisloparcely: null, kmenovecislo: 412, poddelenicisla: 2 }, null);
    expect(parcel?.number).toBe('412/2');
    const whole = parcelFromAttributes({ ...OLD_TOWN_SQUARE, cisloparcely: '', kmenovecislo: 534, poddelenicisla: null }, null);
    expect(whole?.number).toBe('534');
  });

  it("names the land's use, and leaves out a code it does not know", () => {
    const pond = parcelFromAttributes({ ...OLD_TOWN_SQUARE, druhpozemkukod: 11, zpusobyvyuzitipozemku: 6 }, null);
    expect([pond?.landType, pond?.landUse]).toEqual(['vodní plocha', 'rybník']);
    const odd = parcelFromAttributes({ ...OLD_TOWN_SQUARE, druhpozemkukod: 99, zpusobyvyuzitipozemku: 99 }, null);
    expect([odd?.landType, odd?.landUse]).toEqual([null, null]);
  });

  it("names the k.ú. only when it is the parcel's own", () => {
    expect(parcelFromAttributes(OLD_TOWN_SQUARE, { kod: 727181, nazev: 'Nové Město' })?.ku).toBeNull();
  });

  it('is null without an id or a number', () => {
    expect(parcelFromAttributes({ ...OLD_TOWN_SQUARE, id: null }, null)).toBeNull();
    expect(parcelFromAttributes({ ...OLD_TOWN_SQUARE, cisloparcely: null, kmenovecislo: null }, null)).toBeNull();
  });
});

describe('parcelPopupHtml', () => {
  const parcel = parcelFromAttributes(OLD_TOWN_SQUARE, STARE_MESTO) as Parcel;

  it('names the parcel, its k.ú., area and land, and links its page in Nahlížení do KN', () => {
    const html = parcelPopupHtml({ state: 'found', parcel });
    expect(html).toContain('<p class="lp-kicker">Katastrální mapa</p>');
    expect(html).toContain('Parcela 1/1');
    expect(html).toMatch(/<p class="lp-meta">k\.ú\. Staré Město · 1\s511 m²<\/p>/);
    expect(html).toContain('<p class="lp-district">zastavěná plocha a nádvoří</p>');
    expect(html).toContain(
      'href="https://nahlizenidokn.cuzk.gov.cz/ZobrazObjekt.aspx?typ=parcela&amp;id=2098760101" target="_blank"',
    );
    expect(nahlizeniUrl('2098760101')).toBe(
      'https://nahlizenidokn.cuzk.gov.cz/ZobrazObjekt.aspx?typ=parcela&id=2098760101',
    );
  });

  it("escapes ČÚZK's words", () => {
    const html = parcelPopupHtml({ state: 'found', parcel: { ...parcel, ku: '<b>X</b>' } });
    expect(html).toContain('k.ú. &lt;b&gt;X&lt;/b&gt;');
    expect(html).not.toContain('<b>X</b>');
  });

  it('says what it is doing until ČÚZK answers, and when it does not', () => {
    expect(parcelPopupHtml({ state: 'loading' })).toContain('Hledám parcelu…');
    expect(parcelPopupHtml({ state: 'none' })).toContain('Tady katastr žádnou parcelu nevede.');
    expect(parcelPopupHtml({ state: 'failed' })).toContain('Katastr teď neodpovídá. Klikněte znovu.');
  });
});

describe('fetchParcelAt', () => {
  it('asks for the parcel and its k.ú. by the same point, in WGS84', async () => {
    const fetch = ruian({ 5: { features: [{ attributes: OLD_TOWN_SQUARE }] }, 7: { features: [{ attributes: STARE_MESTO }] } });
    vi.stubGlobal('fetch', fetch);
    const parcel = await fetchParcelAt({ lng: 14.4207, lat: 50.087 }, new AbortController().signal);
    expect(parcel?.ku).toBe('Staré Město');
    const urls = fetch.mock.calls.map(([url]) => new URL(url));
    expect(urls.map((u) => u.pathname.split('/').slice(-2, -1)[0]).sort()).toEqual(['5', '7']);
    for (const u of urls) {
      expect(u.origin).toBe('https://ags.cuzk.gov.cz');
      expect(u.searchParams.get('geometry')).toBe('14.4207,50.087');
      expect(u.searchParams.get('inSR')).toBe('4326');
      expect(u.searchParams.get('returnGeometry')).toBe('false');
    }
  });

  it('is null where ČÚZK has no parcel', async () => {
    vi.stubGlobal('fetch', ruian({}));
    expect(await fetchParcelAt({ lng: 13, lat: 47.5 }, new AbortController().signal)).toBeNull();
  });

  it("fails on ČÚZK's error body and on an HTTP error", async () => {
    vi.stubGlobal('fetch', ruian({ 5: { error: { code: 400, message: 'Invalid query' } } }));
    await expect(fetchParcelAt({ lng: 14, lat: 50 }, new AbortController().signal)).rejects.toThrow('Invalid query');
    vi.stubGlobal('fetch', ruian({ 7: new Response('', { status: 503 }) }));
    await expect(fetchParcelAt({ lng: 14, lat: 50 }, new AbortController().signal)).rejects.toThrow('503');
  });
});

const mapAt = (zoom: number) => new FakeMap(document.createElement('div'), { zoom }) as unknown as MaplibreMap;

describe('asksParcel', () => {
  it('asks while the cadastral map is picked and drawn', () => {
    expect(asksParcel(mapAt(17), 'cadastre')).toBe(true);
    expect(asksParcel(mapAt(16.9), 'cadastre')).toBe(false);
    expect(asksParcel(mapAt(19), 'aerial,flood')).toBe(false);
  });
});

describe('lookUpParcel', () => {
  const at = { lng: 14.4207, lat: 50.087 };

  it('answers with the parcel, with none, or that ČÚZK failed, and never rejects', async () => {
    vi.stubGlobal('fetch', ruian({ 5: { features: [{ attributes: OLD_TOWN_SQUARE }] } }));
    expect(await lookUpParcel(at, new AbortController().signal)).toMatchObject({
      state: 'found',
      parcel: { number: '1/1', ku: null },
    });
    vi.stubGlobal('fetch', ruian({}));
    expect(await lookUpParcel(at, new AbortController().signal)).toEqual({ state: 'none' });
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))));
    expect(await lookUpParcel(at, new AbortController().signal)).toEqual({ state: 'failed' });
  });
});
