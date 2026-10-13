/* The portal-id rules shared by the SPA's paste-a-link and the Chrome extension.
 * One real-shaped detail URL per portal, the paste tolerances, the extension's
 * own entry points, and two drift rails: every `source` is a `listings.source`
 * value, and chrome-extension/manifest.json's checked-in matches name exactly
 * PORTALS' hosts. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { filterById } from './filterRegistry.generated';
import { detailRef, isDetailPage, parsePortalUrl, portalForHost, portalForUrl, PORTALS } from './portalIds';
import { ROUTES } from './routes';

const DETAIL: Array<[string, string, string]> = [
  ['sreality', 'https://www.sreality.cz/detail/prodej/byt/2+kk/praha-vinohrady-slezska/3584327500', '3584327500'],
  ['bazos', 'https://reality.bazos.cz/inzerat/198765432/prodej-bytu-2kk-brno-zabovresky.php', '198765432'],
  ['bezrealitky', 'https://www.bezrealitky.cz/nemovitosti-byty-domy/1024119-nabidka-prodej-bytu-jesenicka-bridlicna', '1024119'],
  ['bezrealitky', 'https://www.bezrealitky.com/properties-flats-houses/1074639-nabidka-prodej-bytu-kollarova-plzen', '1074639'],
  ['idnes', 'https://reality.idnes.cz/detail/prodej/byt/praha-zizkov-konevova/66f2a1b3c4d5e6f708192a3b/', '66f2a1b3c4d5e6f708192a3b'],
  ['maxima', 'https://nemovitosti.maxima.cz/nemovitosti/b50089333/', 'b50089333'],
  ['remax', 'https://www.remax-czech.cz/reality/detail/412345/prodej-bytu-3-1-v-osobnim-vlastnictvi-78-m2-olomouc', '412345'],
  ['mmreality', 'https://www.mmreality.cz/nemovitosti/1234567/', '1234567'],
  ['ceskereality', 'https://www.ceskereality.cz/prodej/byty/byty-2-kk/praha/prodej-bytu-2-kk-330-m2-praha-zizkov-3456789.html', '3456789'],
  ['realitymix', 'https://realitymix.cz/detail/praha/prodej-bytu-2-kk-54-m2-praha-zizkov-7654321.html', '7654321'],
];

describe('parsePortalUrl', () => {
  it.each(DETAIL)('%s: %s', (source, url, nativeId) => {
    expect(parsePortalUrl(url)).toEqual({ source, nativeId });
  });

  it('covers every portal', () => {
    expect(new Set(DETAIL.map(([s]) => s))).toEqual(new Set(PORTALS.map((p) => p.source)));
  });

  it('ignores the query string and the fragment', () => {
    expect(
      parsePortalUrl('https://www.sreality.cz/detail/prodej/byt/2+kk/praha-vinohrady-slezska/3584327500?utm_source=share&foto=2#galerie'),
    ).toEqual({ source: 'sreality', nativeId: '3584327500' });
  });

  it('trims surrounding whitespace', () => {
    expect(parsePortalUrl('  \n https://www.mmreality.cz/nemovitosti/1234567/ \t')).toEqual({
      source: 'mmreality',
      nativeId: '1234567',
    });
  });

  it('assumes https when the scheme is missing', () => {
    expect(parsePortalUrl('reality.idnes.cz/detail/prodej/byt/praha-zizkov-konevova/66f2a1b3c4d5e6f708192a3b/')).toEqual({
      source: 'idnes',
      nativeId: '66f2a1b3c4d5e6f708192a3b',
    });
    expect(parsePortalUrl('http://sreality.cz/detail/pronajem/byt/1+kk/brno-veveri/123456789')).toEqual({
      source: 'sreality',
      nativeId: '123456789',
    });
  });

  it('takes the alt hosts: bare and www, any case, the bezrealitky.com apex', () => {
    expect(parsePortalUrl('https://bezrealitky.com/properties-flats-houses/1074639-nabidka-prodej-bytu-kollarova-plzen')).toEqual({
      source: 'bezrealitky',
      nativeId: '1074639',
    });
    expect(parsePortalUrl('HTTPS://WWW.REMAX-CZECH.CZ/reality/detail/412345/x')).toEqual({ source: 'remax', nativeId: '412345' });
    expect(parsePortalUrl('https://www.realitymix.cz/detail/brno/byt-7654321.html')).toEqual({
      source: 'realitymix',
      nativeId: '7654321',
    });
  });

  it('routes the host, not the path: a maxima id on mmreality is no id', () => {
    expect(parsePortalUrl('https://www.mmreality.cz/nemovitosti/b50089333/')).toBeNull();
  });

  it('is null for anything that is not a portal detail URL', () => {
    for (const text of [
      '',
      '   ',
      'not a url',
      'https://www.example.com/detail/prodej/byt/2+kk/praha/3584327500',
      'https://www.sreality.cz/hledani/prodej/byty/praha',
      'https://reality.idnes.cz/s/prodej/byty/praha/',
      '/detail/prodej/byt/2+kk/praha/3584327500',
      'javascript:alert(1)',
    ]) {
      expect(parsePortalUrl(text), text).toBeNull();
    }
  });

  it('lands on the advert route the SPA already serves', () => {
    const ref = parsePortalUrl(DETAIL[0][1]);
    expect(ref && ROUTES.listingCanonical.build(ref)).toBe('/listing/sreality/3584327500');
  });
});

describe('the extension entry points', () => {
  it('detailRef resolves a relative card href against the given host', () => {
    expect(detailRef('/inzerat/198765432/prodej-bytu.php', 'reality.bazos.cz')).toEqual({
      source: 'bazos',
      sourceId: '198765432',
    });
    expect(detailRef('/inzerat/198765432/prodej-bytu.php', 'www.example.com')).toBeNull();
  });

  it('portalForHost / portalForUrl / isDetailPage', () => {
    expect(portalForHost('WWW.SREALITY.CZ')?.source).toBe('sreality');
    expect(portalForHost('www.example.com')).toBeNull();
    expect(portalForUrl('https://www.ceskereality.cz/prodej/byty/')?.source).toBe('ceskereality');
    expect(isDetailPage(DETAIL[1][1])).toBe(true);
    expect(isDetailPage('https://reality.bazos.cz/prodam/byt/')).toBe(false);
  });

  it('saleApartmentHint reads the category from the path', () => {
    const hint = portalForHost('www.sreality.cz')?.saleApartmentHint;
    expect(hint?.('/detail/prodej/byt/2+kk/praha/3584327500')).toBe(true);
    expect(hint?.('/detail/pronajem/byt/2+kk/praha/3584327500')).toBe(false);
    expect(hint?.('/detail/prodej/dum/rodinny/praha/3584327500')).toBe(false);
    expect(portalForHost('realitymix.cz')?.saleApartmentHint).toBeUndefined();
  });
});

describe('drift rails', () => {
  it('every source is a listings.source value (the registry portals enum)', () => {
    const known = new Set((filterById('portals')?.enum_values ?? []).map((o) => o.value));
    expect(PORTALS.map((p) => p.source).filter((s) => !known.has(s))).toEqual([]);
  });

  it('no host belongs to two portals', () => {
    const hosts = PORTALS.flatMap((p) => p.hosts.map((h) => h.toLowerCase()));
    expect(hosts.length).toBe(new Set(hosts).size);
  });

  /* The extension's build overwrites content_scripts.matches from PORTALS, so the
   * checked-in list is a template; this keeps it honest. vitest runs from
   * frontend/, inside the full repo checkout (CI and local alike). */
  it('chrome-extension/manifest.json matches exactly the PORTALS hosts', () => {
    const manifest = JSON.parse(readFileSync(join(process.cwd(), '..', 'chrome-extension', 'manifest.json'), 'utf8'));
    const matches: string[] = manifest.content_scripts[0].matches;
    const fromRules = PORTALS.flatMap((p) => p.hosts.map((h) => `https://${h}/*`));
    expect([...matches].sort()).toEqual([...fromRules].sort());
  });
});
