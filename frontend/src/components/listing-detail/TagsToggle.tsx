/* The property's tags as a header verb beside "Uložit do kolekce" (operator
 * 2026-10-23): the button shows the tags it carries and opens the one assign
 * list every tag surface shares (components/curation/TagAssignList — the board
 * card's popover, the phone's sheet). Property-grain (rule #18), read through
 * lib/useCardTags as whoever the page acts as; a failed read is a retry, never
 * an untagged property (MS16). */
import { useCallback, useId, useRef, useState } from 'react';
import { useCardTags } from '@/lib/useCardTags';
import TagChip from '@/components/curation/TagChip';
import { TAG_ASSIGN_LABEL, TagAssignMenu } from '@/components/curation/TagAssignList';
import ReadFailedMark, { readFailed } from '@/components/ReadFailedMark';

const SHOWN = 2;

export default function TagsToggle({ property_id }: { property_id: number }) {
  const { membersQ, memberIds, mine } = useCardTags(property_id);
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const close = useCallback(() => setOpen(false), []);

  if (membersQ.isLoading) {
    return (
      <span
        className="inline-flex h-[1.9rem] w-24 animate-pulse rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)]"
        aria-hidden
      />
    );
  }
  if (readFailed(membersQ)) {
    return <ReadFailedMark what="Štítky" onRetry={() => void membersQ.refetch()} variant="header" />;
  }

  const more = mine.length - SHOWN;
  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={mine.length ? `${TAG_ASSIGN_LABEL}: ${mine.map((t) => t.name).join(', ')}` : TAG_ASSIGN_LABEL}
        title={mine.length ? mine.map((t) => t.name).join(', ') : 'Přidat štítek'}
        className={[
          'inline-flex max-w-[22rem] items-center gap-1.5 rounded-[var(--radius-sm)] border px-3 py-1.5 text-[0.8rem] transition-colors',
          mine.length
            ? 'border-[var(--color-rule-strong)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)]'
            : 'border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)]',
        ].join(' ')}
      >
        <TagGlyph />
        {mine.length === 0 ? (
          <span>{TAG_ASSIGN_LABEL}</span>
        ) : (
          <span className="flex min-w-0 items-center gap-1 overflow-hidden text-[0.72rem] leading-none" aria-hidden>
            {mine.slice(0, SHOWN).map((t) => (
              <TagChip key={t.id} tag={t} />
            ))}
            {more > 0 && <span className="font-mono tabular-nums text-[var(--color-ink-3)]">+{more}</span>}
          </span>
        )}
        <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden className="shrink-0 text-[var(--color-ink-3)]">
          <path d="M3 4.5L6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <TagAssignMenu
          id={panelId}
          property_id={property_id}
          memberIds={memberIds}
          anchorRef={btnRef}
          onClose={close}
        />
      )}
    </>
  );
}

function TagGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden className="shrink-0">
      <path
        d="M2.5 2.5h5.2l5.8 5.8a1 1 0 010 1.4l-3.8 3.8a1 1 0 01-1.4 0L2.5 7.7V2.5z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <circle cx="5.3" cy="5.3" r="1" fill="currentColor" />
    </svg>
  );
}
