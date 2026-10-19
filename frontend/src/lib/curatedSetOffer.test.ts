import { describe, expect, it } from 'vitest';

import { offerCuratedSet } from './curatedSetOffer';
import type { SetScope } from './setScope';

const cards = [{ property_id: 1 }, { property_id: 2 }];
const IN = (ids: number[]): SetScope => ({ set: null, in: ids, out: [] });
const known = [
  { id: 7, name: 'Šortlist' },
  { id: 8, name: 'Archiv' },
];

describe('offerCuratedSet', () => {
  it('offers nothing while the member map is unresolved', () => {
    expect(offerCuratedSet(cards, undefined, known, IN([7]))).toEqual({
      options: [],
      show: false,
    });
  });

  it('offers only the sets with a member on the board', () => {
    const members = new Map([[1, [7]]]);
    const offer = offerCuratedSet(cards, members, known, null);
    expect(offer.options.map((o) => o.id)).toEqual([7]);
    expect(offer.show).toBe(true);
  });

  it('hides the row when one set holds every card and nothing is selected', () => {
    const members = new Map([
      [1, [7]],
      [2, [7]],
    ]);
    expect(offerCuratedSet(cards, members, known, null).show).toBe(false);
    /* Two sets can still partition such a board. */
    const two = new Map([
      [1, [7]],
      [2, [8]],
    ]);
    expect(offerCuratedSet(cards, two, known, null).show).toBe(true);
  });

  it('keeps a selected set offered even when nothing on the board is in it', () => {
    const offer = offerCuratedSet(cards, new Map(), known, IN([8]));
    expect(offer.options.map((o) => o.id)).toEqual([8]);
    expect(offer.show).toBe(true);
  });

  it('keeps the row for a selected id no set carries any more (the control draws its #id)', () => {
    const offer = offerCuratedSet(cards, new Map(), known, { set: null, in: [], out: [99] });
    expect(offer.options).toEqual([]);
    expect(offer.show).toBe(true);
  });

  it('keeps the row while the whole set is excluded, a unit or none selected', () => {
    const members = new Map([
      [1, [7]],
      [2, [7]],
    ]);
    expect(offerCuratedSet(cards, members, known, { set: 'out', in: [], out: [] }).show).toBe(true);
  });
});
