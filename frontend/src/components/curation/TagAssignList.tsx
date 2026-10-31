/* THE tag assign list (rule #18): every tag of the account as a checkable row,
 * a find-or-create box above it, the edit pencil beside each row. One body for
 * every surface that puts a tag on a property — the property page renders it
 * inline, the board card opens it in a popover, the phone card in a bottom
 * sheet — the way <CollectionSaveList> is shared by every collection
 * affordance. Before it, the property page held a private editor and no card
 * could tag at all.
 *
 * Membership is a PROP, not a read: every surface answers it from the one
 * shared member map (curationKeys.propertyTagMembers). Writes are here, and
 * every one settles through lib/tagCache, so a tag put on a board card is on
 * the property page, the Browse cohort and the phone without a reload.
 */

import { useMemo, useState, type RefObject } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import AnchoredPopover from '@/components/AnchoredPopover';
import TagColorPicker from '@/components/TagColorPicker';
import TagEditPopover from '@/components/curation/TagEditPopover';
import { actOpts, useActAs } from '@/lib/acting';
import { attachTag, createTag, detachTag } from '@/lib/api';
import { revalidateTables } from '@/lib/curationSync';
import { settleTagWrite } from '@/lib/tagCache';
import { useTagReads } from '@/lib/useCardTags';
import type { TagColor } from '@/lib/types';

/* The trigger's accessible name, and the panel's — one string so a test that
 * finds the button finds the panel it opens. */
export const TAG_ASSIGN_LABEL = 'Štítky';
export const TAG_SEARCH_LABEL = 'Najít nebo vytvořit štítek';

export function TagAssignMenu({
  property_id,
  memberIds,
  anchorRef,
  onClose,
  id,
}: {
  property_id: number;
  memberIds: ReadonlySet<number>;
  anchorRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  id?: string;
}) {
  return (
    <AnchoredPopover
      id={id}
      anchorRef={anchorRef}
      onClose={onClose}
      ariaLabel={TAG_ASSIGN_LABEL}
      className="w-[18rem] p-1.5"
    >
      <TagAssignList property_id={property_id} memberIds={memberIds} />
    </AnchoredPopover>
  );
}

/* Class sets per container: `popover` the desktop panel, `sheet` the phone's
 * thumb-sized rows, `block` the property page's inline section. */
const SIZES = {
  popover: {
    search:
      'w-full rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-2 py-1 text-[0.8rem] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]',
    note: 'px-1.5 py-1.5 text-[0.78rem] text-[var(--color-ink-3)]',
    list: 'mt-1 max-h-60 overflow-y-auto',
    row: 'flex min-w-0 flex-1 items-center gap-2 px-1.5 py-1.5 text-left text-[0.82rem] rounded-[var(--radius-xs)] hover:bg-[var(--color-copper-soft)] disabled:opacity-60',
    box: 'inline-flex items-center justify-center w-4 h-4 shrink-0 rounded-[3px] border text-[0.6rem] leading-none',
    count: 'ml-auto shrink-0 font-mono text-[0.7rem] tabular-nums text-[var(--color-ink-4)]',
    create: 'mt-1.5 border-t border-[var(--color-rule-soft)] px-1.5 pt-2',
    createLabel: 'text-[0.65rem] tracking-[0.18em] uppercase text-[var(--color-ink-4)]',
    createButton:
      'ml-auto rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 py-1 text-[0.75rem] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-50',
  },
  sheet: {
    search:
      'mx-4 my-2 block w-[calc(100%-2rem)] rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-3 py-2 text-[15px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]',
    note: 'px-4 py-3 text-[14px] text-[var(--color-ink-3)]',
    list: '',
    row: 'flex h-12 min-w-0 flex-1 items-center gap-3 px-4 text-left text-[15px] active:bg-[var(--color-paper-2)] disabled:opacity-60',
    box: 'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-[var(--radius-xs)] border text-[0.7rem] leading-none',
    count: 'ml-auto shrink-0 font-mono text-[12px] tabular-nums text-[var(--color-ink-4)]',
    create: 'border-t border-[var(--color-rule-soft)] px-4 pb-2 pt-3',
    createLabel: 'text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]',
    createButton:
      'ml-auto inline-flex h-9 items-center rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 text-[13px] text-[var(--color-on-copper)] active:bg-[var(--color-copper-2)] disabled:opacity-50',
  },
  block: {
    search:
      'w-full max-w-sm rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-2.5 py-1.5 text-sm text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]',
    note: 'mt-3 text-sm text-[var(--color-ink-3)]',
    list: 'mt-2 max-w-sm',
    row: 'flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left text-sm rounded-[var(--radius-xs)] hover:bg-[var(--color-copper-soft)] disabled:opacity-60',
    box: 'inline-flex items-center justify-center w-4 h-4 shrink-0 rounded-[3px] border text-[0.6rem] leading-none',
    count: 'ml-auto shrink-0 font-mono text-[0.7rem] tabular-nums text-[var(--color-ink-4)]',
    create: 'mt-2 max-w-sm border-t border-[var(--color-rule-soft)] pt-2',
    createLabel: 'text-[0.65rem] tracking-[0.18em] uppercase text-[var(--color-ink-4)]',
    createButton:
      'ml-auto rounded-[var(--radius-sm)] bg-[var(--color-copper)] px-3 py-1 text-[0.75rem] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-50',
  },
} as const;

export function TagAssignList({
  property_id,
  memberIds,
  size = 'popover',
}: {
  property_id: number;
  /* Tag ids this property already carries — owned by the caller's read. */
  memberIds: ReadonlySet<number>;
  size?: keyof typeof SIZES;
}) {
  const cls = SIZES[size];
  const qc = useQueryClient();
  const [query, setQuery] = useState('');
  const [color, setColor] = useState<TagColor>('copper');
  const [createError, setCreateError] = useState<string | null>(null);

  const actAs = useActAs();
  const { tagsQ } = useTagReads({ members: false });
  const tags = useMemo(() => tagsQ.data?.data ?? [], [tagsQ.data]);

  const attach = useMutation({
    mutationFn: (tag_id: number) => attachTag(property_id, tag_id, ...actOpts(actAs)),
    onSuccess: (_, tag_id) => settleTagWrite(qc, property_id, tag_id, true, actAs),
  });
  const detach = useMutation({
    mutationFn: (tag_id: number) => detachTag(property_id, tag_id, ...actOpts(actAs)),
    onSuccess: (_, tag_id) => settleTagWrite(qc, property_id, tag_id, false, actAs),
  });
  /* A new tag goes straight onto the property: that is why it was typed here. */
  const create = useMutation({
    mutationFn: (name: string) => createTag({ name, color }, ...actOpts(actAs)),
    onSuccess: (t) => {
      setCreateError(null);
      setQuery('');
      void revalidateTables(qc, ['tags'], actAs);
      attach.mutate(t.id);
    },
    onError: (err: Error) => setCreateError(err.message || 'Štítek se nepodařilo vytvořit'),
  });
  const pending = attach.isPending || detach.isPending || create.isPending;

  const trimmed = query.trim();
  const q = trimmed.toLowerCase();
  /* The property's own tags first, then the rest, alphabetical within each. */
  const shown = useMemo(
    () =>
      tags
        .filter((t) => !q || t.name.toLowerCase().includes(q))
        .sort(
          (a, b) =>
            Number(memberIds.has(b.id)) - Number(memberIds.has(a.id)) ||
            a.name.localeCompare(b.name, 'cs'),
        ),
    [tags, q, memberIds],
  );
  const names = useMemo(() => tags.map((t) => t.name.toLowerCase()), [tags]);
  const canCreate = trimmed.length > 0 && !names.includes(q);

  return (
    <>
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Najít nebo vytvořit…"
        aria-label={TAG_SEARCH_LABEL}
        maxLength={50}
        className={cls.search}
      />
      {tagsQ.isLoading ? (
        <p className={cls.note}>Načítám…</p>
      ) : tagsQ.isError ? (
        /* A failed read is NOT an empty one (MS16). */
        <p className={cls.note}>
          Štítky se nepodařilo načíst
          <button
            type="button"
            onClick={() => void tagsQ.refetch()}
            className="ml-2 font-medium text-[var(--color-copper)] underline underline-offset-2 hover:no-underline"
          >
            Zkusit znovu
          </button>
        </p>
      ) : shown.length === 0 ? (
        <p className={cls.note}>
          {tags.length === 0 ? 'Zatím žádné štítky — napište název.' : 'Nic nenalezeno.'}
        </p>
      ) : (
        <ul className={cls.list} aria-label={TAG_ASSIGN_LABEL}>
          {shown.map((t) => {
            const member = memberIds.has(t.id);
            return (
              <li key={t.id} className="flex items-center gap-0.5">
                <button
                  type="button"
                  disabled={pending}
                  aria-pressed={member}
                  onClick={() => (member ? detach : attach).mutate(t.id)}
                  className={cls.row}
                >
                  <span
                    aria-hidden
                    className={[
                      cls.box,
                      member
                        ? 'bg-[var(--color-copper)] border-[var(--color-copper)] text-[var(--color-on-copper)]'
                        : 'border-[var(--color-rule-strong)] text-transparent',
                    ].join(' ')}
                  >
                    ✓
                  </span>
                  <span
                    aria-hidden
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: `var(--color-tag-${t.color})` }}
                  />
                  <span className="truncate text-[var(--color-ink)]">{t.name}</span>
                  <span className={cls.count}>{t.listing_count}</span>
                </button>
                <span className="shrink-0 pr-1">
                  <TagEditPopover tag={t} otherNames={names.filter((n) => n !== t.name.toLowerCase())} />
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {canCreate && (
        <div className={cls.create}>
          <p className={cls.createLabel}>Vytvořit „{trimmed}“</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            <TagColorPicker value={color} onChange={(c) => c && setColor(c)} size="sm" />
            <button
              type="button"
              onClick={() => create.mutate(trimmed)}
              disabled={create.isPending}
              className={cls.createButton}
            >
              {create.isPending ? 'Vytvářím…' : 'Vytvořit'}
            </button>
          </div>
          {createError && (
            <p className="mt-1.5 text-[0.7rem] text-[var(--color-brick)]">{createError}</p>
          )}
        </div>
      )}
    </>
  );
}
