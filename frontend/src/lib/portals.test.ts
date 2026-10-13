/* THE portal chip transition (migration 597) — one rule for Browse, the Watchdog
 * editor and the phone Feed. */

import { describe, expect, it } from 'vitest';

import { DEFAULT_FILTERS } from './filters';
import { cyclePortal, NO_PORTALS, portalScopeLabel } from './portals';

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
