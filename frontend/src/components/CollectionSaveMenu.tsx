/* The "save to collection" panel: every collection of the caller's own as a
 * checkable row (a shared one is view-only, and every saved mark counts the
 * same `mine` set — migration 610), monitored ones first and marked with a bell. Shared by the Browse card's
 * over-photo glyph and the listing header's button, the way
 * <PipelineStageMenu> is shared by every pipeline affordance — three surfaces
 * growing three answers to "which collections is this in, and how do I change
 * that" is the failure mode this file exists to prevent.
 *
 * The panel is the shared <AnchoredPopover> (portalled to <body>, so it escapes
 * the card photo's `overflow-hidden` frame and the card's own <Link>), which
 * owns dismissal: outside pointerdown, Escape (returning focus to the trigger),
 * and the anchor scrolling out of view.
 *
 * Membership is a PROP, not a read: every surface answers it from the one
 * shared member map (lib/collectionCache `useCollectionMembership`). Writes are
 * here, so both surfaces issue the same call and revalidate through the one
 * helper — which is what keeps a save made in the header visible on the card
 * grid without a reload.
 *
 * The phone feed (#1766) opens the same list in a bottom sheet
 * (`CollectionSaveList size="sheet"`): one set of writes, two containers.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import type { RefObject } from 'react';

import AnchoredPopover from '@/components/AnchoredPopover';
import {
  addPropertiesToCollection,
  removePropertyFromCollection,
} from '@/lib/api';
import { revalidateCollections, useCollections } from '@/lib/collectionCache';
import { ROUTES } from '@/lib/routes';

/* The trigger's accessible name, and the panel's — one string so a test that
 * finds the button finds the panel it opens. */
export const COLLECTION_SAVE_LABEL = 'Uložit do kolekce';

export default function CollectionSaveMenu({
  property_id,
  memberIds,
  anchorRef,
  onClose,
  id,
  cohortScoped = false,
}: {
  property_id: number;
  /* Collection ids this property is already in — owned by the caller's read. */
  memberIds: Set<number>;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  /* DOM id, so the trigger's aria-controls can point at the panel. */
  id?: string;
  /* The surrounding cohort is scoped to collections — see revalidateCollections. */
  cohortScoped?: boolean;
}) {
  return (
    <AnchoredPopover
      id={id}
      anchorRef={anchorRef}
      onClose={onClose}
      ariaLabel={COLLECTION_SAVE_LABEL}
      className="w-56 p-1.5"
    >
      <CollectionSaveList property_id={property_id} memberIds={memberIds} cohortScoped={cohortScoped} />
    </AnchoredPopover>
  );
}

/* Class sets per container: `popover` is the desktop panel, unchanged; `sheet`
 * the phone's thumb-sized rows, in the phone's own words (Sbírky). */
const SIZES = {
  popover: {
    note: 'px-1.5 py-1.5 text-[0.78rem] text-[var(--color-ink-3)]',
    create: 'block px-1.5 py-1.5 text-[0.78rem] text-[var(--color-copper)] hover:underline',
    list: 'max-h-60 overflow-y-auto',
    row: 'w-full flex items-center gap-2 px-1.5 py-1.5 text-left text-[0.82rem] rounded-[var(--radius-xs)] hover:bg-[var(--color-copper-soft)] disabled:opacity-60',
    box: 'inline-flex items-center justify-center w-4 h-4 shrink-0 rounded-[3px] border text-[0.6rem] leading-none',
  },
  sheet: {
    note: 'px-4 py-3 text-[14px] text-[var(--color-ink-3)]',
    create: 'flex h-12 items-center px-4 text-[15px] text-[var(--color-copper)]',
    list: '',
    row: 'flex h-12 w-full items-center gap-3 px-4 text-left text-[15px] active:bg-[var(--color-paper-2)] disabled:opacity-60',
    box: 'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-[var(--radius-xs)] border text-[0.7rem] leading-none',
  },
} as const;

const WORDS = {
  popover: { heading: 'Save to collection', loading: 'Loading…', create: 'Create a collection →' },
  sheet: { heading: null, loading: 'Načítám…', create: 'Založit sbírku →' },
} as const;

export function CollectionSaveList({
  property_id,
  memberIds,
  cohortScoped = false,
  size = 'popover',
}: {
  property_id: number;
  memberIds: Set<number>;
  cohortScoped?: boolean;
  size?: keyof typeof SIZES;
}) {
  const cls = SIZES[size];
  const words = WORDS[size];
  const qc = useQueryClient();
  /* Mine only: a collection shared with me is view-only, so it is never a
   * target here (no disabled variant to explain). */
  const { mine, query: collectionsQ } = useCollections();

  const add = useMutation({
    mutationFn: (cid: number) => addPropertiesToCollection(cid, [property_id]),
    onSuccess: (_, cid) =>
      revalidateCollections(qc, { collection_id: cid, cohortScoped }),
  });
  const remove = useMutation({
    mutationFn: (cid: number) => removePropertyFromCollection(cid, property_id),
    onSuccess: (_, cid) =>
      revalidateCollections(qc, { collection_id: cid, cohortScoped }),
  });
  const pending = add.isPending || remove.isPending;

  // Monitored collections first, then alphabetical.
  const sorted = [...mine].sort(
    (a, b) =>
      (b.monitoring_enabled ? 1 : 0) - (a.monitoring_enabled ? 1 : 0) ||
      a.name.localeCompare(b.name),
  );

  return (
    <>
      {words.heading && (
        <p className="px-1.5 py-1 text-[0.6rem] tracking-[0.16em] uppercase text-[var(--color-ink-4)]">
          {words.heading}
        </p>
      )}
      {collectionsQ.isLoading ? (
        <p className={cls.note}>{words.loading}</p>
      ) : collectionsQ.isError ? (
        /* A failed read is NOT an empty one: the old panel answered both with
         * "Create a collection →", which tells an operator whose collections
         * exist that they have none. */
        <p className={cls.note}>
          Kolekce se nepodařilo načíst
          <button
            type="button"
            onClick={() => void collectionsQ.refetch()}
            className="ml-2 font-medium text-[var(--color-copper)] underline underline-offset-2 hover:no-underline"
          >
            Zkusit znovu
          </button>
        </p>
      ) : sorted.length === 0 ? (
        <Link to={ROUTES.collections.build()} className={cls.create}>
          {words.create}
        </Link>
      ) : (
        <ul className={cls.list}>
          {sorted.map((c) => {
            const member = memberIds.has(c.id);
            return (
              <li key={c.id}>
                <button
                  type="button"
                  disabled={pending}
                  aria-pressed={size === 'sheet' ? member : undefined}
                  onClick={() => (member ? remove : add).mutate(c.id)}
                  className={cls.row}
                >
                  <span
                    aria-hidden
                    className={[
                      cls.box,
                      member
                        ? 'bg-[var(--color-copper)] border-[var(--color-copper)] text-white'
                        : 'border-[var(--color-rule-strong)] text-transparent',
                    ].join(' ')}
                  >
                    ✓
                  </span>
                  <span className="truncate text-[var(--color-ink)]">{c.name}</span>
                  {c.monitoring_enabled && (
                    <span
                      title="Monitored — alerts on changes"
                      className="ml-auto shrink-0 text-[var(--color-copper)]"
                    >
                      <BellGlyph />
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

/* The monitored-collection bell: this list's rows and the Collections page's
 * "Monitoring" pill. */
export function BellGlyph() {
  return (
    <svg
      width="9"
      height="9"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M8 1.5a3.5 3.5 0 0 0-3.5 3.5c0 3-1.5 4-1.5 4h10s-1.5-1-1.5-4A3.5 3.5 0 0 0 8 1.5ZM6.5 12.5a1.5 1.5 0 0 0 3 0" />
    </svg>
  );
}
