/* The watchdog's kinds as the SPA keeps them: the API's order (api/notifications.WATCHDOG_KINDS), and
 * Back on the market only beside the Left the market it answers, so the editor never offers a set the API refuses. */
import { describe, expect, it } from 'vitest';

import { CHANGE_META } from './changeKinds';
import { DEFAULT_NOTIFY_KINDS, WATCHDOG_KINDS, toggleNotifyKind } from './notifyKinds';

describe('toggleNotifyKind', () => {
  it('keeps the API order whatever order the clicks come in', () => {
    expect(toggleNotifyKind(['new_source'], 'new')).toEqual(['new', 'new_source']);
    expect(WATCHDOG_KINDS.map((k) => k.kind)).toEqual(
      ['new', 'price_drop', 'price_rise', 'inactive', 'reactivated', 'new_source']);
  });

  it('switches Left the market on with Back on the market, and Back on the market off with Left the market', () => {
    expect(toggleNotifyKind(DEFAULT_NOTIFY_KINDS, 'reactivated')).toEqual(
      ['new', 'price_drop', 'inactive', 'reactivated']);
    expect(toggleNotifyKind(['inactive', 'reactivated'], 'inactive')).toEqual([]);
    expect(toggleNotifyKind(['inactive', 'reactivated'], 'reactivated')).toEqual(['inactive']);
  });

  it('names each kind with the word its alerts carry in the feed', () => {
    expect(WATCHDOG_KINDS.map((k) => CHANGE_META[k.kind].label)).toEqual(
      ['New match', 'Price drop', 'Price rise', 'Left the market', 'Back on the market', 'New portal']);
  });
});
