/* The tag filter, both sides (lib/tagScope): one chip per tag that cycles
 * off → on it → not on it (`cycleTag`) — the portal control's shape, so a
 * negative filter reads the same everywhere. Browse's sidebar and the pipeline
 * board's bar render this one control; the chip's dot is the tag's colour, its
 * fill the choice (copper = must carry, brick = must not). Editing a tag is
 * the assign list's job (components/curation/TagAssignList), not a filter's. */

import { useQuery } from '@tanstack/react-query';

import { PickButton } from '@/components/controls';
import ReadFailedMark, { readFailed } from '@/components/ReadFailedMark';
import { listTags } from '@/lib/api';
import { curationKeys } from '@/lib/queries';
import { cycleTag, type TagChoice } from '@/lib/tagScope';
import type { Tag } from '@/lib/types';

export type TagOption = Pick<Tag, 'id' | 'name' | 'color'>;

export function TagFilterControl({
  tags,
  value,
  onChange,
}: {
  tags: ReadonlyArray<TagOption>;
  value: TagChoice;
  onChange: (next: TagChoice) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {tags.map((t) => {
          const out = value.tagsExcluded.includes(t.id);
          return (
            <PickButton
              key={t.id}
              on={out || value.tags.includes(t.id)}
              negative={out}
              onClick={() => onChange(cycleTag(value, t.id))}
              variant="solid"
              className="min-w-16"
              ariaLabel={out ? `${t.name} (excluded)` : undefined}
            >
              <span
                aria-hidden
                className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle"
                style={{ background: `var(--color-tag-${t.color})` }}
              />
              {out ? `− ${t.name}` : t.name}
            </PickButton>
          );
        })}
      </div>
      {value.tags.length > 1 && (
        <p className="text-[0.65rem] text-[var(--color-ink-4)]">
          Nemovitost musí mít všechny vybrané štítky.
        </p>
      )}
    </div>
  );
}

/* A chip for every tag the account has, in its own words when a selected tag
 * no longer exists (deleted elsewhere) so the constraint stays deselectable. */
export const tagOptionsFor = (
  known: ReadonlyArray<TagOption>,
  value: TagChoice,
): TagOption[] => {
  const out = [...known];
  for (const id of [...value.tags, ...value.tagsExcluded]) {
    if (!out.some((t) => t.id === id)) out.push({ id, name: `#${id}`, color: 'slate' });
  }
  return out;
};

/* Browse's sidebar control: the whole account's tags, over the shared index. */
export function AccountTagFilter({
  value,
  onChange,
}: {
  value: TagChoice;
  onChange: (next: TagChoice) => void;
}) {
  const tagsQ = useQuery({
    queryKey: curationKeys.tags,
    queryFn: listTags,
    staleTime: 60_000,
  });
  if (tagsQ.isLoading) {
    return <p className="text-[0.75rem] text-[var(--color-ink-4)]">Loading…</p>;
  }
  /* A failed read is a retry, never "No tags yet" (MS16). */
  if (readFailed(tagsQ)) {
    return <ReadFailedMark what="Štítky" variant="header" onRetry={() => void tagsQ.refetch()} />;
  }
  const tags = tagOptionsFor(tagsQ.data?.data ?? [], value);
  if (tags.length === 0) {
    return (
      <p className="text-[0.75rem] text-[var(--color-ink-4)]">
        No tags yet. Add one from any property page or pipeline card.
      </p>
    );
  }
  return <TagFilterControl tags={tags} value={value} onChange={onChange} />;
}
