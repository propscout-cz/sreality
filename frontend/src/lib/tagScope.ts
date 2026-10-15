/* Tag membership as a COHORT — one definition, rendered for whichever surface
 * asks: Browse's two property-id lists and the pipeline board's in-memory
 * predicate (the tag analogue of lib/collectionScope). Two sides, the portal
 * rule's shape (lib/portals cyclePortal): a property matches when it carries
 * EVERY tag in `tags` (AND — the registry's stated semantics since migration
 * 025) and NONE of `tagsExcluded`. Nothing chosen = no constraint. */

import type { ListingFilters } from './filters';

export type TagChoice = Pick<ListingFilters, 'tags' | 'tagsExcluded'>;

export const NO_TAG_CHOICE: TagChoice = { tags: [], tagsExcluded: [] };

export const tagChoiceActive = (c: TagChoice): boolean =>
  c.tags.length > 0 || c.tagsExcluded.length > 0;

/* THE chip transition for every tag control (Browse's sidebar, the board's
 * bar): one tap moves a tag off → on it → not on it → off. Builds both fields
 * explicitly, so a caller may pass a whole ListingFilters. */
export function cycleTag(c: TagChoice, id: number): TagChoice {
  const without = (xs: ReadonlyArray<number>) => xs.filter((x) => x !== id);
  if (c.tags.includes(id)) {
    return { tags: without(c.tags), tagsExcluded: [...c.tagsExcluded, id] };
  }
  if (c.tagsExcluded.includes(id)) {
    return { tags: [...c.tags], tagsExcluded: without(c.tagsExcluded) };
  }
  return { tags: [...c.tags, id], tagsExcluded: [...c.tagsExcluded] };
}

/** The rule per property: `undefined` memberIds = carries no tag. */
export function matchesTags(
  memberIds: readonly number[] | undefined,
  c: TagChoice,
): boolean {
  const has = new Set(memberIds ?? []);
  return c.tags.every((id) => has.has(id)) && !c.tagsExcluded.some((id) => has.has(id));
}

/** The member map reduced to Browse's lists: `allow` = the properties carrying
 * every included tag (null when none is included), `deny` = the properties
 * carrying any excluded tag (null when none is excluded). `[]` on either side
 * is a resolved answer, never "no constraint": an allowlist nothing matched
 * empties the cohort, a denylist nothing matched drops nothing. */
export function propertyIdsForTags(
  members: ReadonlyMap<number, readonly number[]>,
  c: TagChoice,
): { allow: number[] | null; deny: number[] | null } {
  const include = new Set(c.tags);
  const exclude = new Set(c.tagsExcluded);
  const allow: number[] | null = include.size ? [] : null;
  const deny: number[] | null = exclude.size ? [] : null;
  for (const [property_id, ids] of members) {
    const has = new Set(ids);
    if (allow && [...include].every((id) => has.has(id))) allow.push(property_id);
    if (deny && [...exclude].some((id) => has.has(id))) deny.push(property_id);
  }
  return { allow, deny };
}
