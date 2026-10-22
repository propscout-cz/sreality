/* The board card's broker tooltip. */

import { describe, expect, it } from 'vitest';
import { brokerHoverTitle } from './BoardCard';
import type { ListingBroker } from '@/lib/brokers';

const broker = (over: Partial<ListingBroker>): ListingBroker => ({
  sreality_id: null,
  listing_id: 111,
  broker_id: 7,
  broker_display_name: 'Jan Novák',
  broker_firm_label: 'RE/MAX',
  primary_email: null,
  primary_phone: null,
  ...over,
});

describe('brokerHoverTitle', () => {
  it('lists the contact', () => {
    expect(
      brokerHoverTitle(
        broker({ primary_phone: '+420 777 123 456', primary_email: 'jan@remax.cz' }),
      ),
    ).toBe('Jan Novák · RE/MAX · +420 777 123 456 · jan@remax.cz');
  });

  it('stays silent about contact when the broker has none', () => {
    expect(brokerHoverTitle(broker({}))).toBe(
      'Jan Novák · RE/MAX',
    );
  });

  it('falls back to the link label when nothing is known', () => {
    expect(
      brokerHoverTitle(broker({ broker_display_name: null, broker_firm_label: null })),
    ).toBe('Zobrazit makléře');
  });

  /* MS7: with no active ad the board shows the inactive ads' brokers, and says so. */
  it('says the broker comes from inactive ads', () => {
    expect(brokerHoverTitle(broker({}), true)).toBe(
      'Jan Novák · RE/MAX · z neaktivních inzerátů',
    );
  });
});
