/* The watchdog's kinds as the SPA keeps them: the API's order (api/notifications.WATCHDOG_KINDS), and
 * Relisted only beside the Delisted it answers, so the editor never offers a set the API refuses. */
import { describe, expect, it } from 'vitest';

import { CHANGE_META, DEFAULT_NOTIFY_KINDS, WATCHDOG_KINDS, toggleNotifyKind } from './changeKinds';

describe('toggleNotifyKind', () => {
  it('keeps the API order whatever order the clicks come in', () => {
    expect(toggleNotifyKind(['new_source'], 'new')).toEqual(['new', 'new_source']);
    expect(WATCHDOG_KINDS.map((k) => k.kind)).toEqual(
      ['new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']);
  });

  it('switches Delisted on with Relisted, and Relisted off with Delisted', () => {
    expect(toggleNotifyKind(DEFAULT_NOTIFY_KINDS, 'reactivated')).toEqual(
      ['new', 'price_drop', 'inactive', 'reactivated']);
    expect(toggleNotifyKind(['inactive', 'reactivated'], 'inactive')).toEqual([]);
    expect(toggleNotifyKind(['inactive', 'reactivated'], 'reactivated')).toEqual(['inactive']);
  });

  it('names each kind with the word its alerts carry in the feed', () => {
    expect(WATCHDOG_KINDS.map((k) => CHANGE_META[k.kind].label)).toEqual(
      ['New match', 'Price drop', 'Price rise', 'Delisted', 'Relisted', 'New source']);
  });
});
