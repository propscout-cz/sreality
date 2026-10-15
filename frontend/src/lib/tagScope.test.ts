import { describe, expect, it } from 'vitest';

import { cycleTag, matchesTags, NO_TAG_CHOICE, propertyIdsForTags, tagChoiceActive } from './tagScope';

const members = new Map<number, number[]>([
  [1, [10, 11]],
  [2, [10]],
  [3, [12]],
]);

describe('cycleTag', () => {
  it('moves a tag off → on → not → off', () => {
    const on = cycleTag(NO_TAG_CHOICE, 10);
    expect(on).toEqual({ tags: [10], tagsExcluded: [] });
    const not = cycleTag(on, 10);
    expect(not).toEqual({ tags: [], tagsExcluded: [10] });
    expect(cycleTag(not, 10)).toEqual(NO_TAG_CHOICE);
  });

  it('leaves the other tags where they are', () => {
    const c = cycleTag({ tags: [10], tagsExcluded: [11] }, 12);
    expect(c).toEqual({ tags: [10, 12], tagsExcluded: [11] });
  });
});

describe('matchesTags', () => {
  it('is no constraint when nothing is chosen', () => {
    expect(matchesTags(undefined, NO_TAG_CHOICE)).toBe(true);
    expect(tagChoiceActive(NO_TAG_CHOICE)).toBe(false);
  });

  it('needs EVERY included tag', () => {
    const c = { tags: [10, 11], tagsExcluded: [] };
    expect(matchesTags(members.get(1), c)).toBe(true);
    expect(matchesTags(members.get(2), c)).toBe(false);
    expect(matchesTags(undefined, c)).toBe(false);
  });

  it('refuses ANY excluded tag, and an untagged property passes', () => {
    const c = { tags: [], tagsExcluded: [11, 12] };
    expect(matchesTags(members.get(1), c)).toBe(false);
    expect(matchesTags(members.get(2), c)).toBe(true);
    expect(matchesTags(undefined, c)).toBe(true);
  });
});

describe('propertyIdsForTags', () => {
  it('is null on both sides when nothing is chosen', () => {
    expect(propertyIdsForTags(members, NO_TAG_CHOICE)).toEqual({ allow: null, deny: null });
  });

  it('allows the properties carrying every included tag, AND across the selection', () => {
    expect(propertyIdsForTags(members, { tags: [10], tagsExcluded: [] }).allow).toEqual([1, 2]);
    expect(propertyIdsForTags(members, { tags: [10, 11], tagsExcluded: [] }).allow).toEqual([1]);
    /* `?tags=10,10` must not raise the bar to two. */
    expect(propertyIdsForTags(members, { tags: [10, 10], tagsExcluded: [] }).allow).toEqual([1, 2]);
  });

  it('denies the properties carrying any excluded tag', () => {
    const { allow, deny } = propertyIdsForTags(members, { tags: [], tagsExcluded: [11, 12] });
    expect(allow).toBeNull();
    expect(deny).toEqual([1, 3]);
  });

  it('resolves to [] rather than null when a chosen tag has no property', () => {
    expect(propertyIdsForTags(members, { tags: [99], tagsExcluded: [] }).allow).toEqual([]);
    expect(propertyIdsForTags(members, { tags: [], tagsExcluded: [99] }).deny).toEqual([]);
  });
});
