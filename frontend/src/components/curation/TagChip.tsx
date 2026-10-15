/* THE tag chip: a tag's name in its own colour. One component so the board
 * card, the phone card and the assign list print the same chip — the tag
 * analogue of <CollectionMark>, and deliberately NOT a PickButton: a filter
 * chip says on/not (copper/brick), a tag chip says WHICH tag (its colour).
 *
 * Purely presentational: the font size is the host's (inherited), so a dense
 * card row and a 13px phone row each set their own. */

import type { Tag } from '@/lib/types';

export default function TagChip({ tag }: { tag: Pick<Tag, 'name' | 'color'> }) {
  return (
    <span
      className="inline-flex max-w-full items-center truncate rounded-[var(--radius-xs)] border px-1.5 leading-[1.5]"
      style={{
        background: `var(--color-tag-${tag.color}-soft)`,
        color: `var(--color-tag-${tag.color})`,
        borderColor: `var(--color-tag-${tag.color})`,
      }}
    >
      {tag.name}
    </span>
  );
}
