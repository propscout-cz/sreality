/* A watchdog's and a collection's kinds as the SPA keeps them: the API's order (api/notifications.KIND_ORDER),
 * a watchdog's six and a collection's five (all but New match), each switched on its own. */
import { describe, expect, it } from 'vitest';

import { CHANGE_META, matchReasonLabel } from './changeKinds';
import { COLLECTION_KINDS, DEFAULT_NOTIFY_KINDS, WATCHDOG_KINDS, toggleNotifyKind } from './notifyKinds';

describe('toggleNotifyKind', () => {
  it('keeps the API order whatever order the clicks come in', () => {
    expect(toggleNotifyKind(['new_source'], 'new')).toEqual(['new', 'new_source']);
    expect(WATCHDOG_KINDS.map((k) => k.kind)).toEqual(
      ['new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']);
  });

  it('offers a collection every kind but New match, Back on the market among them', () => {
    expect(COLLECTION_KINDS.map((k) => k.kind)).toEqual(
      ['price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']);
  });

  it('switches Back on the market on its own, with or without Left the market', () => {
    expect(toggleNotifyKind(['new_source'], 'reactivated')).toEqual(['reactivated', 'new_source']);
    expect(toggleNotifyKind(['inactive', 'reactivated'], 'inactive')).toEqual(['reactivated']);
    expect(toggleNotifyKind(['inactive', 'reactivated'], 'reactivated')).toEqual(['inactive']);
    expect(DEFAULT_NOTIFY_KINDS).toEqual(['new', 'price_drop', 'reactivated']);
    expect(toggleNotifyKind(DEFAULT_NOTIFY_KINDS, 'inactive')).toEqual(['new', 'price_drop', 'inactive', 'reactivated']);
  });

  it('names each kind with the word its alerts carry in the feed', () => {
    expect(WATCHDOG_KINDS.map((k) => CHANGE_META[k.kind].label)).toEqual(
      ['New match', 'Price drop', 'Price rise', 'Left the market', 'Back on the market', 'New portal']);
  });
});

describe('matchReasonLabel', () => {
  it('names why a New match entered, a price change by its direction', () => {
    expect(matchReasonLabel({ match_reason: 'back', trigger_price_czk: 1, prev_price_czk: null })).toBe(
      'Back on the market');
    expect(matchReasonLabel({ match_reason: 'price', trigger_price_czk: 8_400_000, prev_price_czk: 9_000_000 }))
      .toBe('Price cut');
    expect(matchReasonLabel({ match_reason: 'price', trigger_price_czk: 21_000, prev_price_czk: 20_000 })).toBe(
      'Price raised');
    expect(matchReasonLabel({ match_reason: 'other', trigger_price_czk: null, prev_price_czk: null })).toBe(
      'Now matches');
  });

  it('reads a New match told before reasons existed as newly listed', () => {
    expect(matchReasonLabel({ match_reason: null, trigger_price_czk: 1, prev_price_czk: null })).toBe(
      'Newly listed');
  });
});
