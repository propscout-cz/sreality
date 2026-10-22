/* The portal-id rules shared by the SPA's paste-a-link and the Chrome extension.
 * One real-shaped detail URL per portal, the paste tolerances, the extension's
 * own entry points, and two drift rails: every `source` is a `listings.source`
 * value, and chrome-extension/manifest.json's checked-in matches name exactly
 * PORTALS' hosts. */
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
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

/* The parity arm: the SAME probe rows tests/scraper/test_portal_identity.py runs
 * against the Python canon (scraper/portal_identity.py), so the walk's identity and
 * the browser's can only drift with a red suite. Both read the same exact host lists
 * (pytest compares them). Where the browser stops short by design, the row must
 * answer null here: a host these rules do not list (the Facebook / Google wrappers,
 * an unknown site) is the server's call — POST /listings/ingest reads the pasted
 * text itself. The 512-character cap is resolve_url's, not the id rule's. Read
 * unconditionally, like imageUrl.test.ts: a moved fixture reds the suite. */
interface UrlProbe {
  input: string;
  source: string | null;
  native: string | null;
  note: string;
}
const PROBES = JSON.parse(
  readFileSync(resolve(process.cwd(), '../tests/fixtures/portal_url_probes.json'), 'utf8'),
) as UrlProbe[];

describe('the shared probe fixture (tests/fixtures/portal_url_probes.json)', () => {
  const relative = PROBES.filter((p) => p.input.startsWith('/'));
  const absolute = PROBES.filter((p) => !p.input.startsWith('/'));
  const known = absolute.filter((p) => portalForUrl(p.input) != null);

  it.each(relative.map((p) => [p.note, p] as const))('index href: %s', (_note, p) => {
    const portal = PORTALS.find((x) => x.source === p.source);
    expect(portal, `unknown source ${p.source}`).toBeDefined();
    expect(detailRef(p.input, portal!.hosts[0])).toEqual(
      p.native == null ? null : { source: p.source, sourceId: p.native },
    );
  });

  it.each(known.map((p) => [p.note, p] as const))('portal URL: %s', (_note, p) => {
    expect(portalForUrl(p.input)?.source).toBe(p.source);
    expect(parsePortalUrl(p.input)).toEqual(p.native == null ? null : { source: p.source, nativeId: p.native });
  });

  it('leaves every other host to the server', () => {
    for (const p of absolute.filter((x) => portalForUrl(x.input) == null)) {
      expect(parsePortalUrl(p.input), p.note).toBeNull();
    }
  });

  it('refuses a pasted path the fetch would rewrite, as the canon does', () => {
    for (const url of [
      'https://reality.bazos.cz/inzerat/1\\..\\..\\inzerat/2/x.php',
      'reality.bazos.cz/inzerat/1/../../inzerat/2/x.php',
      'https://www.mmreality.cz/nemovitosti/1/%2E%2E/2/',
    ]) {
      expect(parsePortalUrl(url), url).toBeNull();
    }
    expect(parsePortalUrl('https://reality.bazos.cz/inzerat/1/a..b.php')).toEqual({ source: 'bazos', nativeId: '1' });
  });

  it('compares a detail URL and an index href of every portal it can', () => {
    const covered = new Set(known.filter((p) => p.native != null).map((p) => p.source));
    expect([...covered].sort()).toEqual(PORTALS.map((p) => p.source).sort());
    expect(relative.length).toBeGreaterThan(0);
  });
});
