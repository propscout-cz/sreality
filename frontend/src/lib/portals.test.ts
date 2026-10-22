/* THE portal chip transition (migration 597) — one rule for Browse, the Watchdog
 * editor and the phone Feed. */

import { describe, expect, it } from 'vitest';

import { DEFAULT_FILTERS } from './filters';
import { advertHost, cyclePortal, NO_PORTALS, portalBadge, portalScopeLabel } from './portals';

describe('cyclePortal', () => {
  it('moves a chip off → on it → not on it → off', () => {
    const on = cyclePortal(NO_PORTALS, 'sreality');
    expect(on).toEqual({ ...NO_PORTALS, portals: ['sreality'] });
    const out = cyclePortal(on, 'sreality');
    expect(out).toEqual({ ...NO_PORTALS, portalsExcluded: ['sreality'] });
    expect(cyclePortal(out, 'sreality')).toEqual(NO_PORTALS);
  });

  it('drops "active ads only" with the last exclusion, and only then', () => {
    const two = { portals: [], portalsExcluded: ['sreality', 'bazos'], portalsExcludedActiveOnly: true };
    const one = cyclePortal(two, 'bazos');
    expect(one).toEqual({ portals: [], portalsExcluded: ['sreality'], portalsExcludedActiveOnly: true });
    expect(cyclePortal(one, 'sreality')).toEqual(NO_PORTALS);
  });

  it('returns only the three portal fields, whatever it is handed', () => {
    expect(Object.keys(cyclePortal(DEFAULT_FILTERS, 'idnes')).sort())
      .toEqual(['portals', 'portalsExcluded', 'portalsExcludedActiveOnly']);
  });
});

describe('portalScopeLabel', () => {
  it('names both sides, and nothing when the filter is off', () => {
    expect(portalScopeLabel([], [], false)).toBeNull();
    expect(portalScopeLabel(['idnes'], ['sreality'], false)).toBe('on iDNES −Sreality');
    expect(portalScopeLabel([], ['sreality'], true)).toBe('−Sreality (active ads)');
    expect(portalScopeLabel(['idnes'], [], true)).toBe('on iDNES');
  });
});

describe('portalBadge', () => {
  it("names a web advert by its own host, without www.", () => {
    expect(portalBadge('web', 'https://www.reality-novak.cz/nabidka/byt-2kk')).toBe('reality-novak.cz');
    expect(portalBadge('web', 'https://byty.example.com/x', 'long')).toBe('byty.example.com');
  });

  it('falls back to the portal name when a web advert has no readable URL', () => {
    expect(portalBadge('web', null)).toBe('web');
    expect(portalBadge('web', 'not a url', 'long')).toBe('Web');
  });

  it("keeps every other portal's name in the surface's register, whatever its URL", () => {
    const url = 'https://www.sreality.cz/detail/prodej/byt/2+kk/praha/123';
    expect(portalBadge('sreality', url)).toBe('Sreality');
    expect(portalBadge('idnes', url)).toBe('iDNES');
    expect(portalBadge('idnes', url, 'long')).toBe('iDNES Reality');
    expect(portalBadge('remax', null, 'long')).toBe('RE/MAX');
  });
});

describe('advertHost', () => {
  it('reads the host and nothing else', () => {
    expect(advertHost('https://user@WWW.Example.cz:8443/a?b=1#c')).toBe('example.cz');
    expect(advertHost(undefined)).toBeNull();
    expect(advertHost('')).toBeNull();
  });
});
