/* Sbírky on the phone (#1769, #1760 Q11): the account's collections as a
 * hairline roll — the name set in Fraunces like a title on an index page, the
 * count and the founding day under it, a quiet "sledováno" mark on a monitored
 * one — and "Nová sbírka" as the one action, a name in a bottom sheet.
 *
 * Reads and writes are the desktop page's own: `useCollections`,
 * `createCollection` (monitoring off — monitoring is set on the desktop, Q11),
 * then `revalidateCollections` so every collection glyph on
 * every card hears about the new list. Rename, delete, monitoring and sharing
 * have no control here on purpose; the footer says where they live. Collections
 * someone shared with me follow under "Sdílené se mnou", read-only. */

import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import BottomSheet from '@/components/phone/BottomSheet';
import { usePhoneHeaderAside } from '@/components/phone/PhoneShell';
import { RollEmpty } from '@/components/phone/RollParts';
import { BellIcon, ChevronIcon, PlusIcon } from '@/components/phone/phoneIcons';
import { createCollection } from '@/lib/api';
import { isMine, revalidateCollections, useCollections } from '@/lib/collectionCache';
import { dayRuleLabel, pragueDayKey } from '@/lib/feed';
import { czPlural, fmtCount } from '@/lib/format';
import { ROUTES } from '@/lib/routes';
import { pushToast } from '@/lib/toast';
import type { Collection } from '@/lib/types';

export const DESKTOP_ONLY_NOTE = 'Přejmenování, smazání, sledování a sdílení nastavíte na počítači.';
/* A collection shared with me: nothing of it is mine to manage but my place in it. */
export const VIEWER_NOTE = 'Sbírku ze svého seznamu odeberete na počítači.';

export const propertiesWord = (n: number): string =>
  czPlural(n, 'nemovitost', 'nemovitosti', 'nemovitostí');

/* "založena dnes", "založena 3. 10.", "založena 3. 10. 2025" — the feed's day
 * words, on the Prague calendar. */
export function foundedLabel(createdAt: string, now: number = Date.now()): string {
  return `založena ${dayRuleLabel(pragueDayKey(createdAt), pragueDayKey(now)).toLowerCase()}`;
}

/* "sdílí jana@…" for a collection shared with me; null for my own. */
export const sharedByLabel = (c: Collection): string | null =>
  isMine(c) ? null : `sdílí ${c.owner_email}`;

/* The small monitored mark: ink, never the accent (colour on the phone means
 * the stage). */
export function MonitoredMark() {
  return (
    <span
      title="Sledovaná sbírka — změny ceny a stažení inzerátu přijdou jako upozornění"
      className="inline-flex shrink-0 items-center gap-1 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-ink-3)]"
    >
      <BellIcon className="h-3 w-3" />
      sledováno
    </span>
  );
}

export default function PhoneCollections() {
  const [creating, setCreating] = useState(false);
  const sharedHeadingId = useId();
  const { mine, shared, query: listQ } = useCollections();
  /* Mine only: the shared ones are counted by their own group. */
  const total = listQ.data ? mine.length : null;

  usePhoneHeaderAside(
    total != null && total > 0 ? `${fmtCount(total)} ${czPlural(total, 'sbírka', 'sbírky', 'sbírek')}` : null,
  );

  return (
    <div className="pt-3">
      {listQ.isPending ? (
        <RowsSkeleton />
      ) : listQ.isError ? (
        <RollEmpty
          title="Sbírky se nepodařilo načíst."
          detail="Spojení nebo server teď neodpověděly. Zkuste to znovu."
          action={{ label: 'Zkusit znovu', onClick: () => void listQ.refetch() }}
        />
      ) : mine.length === 0 ? (
        <RollEmpty
          title={shared.length > 0 ? 'Zatím žádná vlastní sbírka.' : 'Zatím žádná sbírka.'}
          detail="Založte první; nemovitost do ní uložíte záložkou na kartě v Nových."
        />
      ) : (
        <ul aria-label="Sbírky" className="border-b border-[var(--color-rule)]">
          {mine.map((c) => (
            <li key={c.id}>
              <CollectionRow c={c} />
            </li>
          ))}
        </ul>
      )}

      {/* Ruling P30: the add slot ends my own list, dashed (a place to fill), in
          every state of the read, as it was reachable before. */}
      <div className="mt-3 px-4">
        <button
          type="button"
          onClick={() => setCreating(true)}
          aria-haspopup="dialog"
          className="flex h-11 w-full items-center justify-center gap-2 rounded-[var(--radius-sm)] border border-dashed border-[var(--color-rule-strong)] bg-[var(--color-paper)] text-[15px] font-medium text-[var(--color-copper)] active:bg-[var(--color-inset)]"
        >
          <PlusIcon className="h-[18px] w-[18px]" />
          Nová sbírka
        </button>
      </div>

      {listQ.isSuccess && shared.length > 0 && (
        <section aria-labelledby={sharedHeadingId} className="mt-6">
          <h3
            id={sharedHeadingId}
            className="px-4 pb-2 text-sm leading-5 font-semibold text-[var(--color-ink)]"
          >
            Sdílené se mnou
          </h3>
          <ul aria-label="Sdílené se mnou" className="border-b border-[var(--color-rule)]">
            {shared.map((c) => (
              <li key={c.id}>
                <CollectionRow c={c} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <p className="px-4 pb-6 pt-4 text-[12px] text-[var(--color-ink-3)]">{DESKTOP_ONLY_NOTE}</p>

      {creating && (
        <NewCollectionSheet
          existing={mine.map((c) => c.name.toLowerCase())}
          onClose={() => setCreating(false)}
        />
      )}
    </div>
  );
}

/* One row for both groups; a shared one names its owner ("sdílí …") where an
 * own one names its founding day, and never carries the monitored mark (the
 * alerts are the owner's). */
function CollectionRow({ c }: { c: Collection }) {
  return (
    <Link
      to={ROUTES.collectionDetail.build({ id: c.id })}
      className="flex min-h-16 items-center gap-3 border-t border-[var(--color-rule)] py-3 pl-4 pr-3 active:bg-[var(--color-paper-2)]"
    >
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline gap-2">
          <span className="truncate font-display text-[19px] leading-snug text-[var(--color-ink)]">{c.name}</span>
          {isMine(c) && c.monitoring_enabled && <MonitoredMark />}
        </span>
        {c.description && (
          <span className="mt-0.5 block truncate text-[13px] text-[var(--color-ink-2)]">{c.description}</span>
        )}
        <span className="mt-0.5 block text-[12px] text-[var(--color-ink-3)]">
          <span className="font-mono tabular-nums text-[var(--color-ink-2)]">{fmtCount(c.listing_count)}</span>{' '}
          {propertiesWord(c.listing_count)} ·{' '}
          {sharedByLabel(c) ?? foundedLabel(c.created_at)}
        </span>
      </span>
      <ChevronIcon className="h-4 w-4 shrink-0 text-[var(--color-ink-4)]" />
    </Link>
  );
}

function RowsSkeleton() {
  return (
    <>
      <p className="sr-only" role="status">
        Načítám…
      </p>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          aria-hidden
          className="flex min-h-16 flex-col justify-center gap-2 border-t border-[var(--color-rule)] px-4 py-3 last:border-b"
        >
          <div className="h-5 w-44 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          <div className="h-3.5 w-32 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
        </div>
      ))}
    </>
  );
}

/* The name, and nothing else: monitoring starts off, as the desktop form's
 * default; the description stays a desktop field. Return on the keyboard
 * submits, so the sheet works with the keyboard covering its lower half. */
function NewCollectionSheet({ existing, onClose }: { existing: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const inputId = useId();
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (n: string) => createCollection({ name: n, description: null, monitoring_enabled: false }),
    onSuccess: (c) => {
      revalidateCollections(qc);
      pushToast('ok', `Sbírka „${c.name}“ je založená.`);
      onClose();
    },
    onError: (err: Error) => setError(err.message || 'Sbírku se nepodařilo založit.'),
  });

  const trimmed = name.trim();
  const dup = trimmed.length > 0 && existing.includes(trimmed.toLowerCase());
  const disabled = trimmed.length === 0 || dup || create.isPending;

  return (
    <BottomSheet label="Nová sbírka" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!disabled) create.mutate(trimmed);
        }}
        className="flex min-h-[45dvh] flex-col gap-2 px-4 pt-3"
      >
        <label htmlFor={inputId} className="text-[13px] text-[var(--color-ink-2)]">
          Název
        </label>
        <input
          id={inputId}
          type="text"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError(null);
          }}
          maxLength={200}
          autoComplete="off"
          enterKeyHint="done"
          placeholder="např. Vysočany, k prohlídce"
          aria-invalid={dup || error != null || undefined}
          className="h-11 w-full rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-3 text-[16px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]"
        />
        {dup && (
          <p className="text-[13px] text-[var(--color-brick)]">Sbírka „{trimmed}“ už existuje.</p>
        )}
        {error && !dup && <p className="text-[13px] text-[var(--color-brick)]">{error}</p>}
        <button
          type="submit"
          disabled={disabled}
          className="mt-2 h-11 w-full rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[15px] font-medium text-[var(--color-paper)] transition-opacity disabled:opacity-40"
        >
          {create.isPending ? 'Zakládám…' : 'Založit sbírku'}
        </button>
        <p className="mt-1 text-[12px] text-[var(--color-ink-3)]">
          Sledování změn zapnete na počítači.
        </p>
      </form>
    </BottomSheet>
  );
}
