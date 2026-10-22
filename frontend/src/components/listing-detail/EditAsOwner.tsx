/* "Upravit jako {email}" on the desktop property page (design §3.5, §8-4,
 * §8-10). While the active board is an EDIT share, the header offers the
 * switch; on, the header's curation verbs and the curation block render inside
 * <ActingAs> and act as the board's owner, and the banner below the header
 * says so with the way out. The switch is sticky per device (lib/acting):
 * it stays on across property pages until "Ukončit" or a change of the
 * active board. Nothing else on the page acts — estimations, Valuo, the map
 * and the sold comps stay mine. Not on the phone (ruling 6). */

import { setEditAsOwner, shareOwnerName } from '@/lib/acting';
import type { PipelineShare } from '@/lib/types';

const editAsLabel = (share: PipelineShare): string => `Upravit jako ${shareOwnerName(share)}`;

export function EditAsOwnerToggle({ share, on }: { share: PipelineShare; on: boolean }) {
  return (
    <button
      type="button"
      onClick={() => setEditAsOwner(!on)}
      aria-pressed={on}
      title={
        on
          ? 'Ukončit úpravy jako vlastník pipeline'
          : 'Štítky, kolekce, poznámky, odkaz, vyřazení a pipeline této nemovitosti upravovat jako vlastník sdíleného pipeline'
      }
      className={[
        'inline-flex max-w-[16rem] items-center gap-1.5 rounded-[var(--radius-sm)] border px-3 py-1.5 text-[0.8rem] transition-colors',
        on
          ? 'border-[var(--color-copper)] bg-[var(--color-copper-soft)] text-[var(--color-copper)]'
          : 'border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)]',
      ].join(' ')}
    >
      <span className="truncate">{editAsLabel(share)}</span>
    </button>
  );
}

/* In the page, under the header row — never in the 3.5 rem top bar. */
export function ActingBanner({ share }: { share: PipelineShare }) {
  return (
    <div
      role="status"
      className="mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-[var(--radius-sm)] border border-[var(--color-copper)]/40 bg-[var(--color-copper-soft)] px-3 py-2 text-[0.8rem]"
    >
      <p className="text-[var(--color-ink-2)]">
        Upravujete jako <span className="text-[var(--color-ink)]">{shareOwnerName(share)}</span>
        <span className="text-[var(--color-ink-3)]">
          {' '}
          · pipeline, kolekce, vyřazení, štítky, odkaz a poznámky; odhady a vše ostatní zůstávají vaše
        </span>
      </p>
      <button
        type="button"
        onClick={() => setEditAsOwner(false)}
        className="ml-auto text-[0.72rem] tracking-[0.1em] uppercase text-[var(--color-copper)] underline-offset-2 hover:underline"
      >
        Ukončit
      </button>
    </div>
  );
}
