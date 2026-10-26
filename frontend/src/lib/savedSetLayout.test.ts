/* The saved-set arrangement (migration 672): which box a chip sits in, how a drag
 * crosses boxes, and the one write a drop sends. */

import { describe, expect, it } from 'vitest';

import {
  LOOSE, applyLayout, containerOf, containersOf, layoutOf, moveAcross, moveTo, moveWithin, sameLayout,
} from './savedSetLayout';

const ITEMS = [
  { id: 'a', group_id: null },
  { id: 'b', group_id: 'g1' },
  { id: 'c', group_id: null },
  { id: 'd', group_id: 'g1' },
  { id: 'e', group_id: 'gone' },
];

describe('containersOf', () => {
  it('puts each item in its box in list order, a box this list lacks reading loose', () => {
    expect(containersOf(ITEMS, ['g1', 'g2'])).toEqual({ [LOOSE]: ['a', 'c', 'e'], g1: ['b', 'd'], g2: [] });
  });

  it('is one loose row without groups', () => {
    expect(containersOf(ITEMS, [])).toEqual({ [LOOSE]: ['a', 'b', 'c', 'd', 'e'] });
  });
});

describe('a drag', () => {
  const c = containersOf(ITEMS, ['g1', 'g2']);

  it('names a container by itself or by an item in it', () => {
    expect(containerOf(c, 'g2')).toBe('g2');
    expect(containerOf(c, 'd')).toBe('g1');
    expect(containerOf(c, 'nope')).toBeUndefined();
  });

  it('lands at the hovered chip when it crosses into a box', () => {
    expect(moveAcross(c, 'a', 'd')).toEqual({ [LOOSE]: ['c', 'e'], g1: ['b', 'a', 'd'], g2: [] });
  });

  it('lands at the end of a box hovered as a whole (an empty one)', () => {
    expect(moveAcross(c, 'b', 'g2')).toEqual({ [LOOSE]: ['a', 'c', 'e'], g1: ['d'], g2: ['b'] });
  });

  it('comes back out to the loose row', () => {
    expect(moveAcross(c, 'd', LOOSE)).toEqual({ [LOOSE]: ['a', 'c', 'e', 'd'], g1: ['b'], g2: [] });
  });

  it('does nothing inside one container or for a dragged box', () => {
    expect(moveAcross(c, 'a', 'c')).toBeNull();
    expect(moveAcross(c, 'g1', 'g2')).toBeNull();
  });

  it('reorders inside one container on the drop', () => {
    expect(moveWithin(c, 'a', 'e')[LOOSE]).toEqual(['c', 'e', 'a']);
    expect(moveWithin(c, 'a', 'd')).toBe(c);
    expect(moveWithin(c, 'a', LOOSE)).toBe(c);
  });

  it('moves by menu to the end of a box, or out', () => {
    expect(moveTo(c, 'c', 'g2').g2).toEqual(['c']);
    expect(moveTo(c, 'b', LOOSE)[LOOSE]).toEqual(['a', 'c', 'e', 'b']);
    expect(moveTo(c, 'b', 'g1')).toBe(c);
  });
});

describe('the write', () => {
  it('lists the loose row first, then each box in box order', () => {
    const c = { [LOOSE]: ['c'], g1: ['b', 'a'], g2: ['d'] };
    expect(layoutOf(c, ['g2', 'g1'])).toEqual({
      groups: ['g2', 'g1'],
      items: [
        { id: 'c', group_id: null },
        { id: 'd', group_id: 'g2' },
        { id: 'b', group_id: 'g1' },
        { id: 'a', group_id: 'g1' },
      ],
    });
  });

  it('is unchanged when nothing moved', () => {
    const c = containersOf(ITEMS, ['g1']);
    expect(sameLayout(layoutOf(c, ['g1']), layoutOf(containersOf(ITEMS, ['g1']), ['g1']))).toBe(true);
    expect(sameLayout(layoutOf(c, ['g1']), layoutOf(moveTo(c, 'a', 'g1'), ['g1']))).toBe(false);
  });

  it('patches the cache in the new order, unlisted (shared) items after, boxes reordered', () => {
    const prev = {
      data: [
        { id: 'a', group_id: null, name: 'A' },
        { id: 's', group_id: null, name: 'Shared' },
        { id: 'b', group_id: 'g1', name: 'B' },
      ],
      total: 3,
      groups: [{ id: 'g1' }, { id: 'g2' }],
    };
    const next = applyLayout(prev, {
      groups: ['g2', 'g1'],
      items: [{ id: 'b', group_id: null }, { id: 'a', group_id: 'g2' }],
    });
    expect(next.data.map((i) => [i.id, i.group_id])).toEqual([['b', null], ['a', 'g2'], ['s', null]]);
    expect(next.data[0].name).toBe('B');
    expect(next.groups?.map((g) => g.id)).toEqual(['g2', 'g1']);
    expect(next.total).toBe(3);
  });
});
