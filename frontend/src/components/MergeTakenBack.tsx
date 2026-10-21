/* MS12's count before a merge, one line for Browse's merge bar and the Rulings
 * page's confirm: while it is read, how many "Různé" rulings the merge takes
 * back, or that the read failed, with a retry — never a failed or unread count
 * shown as none (MS16). Both hold the merge until the read has answered. */

import type { UseQueryResult } from '@tanstack/react-query';

import type { MergePreview } from '@/lib/api';

export default function MergeTakenBack({
  preview,
  className = '',
}: {
  preview: UseQueryResult<MergePreview, Error>;
  className?: string;
}) {
  if (preview.isError) {
    return (
      <span className={`text-[var(--color-brick)] ${className}`}>
        Kolik rozhodnutí „Různé“ sloučení vezme zpět, se nepodařilo zjistit.
        <button
          type="button"
          onClick={() => void preview.refetch()}
          className="ml-1.5 font-medium underline underline-offset-2 hover:no-underline"
        >
          Zkusit znovu
        </button>
      </span>
    );
  }
  if (preview.isPending) {
    return (
      <span className={`text-[var(--color-ink-3)] ${className}`}>
        Zjišťuji, kolik rozhodnutí „Různé“ sloučení vezme zpět…
      </span>
    );
  }
  /* One count over the statement: what every letter together takes back. */
  const n = Object.values(preview.data.letters).reduce((sum, l) => sum + l.rulings_taken_back, 0);
  return n > 0 ? (
    <span className={`text-[var(--color-ink-2)] ${className}`}>Vezme zpět {n} rozhodnutí „Různé“.</span>
  ) : null;
}
