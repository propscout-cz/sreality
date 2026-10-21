/* Merge mode's bar: the statement so far, letter by letter — each property by
 * number with its marks (MS16), so what a merge will carry is in view before the
 * click — the "Různé" rulings the statement would take back (MS12), read before
 * the merge is offered, and the letter a click writes: one chip per letter in
 * use plus the next free one, for a session without a keyboard. The merge is
 * held while a letter has one property: that is no merge, and on a page it is a
 * slip. */
import { LETTERS } from '@/lib/api';
import CurationMarks from '@/components/CurationMarks';
import MergeTakenBack from '@/components/MergeTakenBack';
import type { MergeMode } from '@/lib/useMergeMode';
import { useMergePreview } from '@/lib/useMergeProperties';

const BTN = 'px-3 py-1.5 text-sm rounded-[var(--radius-sm)] transition-colors disabled:opacity-50';
const CHIP =
  'h-6 min-w-6 px-1.5 rounded-[var(--radius-xs)] border font-mono text-[0.75rem] font-medium transition-colors';

export default function MergeBar({
  merge,
  merging,
  onMerge,
}: {
  merge: MergeMode;
  merging: boolean;
  onMerge: () => void;
}) {
  const preview = useMergePreview(merge.letters);
  const used = merge.groups.map(([letter]) => letter);
  const next = LETTERS.find((letter) => !used.includes(letter));
  const chips = next ? [...used, next] : used;
  return (
    <div className="flex items-center gap-2 shrink-0">
      {merge.active && (
        <>
          <div
            role="radiogroup"
            aria-label="Písmeno, které zapíše kliknutí"
            className="flex items-center gap-1"
          >
            {chips.map((letter) => (
              <button
                key={letter}
                type="button"
                role="radio"
                aria-checked={letter === merge.activeLetter}
                onClick={() => merge.setActiveLetter(letter)}
                className={`${CHIP} ${
                  letter === merge.activeLetter
                    ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] text-[var(--color-copper-2)]'
                    : 'border-[var(--color-rule)] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)]'
                }`}
              >
                {letter}
              </button>
            ))}
          </div>
          {merge.groups.length === 0 ? (
            <span className="text-[0.75rem] text-[var(--color-ink-3)]">
              Click a card to mark it {merge.activeLetter}, or press a letter on it
            </span>
          ) : (
            <MergeStatement groups={merge.groups} />
          )}
          {merge.complete && <MergeTakenBack preview={preview} className="text-[0.75rem]" />}
          <button
            type="button"
            onClick={onMerge}
            disabled={merging || !merge.complete || preview.isPending}
            className={`${BTN} bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)]`}
          >
            {merging ? 'Merging…' : used.length ? `Merge ${used.join(', ')}` : 'Merge'}
          </button>
        </>
      )}
      <button
        type="button"
        onClick={merge.active ? merge.exit : merge.enter}
        disabled={merging}
        className={`${BTN} border ${
          merge.active
            ? 'border-[var(--color-rule)] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] hover:border-[var(--color-rule-strong)]'
            : 'border-[var(--color-copper)] text-[var(--color-copper-2)] hover:bg-[var(--color-copper-soft)]'
        }`}
      >
        {merge.active ? 'Cancel' : 'Merge mode'}
      </button>
    </div>
  );
}

/* The statement: each letter with its properties by number and their marks,
 * read-only; a letter held by one property says so, in ochre. */
export function MergeStatement({ groups }: { groups: MergeMode['groups'] }) {
  return (
    <ul aria-label="Vybráno ke sloučení" className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {groups.map(([letter, ids]) => (
        <li key={letter} className="flex items-center gap-1.5">
          <span className="font-mono text-[0.75rem] font-medium text-[var(--color-copper-2)]">
            {letter}
          </span>
          {ids.map((id) => (
            <span key={id} className="flex items-center gap-1">
              <span className="font-mono text-[0.75rem] tabular-nums text-[var(--color-ink-2)]">
                #{id}
              </span>
              <CurationMarks property_id={id} />
            </span>
          ))}
          {ids.length < 2 && (
            <span className="text-[0.72rem] text-[var(--color-ochre)]">needs a second</span>
          )}
        </li>
      ))}
    </ul>
  );
}
