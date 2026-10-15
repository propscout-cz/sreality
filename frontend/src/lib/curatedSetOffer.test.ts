import { describe, expect, it } from 'vitest';

import { offerCuratedSet } from './curatedSetOffer';

const cards = [{ property_id: 1 }, { property_id: 2 }];
const known = [
  { id: 7, name: 'Šortlist' },
  { id: 8, name: 'Archiv' },
];

describe('offerCuratedSet', () => {
  it('offers nothing while the member map is unresolved', () => {
    expect(offerCuratedSet(cards, undefined, known, [7])).toEqual({
      options: [],
      missing: [],
      show: false,
    });
  });

  it('offers only the sets with a member on the board', () => {
    const members = new Map([[1, [7]]]);
    const offer = offerCuratedSet(cards, members, known, []);
    expect(offer.options.map((o) => o.id)).toEqual([7]);
    expect(offer.show).toBe(true);
  });

  it('hides the row when one set holds every card and nothing is selected', () => {
    const members = new Map([
      [1, [7]],
      [2, [7]],
    ]);
    expect(offerCuratedSet(cards, members, known, []).show).toBe(false);
    /* Two sets can still partition such a board. */
    const two = new Map([
      [1, [7]],
      [2, [8]],
    ]);
    expect(offerCuratedSet(cards, two, known, []).show).toBe(true);
  });

  it('keeps a selected set offered even when nothing on the board is in it', () => {
    const offer = offerCuratedSet(cards, new Map(), known, [8]);
    expect(offer.options.map((o) => o.id)).toEqual([8]);
    expect(offer.show).toBe(true);
  });

  it('names a selected id no set carries any more', () => {
    const offer = offerCuratedSet(cards, new Map(), known, [99]);
    expect(offer.options).toEqual([]);
    expect(offer.missing).toEqual([99]);
    expect(offer.show).toBe(true);
  });
});
