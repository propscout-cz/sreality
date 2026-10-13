/* Paste-a-link (#1767): the first URL comes out of whatever a share sheet put
 * on the clipboard, and only a portal listing's detail URL resolves. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  READ_PORTALS,
  RECENT_LINKS_KEY,
  RECENT_LINKS_MAX,
  firstUrl,
  readPastedLink,
  readRecentLinks,
  rememberLink,
} from './pastedLink';

const SREALITY = 'https://www.sreality.cz/detail/prodej/byt/2+kk/praha-vysocany-freyova/2861372748';
const BAZOS = 'https://reality.bazos.cz/inzerat/196123456/byt-2kk-praha-9.php';

describe('firstUrl', () => {
  it('pulls the link out of a message that wraps it in words', () => {
    expect(firstUrl(`Koukni na tohle ${SREALITY} — 2+kk u metra, co myslíš?`)).toBe(SREALITY);
  });

  it('takes a bare www. link with no scheme', () => {
    expect(firstUrl('mrkni www.bezrealitky.cz/nemovitosti-byty-domy/912345-nabidka-prodej-bytu dík')).toBe(
      'www.bezrealitky.cz/nemovitosti-byty-domy/912345-nabidka-prodej-bytu',
    );
  });

  it('takes the FIRST of two links', () => {
    expect(firstUrl(`${BAZOS} a taky ${SREALITY}`)).toBe(BAZOS);
    expect(firstUrl(`první www.idnes.cz/x, druhý ${SREALITY}`)).toBe('www.idnes.cz/x');
  });

  it('drops the punctuation a sentence leaves on the end, and an unopened bracket', () => {
    expect(firstUrl(`Tady: ${SREALITY}.`)).toBe(SREALITY);
    expect(firstUrl(`(viz ${BAZOS})`)).toBe(BAZOS);
    expect(firstUrl(`„${SREALITY}“`)).toBe(SREALITY);
  });

  it('takes one dotted token with no scheme as a link', () => {
    expect(firstUrl('reality.bazos.cz/inzerat/196123456/x.php')).toBe('reality.bazos.cz/inzerat/196123456/x.php');
  });

  it('finds nothing in plain words', () => {
    expect(firstUrl('byt 2+kk Vysočany, 54 m2')).toBeNull();
    expect(firstUrl('')).toBeNull();
  });
});

describe('readPastedLink', () => {
  it('resolves a wrapped portal link to its source and native id', () => {
    expect(readPastedLink(`Sdíleno ze Sreality: ${SREALITY}`)).toEqual({
      kind: 'listing',
      url: SREALITY,
      source: 'sreality',
      nativeId: '2861372748',
    });
  });

  it('gives a scheme-less hit a scheme, so the portal link opens', () => {
    const hit = readPastedLink('www.bezrealitky.cz/nemovitosti-byty-domy/912345-nabidka');
    expect(hit).toMatchObject({ kind: 'listing', source: 'bezrealitky', nativeId: '912345' });
    expect(hit.kind === 'listing' && hit.url).toBe('https://www.bezrealitky.cz/nemovitosti-byty-domy/912345-nabidka');
  });

  it('tells a portal page that is no listing from a site we do not read', () => {
    expect(readPastedLink('https://www.sreality.cz/hledani/prodej/byty/praha')).toEqual({
      kind: 'portal-page',
      url: 'https://www.sreality.cz/hledani/prodej/byty/praha',
      source: 'sreality',
    });
    expect(readPastedLink('https://www.ulovdomov.cz/inzerat/123')).toEqual({
      kind: 'other',
      url: 'https://www.ulovdomov.cz/inzerat/123',
    });
    expect(readPastedLink('jen slova')).toEqual({ kind: 'no-url' });
  });

  it('names the nine portals the platform reads', () => {
    expect(READ_PORTALS).toEqual([
      'sreality', 'bazos', 'bezrealitky', 'idnes', 'maxima', 'remax', 'mmreality', 'ceskereality', 'realitymix',
    ]);
  });
});

describe('the recent-links roll', () => {
  beforeEach(() => localStorage.clear());

  const link = (n: number) => ({ url: `https://reality.bazos.cz/inzerat/${n}/x.php`, source: 'bazos', nativeId: String(n) });

  it('keeps the last five, newest first, a repeat moved up rather than doubled', () => {
    for (let n = 1; n <= 6; n++) rememberLink(link(n), new Date(2026, 9, 12, 10, n));
    rememberLink(link(3), new Date(2026, 9, 12, 11, 0));
    const roll = readRecentLinks();
    expect(roll).toHaveLength(RECENT_LINKS_MAX);
    expect(roll.map((r) => r.nativeId)).toEqual(['3', '6', '5', '4', '2']);
  });

  it('reads junk or a throwing storage as an empty roll', () => {
    localStorage.setItem(RECENT_LINKS_KEY, '{not json');
    expect(readRecentLinks()).toEqual([]);
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(readRecentLinks()).toEqual([]);
    get.mockRestore();
  });
});
