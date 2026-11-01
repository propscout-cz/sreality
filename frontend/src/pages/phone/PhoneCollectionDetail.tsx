/* One collection on the phone (#1769, #1760 Q11): its name and count, then its
 * members as the feed's own cards — the margin marks work exactly as on Nové:
 * the funnel files, the eye hides, and the bookmark opens the collection sheet,
 * where unchecking THIS collection is the removal (the shared
 * `CollectionSaveList`, so the write is `removePropertyFromCollection` and the
 * settle is `settleCollectionWrite`, the desktop row's own pair). A
 * collection shared with me is read-only: the sheet lists only my own
 * collections, so it offers no removal here, and the header says whose it is.
 *
 * MY COLLECTION'S HEADER FOLLOWS THE DESKTOP'S (ruling P31; operator 2026-10-27:
 * the phone follows desktop): the Sledovat switch, "Sdílet · N" and ⋯
 * (Přejmenovat, Smazat sbírku), each opening what the desktop's opens — the
 * sharing card under the header, the name and description as fields in place,
 * the two-step delete — over the same writes.
 *
 * THE CARDS ARE BROWSE'S READ, shared with the desktop page (lib/collectionCards).
 * A removal shows at once: the write's settle (lib/curationSettle) drops the
 * card from this roll in place. A member Browse does not serve has no card; it
 * stays reachable as a plain line under the roll, from the collection's own read. */

import { useId, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { CardRoll } from '@/components/phone/CardRoll';
import { RollEmpty, RollSkeleton } from '@/components/phone/RollParts';
import DayRule from '@/components/phone/DayRule';
import { ChevronIcon } from '@/components/phone/phoneIcons';
import OverflowMenu, { type OverflowMenuItem } from '@/components/OverflowMenu';
import ShareBlock from '@/components/ShareBlock';
import { TwoStep } from '@/components/TwoStep';
import { useActing } from '@/lib/acting';
import {
  ApiError,
  deleteCollection,
  getCollection,
  shareCollection,
  unshareCollection,
  updateCollection,
} from '@/lib/api';
import { deletedTables, revalidateTables } from '@/lib/curationSync';
import { useCollectionEditor } from '@/lib/useCollectionEditor';
import { isMine } from '@/lib/collectionRules';
import { collectionListKeys, useCollectionCards } from '@/lib/collectionCards';
import { useListHold } from '@/lib/useListHold';
import { listingKindLabel } from '@/lib/enums';
import { fmtArea, fmtCount, fmtCzk } from '@/lib/format';
import { propertyPath } from '@/lib/listingUrl';
import { usePageTitle } from '@/lib/pageTitle';
import { DEFAULT_SORT, curationKeys } from '@/lib/queries';
import { ROUTES } from '@/lib/routes';
import type { Collection, CollectionPropertyRow } from '@/lib/types';

import {
  MonitoredMark,
  VIEWER_NOTE,
  foundedLabel,
  propertiesWord,
  sharedByLabel,
} from './PhoneCollections';

export default function PhoneCollectionDetail() {
  const { id: idParam } = useParams();
  const id = idParam && /^\d+$/.test(idParam) ? Number(idParam) : null;
  const navigate = useNavigate();

  const q = useQuery({
    queryKey: id != null ? curationKeys.collection(id) : ['curation', 'collection', 'invalid'],
    queryFn: () => getCollection(id as number),
    enabled: id != null,
    staleTime: 30_000,
  });
  usePageTitle(q.data ? q.data.collection.name : null);

  const backToList = { label: 'Všechny sbírky', onClick: () => navigate(ROUTES.collections.build()) };

  if (id == null || (q.error instanceof ApiError && q.error.status === 404)) {
    return (
      <div className="pt-4">
        <RollEmpty
          title="Taková sbírka není."
          detail="Odkaz je špatně, sbírka byla na počítači smazána, nebo už s vámi není sdílená."
          action={backToList}
        />
      </div>
    );
  }
  if (q.isPending) {
    return (
      <div aria-busy>
        <div aria-hidden className="flex flex-col gap-2 px-4 pb-4 pt-5">
          <div className="h-3 w-14 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          <div className="h-7 w-52 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          <div className="h-3.5 w-40 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
        </div>
        <RollSkeleton />
      </div>
    );
  }
  if (q.isError || !q.data) {
    return (
      <div className="pt-4">
        <RollEmpty
          title="Sbírku se nepodařilo načíst."
          detail="Spojení nebo server teď neodpověděly. Zkuste to znovu."
          action={{ label: 'Zkusit znovu', onClick: () => void q.refetch() }}
        />
      </div>
    );
  }

  return <Members collection={q.data.collection} members={q.data.properties} />;
}

function Header({ collection }: { collection: Collection }) {
  const own = isMine(collection);
  /* Acting as a pipeline's owner the API refuses monitoring (403), as on the desktop. */
  const acting = useActing() != null;
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelId = useId();

  const del = useMutation({
    mutationFn: () => deleteCollection(collection.id),
    onSuccess: () => {
      void revalidateTables(qc, deletedTables('collections'));
      navigate(ROUTES.collections.build(), { replace: true });
    },
    onError: (err: Error) => setError(err.message || 'Sbírku se nepodařilo smazat.'),
  });

  const recipients = collection.shared_with.length;
  const items: OverflowMenuItem[] = [
    {
      label: 'Přejmenovat',
      onSelect: () => {
        setConfirmingDelete(false);
        setEditing(true);
      },
    },
    ...(collection.is_system
      ? []
      : [{ label: 'Smazat sbírku', danger: true, onSelect: () => setConfirmingDelete(true) }]),
  ];

  return (
    <header className="px-4 pb-1 pt-5">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]">Sbírka</p>
      {editing ? (
        <Editor collection={collection} onDone={() => setEditing(false)} />
      ) : (
        <>
          <h2 className="mt-1 font-display text-[26px] font-medium leading-[1.15] tracking-[-0.015em] text-[var(--color-ink)]">
            {collection.name}
          </h2>
          {collection.description && (
            <p className="mt-1.5 text-[14px] text-[var(--color-ink-2)]">{collection.description}</p>
          )}
        </>
      )}
      <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-[var(--color-ink-3)]">
        <span>
          <span className="font-mono tabular-nums text-[var(--color-ink-2)]">
            {fmtCount(collection.listing_count)}
          </span>{' '}
          {propertiesWord(collection.listing_count)}
        </span>
        <span aria-hidden>·</span>
        {isMine(collection) ? (
          <span>{foundedLabel(collection.created_at)}</span>
        ) : (
          <span>
            {sharedByLabel(collection)}
            <span className="text-[var(--color-ink-4)]"> · jen ke čtení</span>
          </span>
        )}
        {own && collection.monitoring_enabled && (
          <>
            <span aria-hidden>·</span>
            <MonitoredMark />
          </>
        )}
      </p>
      {own && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {!acting && <MonitorSwitch collection={collection} setError={setError} />}
          <button
            type="button"
            onClick={() => setSharing((v) => !v)}
            aria-expanded={sharing}
            aria-controls={sharing ? panelId : undefined}
            className="inline-flex h-10 items-center whitespace-nowrap rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-3 text-[14px] text-[var(--color-ink-2)] active:bg-[var(--color-inset)] aria-expanded:border-[var(--color-copper)] aria-expanded:text-[var(--color-copper)]"
          >
            Sdílet
            {recipients > 0 && (
              <>
                {' · '}
                <span className="ml-1 font-mono tabular-nums">{fmtCount(recipients)}</span>
              </>
            )}
          </button>
          <OverflowMenu label="Další akce" items={items} />
        </div>
      )}
      {own && confirmingDelete && (
        <div className="mt-3">
          <TwoStep
            idle="Smazat sbírku"
            confirm={del.isPending ? 'Mažu…' : `Smazat „${collection.name}“`}
            cancel="Zrušit"
            confirming
            setConfirming={setConfirmingDelete}
            pending={del.isPending}
            onConfirm={() => del.mutate()}
            touch
          />
        </div>
      )}
      {error && <p className="mt-2 text-[13px] text-[var(--color-brick)]">{error}</p>}
      {own && sharing && (
        <div className="mt-4">
          <SharingCard collection={collection} id={panelId} />
        </div>
      )}
    </header>
  );
}

/* ⋯ → Přejmenovat: the name and description as fields in place of the heading. */
function Editor({ collection, onDone }: { collection: Collection; onDone: () => void }) {
  const { name, description, nameRef, descriptionRef, save, canSave, error } = useCollectionEditor(collection, {
    onDone,
    failed: 'Změny se nepodařilo uložit.',
  });
  const nameId = useId();
  const descriptionId = useId();
  const field =
    'h-11 w-full rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] px-3 text-[16px] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)] disabled:opacity-50';

  return (
    <form
      className="mt-2 flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSave) save.mutate();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          onDone();
        }
      }}
    >
      <label htmlFor={nameId} className="text-[13px] text-[var(--color-ink-2)]">
        Název
      </label>
      <input
        id={nameId}
        ref={nameRef}
        type="text"
        {...name.bind}
        maxLength={200}
        disabled={collection.is_system}
        className={field}
      />
      <label htmlFor={descriptionId} className="text-[13px] text-[var(--color-ink-2)]">
        Popis
      </label>
      <input
        id={descriptionId}
        ref={descriptionRef}
        type="text"
        {...description.bind}
        placeholder="Nepovinný"
        className={field}
      />
      {collection.is_system && (
        <p className="text-[12px] text-[var(--color-ink-3)]">Výchozí sbírku nelze přejmenovat ani smazat.</p>
      )}
      {error && <p className="text-[13px] text-[var(--color-brick)]">{error}</p>}
      <div className="mt-1 flex gap-2">
        <button
          type="submit"
          disabled={!canSave}
          className="h-11 flex-1 rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[15px] font-medium text-[var(--color-paper)] transition-opacity disabled:opacity-40"
        >
          {save.isPending ? 'Ukládám…' : 'Uložit'}
        </button>
        <button type="button" onClick={onDone} className="h-11 px-4 text-[15px] text-[var(--color-ink-3)]">
          Zrušit
        </button>
      </div>
    </form>
  );
}

/* "Sledovat": the desktop's switch at the phone's size (the account sheet's track). */
function MonitorSwitch({
  collection,
  setError,
}: {
  collection: Collection;
  setError: (message: string | null) => void;
}) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (next: boolean) => updateCollection(collection.id, { monitoring_enabled: next }),
    onSuccess: () => {
      setError(null);
      void revalidateTables(qc, ['collections']);
    },
    onError: (err: Error) => setError(err.message || 'Sledování se nepodařilo změnit.'),
  });
  const on = collection.monitoring_enabled;
  const labelId = useId();
  const descriptionId = useId();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-labelledby={labelId}
      aria-describedby={descriptionId}
      disabled={toggle.isPending}
      onClick={() => toggle.mutate(!on)}
      className="flex h-10 items-center gap-2 pr-1 disabled:opacity-50"
    >
      <span id={labelId} className="text-[14px] text-[var(--color-ink-2)]">
        Sledovat
      </span>
      <span
        aria-hidden
        className={[
          'relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors',
          on ? 'bg-[var(--color-copper)]' : 'bg-[var(--color-rule-strong)]',
        ].join(' ')}
      >
        <span
          className={[
            'absolute top-[3px] h-5 w-5 rounded-full bg-[var(--color-paper)] transition-[left]',
            on ? 'left-[21px]' : 'left-[3px]',
          ].join(' ')}
        />
      </span>
      <span id={descriptionId} className="sr-only">
        {on
          ? 'Zapnuto: změna ceny nebo stažení inzerátu u nemovitosti v této sbírce vás upozorní.'
          : 'Vypnuto: nemovitosti v této sbírce se nesledují.'}
      </span>
    </button>
  );
}

/* "Sdílet · N": the one ShareBlock, read-only shares (migration 610), as on the desktop. */
function SharingCard({ collection, id }: { collection: Collection; id: string }) {
  const qc = useQueryClient();
  const changed = () => revalidateTables(qc, ['collection_shares']);
  return (
    <ShareBlock
      id={id}
      title="Sdílení"
      lang="cs"
      description="Komu sbírku nasdílíte, uvidí ji i její nemovitosti a přečte poznámky, které označíte jako sdílené. Měnit ji nemůže a upozornění zůstávají vaše."
      recipients={collection.shared_with}
      onShare={(email) => shareCollection(collection.id, email).then(changed)}
      onRevoke={(account_id) => unshareCollection(collection.id, account_id).then(changed)}
    />
  );
}

function Members({ collection, members }: { collection: Collection; members: CollectionPropertyRow[] }) {
  const navigate = useNavigate();
  const { list, uncarded } = useCollectionCards({ collectionId: collection.id, members, sort: DEFAULT_SORT });
  /* A change made elsewhere moves the roll at once, but never under a finger (ruling Q3(c)). */
  const hand = useListHold(collectionListKeys(collection.id));

  return (
    <div>
      <Header collection={collection} />
      <DayRule label="Nemovitosti" />
      <div {...hand}>
        <CardRoll
          list={list}
          rows={list.rows}
          errorTitle="Nemovitosti se nepodařilo načíst."
          endLabel={uncarded.length > 0 ? 'Další jsou níže, bez karty.' : 'Víc nemovitostí v této sbírce není.'}
          empty={
            uncarded.length > 0 ? null : isMine(collection) ? (
              <RollEmpty
                title="Sbírka je prázdná."
                detail="Nemovitost sem uložíte záložkou na kartě v Nových nebo na stránce nemovitosti."
                action={{ label: 'Otevřít Nové', onClick: () => navigate(ROUTES.feed.build()) }}
              />
            ) : (
              <RollEmpty title="Sbírka je zatím prázdná." detail="Až do ní majitel něco uloží, uvidíte to tady." />
            )
          }
        />
        {uncarded.length > 0 && <Uncarded rows={uncarded} />}
      </div>
      {/* My collection is managed from its header; a viewer manages nothing, and
          its one write, leaving, lives on the desktop. */}
      {!isMine(collection) && (
        <p className="px-4 pb-6 pt-4 text-[12px] text-[var(--color-ink-3)]">{VIEWER_NOTE}</p>
      )}
    </div>
  );
}

/* Members Browse does not serve — reachable one by one, never silently gone. */
function Uncarded({ rows }: { rows: CollectionPropertyRow[] }) {
  return (
    <section aria-label="Bez karty">
      <DayRule label="Bez karty" />
      <p className="-mt-1 px-4 pb-2 text-[12px] text-[var(--color-ink-3)]">
        Tyto nemovitosti nemají ověřenou polohu, proto mezi kartami nejsou.
      </p>
      <ul className="border-b border-[var(--color-rule)]">
        {rows.map((m) => {
          const line = [listingKindLabel(m), m.area_m2 != null ? fmtArea(m.area_m2) : null, m.display_label]
            .filter(Boolean)
            .join(' · ');
          return (
            <li key={m.property_id}>
              <Link
                to={propertyPath(m.property_id)}
                className="flex min-h-14 items-center gap-3 border-t border-[var(--color-rule)] py-2.5 pl-4 pr-3 active:bg-[var(--color-paper-2)]"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[14px] text-[var(--color-ink-2)]">{line || 'Nemovitost'}</span>
                  {!m.is_active && (
                    <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-ink-3)]">
                      neaktivní
                    </span>
                  )}
                </span>
                {m.price_czk != null && (
                  <span className="whitespace-nowrap font-mono text-[13px] tabular-nums text-[var(--color-ink)]">
                    {fmtCzk(m.price_czk)}
                  </span>
                )}
                <ChevronIcon className="h-4 w-4 shrink-0 text-[var(--color-ink-4)]" />
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
