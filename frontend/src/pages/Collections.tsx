import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ROUTES } from '@/lib/routes';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createCollection, deleteCollection } from '@/lib/api';
import { useCollections } from '@/lib/collectionCache';
import { deletedTables, revalidateTables } from '@/lib/curationSync';
import { isMine } from '@/lib/collectionRules';
import { BellGlyph } from '@/components/CollectionSaveMenu';
import { Field } from '@/components/controls';
import ErrorBanner from '@/components/ErrorBanner';
import { fmtCount, fmtRelative, fmtAbsolute } from '@/lib/format';
import type { Collection } from '@/lib/types';
import { usePhoneShell } from '@/lib/phoneShell';
import { PhoneSuspense } from '@/components/phone/RollParts';
import { lazyChunk } from '@/lib/lazyChunk';

const PhoneCollections = lazyChunk(() => import('./phone/PhoneCollections'));

/* Under the phone shell: the list and "Nová sbírka" only (#1760 Q11); rename,
 * delete and monitoring stay on this desktop page. */
export default function Collections() {
  return usePhoneShell() ? (
    <PhoneSuspense>
      <PhoneCollections />
    </PhoneSuspense>
  ) : <CollectionsDesktop />;
}

function CollectionsDesktop() {
  const qc = useQueryClient();

  const { mine, shared, query: listQ } = useCollections();

  return (
    <div className="px-6 py-8 max-w-4xl mx-auto">
      {/* Mine only: the shared ones are counted by their own section. */}
      <Header total={listQ.data ? mine.length : null} />
      <div className="mt-9">
        {listQ.isLoading && !listQ.data ? (
          <div className="text-sm text-[var(--color-ink-3)]">Loading…</div>
        ) : listQ.error ? (
          <ErrorBanner
            title="Failed to load:"
            message={listQ.error.message}
            onRetry={() => void listQ.refetch()}
          />
        ) : mine.length === 0 ? (
          <EmptyState sharedOnly={shared.length > 0} />
        ) : (
          <CollectionList items={mine} />
        )}
        {/* Ruling P30: the form opens from the end of my list, in every state of
            the read (it was reachable in all of them above the list). */}
        <div className="mt-3">
          <AddCollectionCard
            existing={mine.map((c) => c.name.toLowerCase())}
            onCreated={() => void revalidateTables(qc, ['collections'])}
          />
        </div>
        {!listQ.error && shared.length > 0 && <SharedWithMe items={shared} />}
      </div>
    </div>
  );
}

/* A group of the list, not a section card (items in a list are not cards):
 * an L3 title (system.md § Section cards ladder), never small caps. */
function SharedWithMe({ items }: { items: Collection[] }) {
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} className="mt-10">
      <h2 id={titleId} className="text-sm leading-5 font-semibold text-[var(--color-ink)]">
        Shared with me
      </h2>
      <div className="mt-3">
        <CollectionList items={items} />
      </div>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Header                                                                     */
/* -------------------------------------------------------------------------- */

function Header({ total }: { total: number | null }) {
  return (
    <header>
      <h1
        className="text-[2.1rem] leading-tight"
        style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
      >
        Curated lists
      </h1>
      <p className="mt-2 text-sm text-[var(--color-ink-2)]">
        Named groups of properties. A property can live in many collections; a
        monitored one alerts you when its price changes or it is delisted.{' '}
        {total != null && total > 0 && (
          <span className="text-[var(--color-ink-3)]">
            {fmtCount(total)} {total === 1 ? 'collection' : 'collections'}.
          </span>
        )}
      </p>
    </header>
  );
}

/* -------------------------------------------------------------------------- */
/* New-collection form                                                        */
/* -------------------------------------------------------------------------- */

/* Dashed = a place to fill (system.md); it flips into the form at the same
 * width, and back on Cancel, Escape or a created collection. */
function AddCollectionCard({
  existing,
  onCreated,
}: {
  existing: string[];
  onCreated: () => void;
}) {
  const [open, setOpen] = useState(false);
  const cardRef = useRef<HTMLButtonElement>(null);
  /* Only a close hands focus back: the first render leaves it where it is. */
  const refocus = useRef(false);

  useEffect(() => {
    if (!open && refocus.current) {
      refocus.current = false;
      cardRef.current?.focus();
    }
  }, [open]);

  const close = () => {
    refocus.current = true;
    setOpen(false);
  };

  if (open) {
    return (
      <NewCollectionForm
        existing={existing}
        onCreated={() => {
          onCreated();
          close();
        }}
        onCancel={close}
      />
    );
  }
  return (
    <button
      ref={cardRef}
      type="button"
      onClick={() => setOpen(true)}
      className="flex w-full items-center justify-center gap-2 px-4 py-4 text-sm rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule-strong)] bg-[var(--color-paper)] text-[var(--color-ink-2)] hover:border-[var(--color-copper)] hover:text-[var(--color-copper)] transition-colors"
    >
      <span aria-hidden>+</span>
      Nová kolekce
    </button>
  );
}

function NewCollectionForm({
  existing,
  onCreated,
  onCancel,
}: {
  existing: string[];
  onCreated: () => void;
  onCancel: () => void;
}) {
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => nameRef.current?.focus(), []);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [monitor, setMonitor] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: (input: {
      name: string;
      description: string | null;
      monitoring_enabled: boolean;
    }) => createCollection(input),
    onSuccess: () => {
      setName('');
      setDescription('');
      setMonitor(false);
      setError(null);
      onCreated();
    },
    onError: (err: Error) => {
      setError(err.message || 'Failed to create');
    },
  });

  const trimmed = name.trim();
  const dup = existing.includes(trimmed.toLowerCase());
  const disabled = trimmed.length === 0 || dup || mut.isPending;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (disabled) return;
        mut.mutate({
          name: trimmed,
          description: description.trim() === '' ? null : description.trim(),
          monitoring_enabled: monitor,
        });
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          onCancel();
        }
      }}
      className="rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-4 py-4"
    >
      <p className="text-[0.65rem] tracking-[0.22em] uppercase text-[var(--color-ink-2)] font-medium">
        New collection
      </p>
      <div className="mt-3 flex flex-col sm:flex-row sm:items-end gap-3">
        <Field label="Name" as="control" className="flex-1 min-w-0">
          <input
            ref={nameRef}
            type="text"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            placeholder="e.g. Shortlist"
            maxLength={200}
            className="w-full px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]"
          />
        </Field>
        <Field label="Description" as="control" className="flex-1 min-w-0">
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="optional"
            className="w-full px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]"
          />
        </Field>
        <button
          type="submit"
          disabled={disabled}
          className="shrink-0 px-4 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {mut.isPending ? 'Creating…' : 'Create'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="shrink-0 px-2.5 py-1.5 text-sm tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)] transition-colors"
        >
          Cancel
        </button>
      </div>
      <label className="mt-3 flex items-center gap-2 w-fit text-[0.78rem] text-[var(--color-ink-2)] cursor-pointer select-none">
        <input
          type="checkbox"
          checked={monitor}
          onChange={(e) => setMonitor(e.target.checked)}
          className="accent-[var(--color-copper)]"
        />
        <span>Monitor changes — alert me when a property here changes price or is delisted</span>
      </label>
      {dup && (
        <p className="mt-2 text-[0.75rem] text-[var(--color-brick)]">
          A collection named "{trimmed}" already exists.
        </p>
      )}
      {error && !dup && (
        <p className="mt-2 text-[0.75rem] text-[var(--color-brick)]">{error}</p>
      )}
    </form>
  );
}

/* -------------------------------------------------------------------------- */
/* List                                                                       */
/* -------------------------------------------------------------------------- */

function CollectionList({ items }: { items: Collection[] }) {
  return (
    <ul className="divide-y divide-[var(--color-rule-soft)] border-t border-b border-[var(--color-rule-soft)]">
      {items.map((c) => (
        <li key={c.id}>
          <CollectionRow c={c} />
        </li>
      ))}
    </ul>
  );
}

/* One row for both groups: a collection shared with me names its owner and
 * carries no Default / Delete / monitoring — those are the owner's. */
function CollectionRow({ c }: { c: Collection }) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const own = isMine(c);
  const recipients = c.shared_with.length;

  const del = useMutation({
    mutationFn: () => deleteCollection(c.id),
    onSuccess: () => void revalidateTables(qc, deletedTables('collections')),
  });

  return (
    <div className="py-4 flex items-baseline gap-5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Link
            to={ROUTES.collectionDetail.build({ id: c.id })}
            className="text-base text-[var(--color-ink)] hover:text-[var(--color-copper)] hover:underline underline-offset-2 truncate"
          >
            {c.name}
          </Link>
          {own && c.monitoring_enabled && <MonitorPill />}
        </div>
        {c.description && (
          <p className="mt-1 text-sm text-[var(--color-ink-3)] truncate">
            {c.description}
          </p>
        )}
        <p className="mt-1 text-[0.7rem] tracking-wide text-[var(--color-ink-4)]">
          {!own && <>Shared by {c.owner_email} · </>}
          Updated{' '}
          <span className="cursor-help" title={fmtAbsolute(c.updated_at)}>
            {fmtRelative(c.updated_at)}
          </span>
          {own && recipients > 0 && <> · shared with {fmtCount(recipients)}</>}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <p className="font-mono tabular-nums text-sm text-[var(--color-ink-2)]">
          {fmtCount(c.listing_count)}
        </p>
        <p className="text-[0.65rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)] mt-0.5">
          {c.listing_count === 1 ? 'listing' : 'listings'}
        </p>
      </div>
      <div className="shrink-0">
        {!own ? null : c.is_system ? (
          <span
            title="The default monitoring collection can't be deleted."
            className="px-2.5 py-1 text-[0.65rem] tracking-[0.12em] uppercase text-[var(--color-ink-4)] select-none"
          >
            Default
          </span>
        ) : confirming ? (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => del.mutate()}
              disabled={del.isPending}
              className="px-2.5 py-1 text-[0.7rem] tracking-wide rounded-[var(--radius-sm)] bg-[var(--color-brick-soft)] text-[var(--color-brick)] hover:bg-[var(--color-brick)]/15 disabled:opacity-50"
            >
              {del.isPending ? 'Deleting…' : 'Confirm'}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="px-2.5 py-1 text-[0.7rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="px-2.5 py-1 text-[0.7rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-brick)] transition-colors"
          >
            Delete
          </button>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty                                                                      */
/* -------------------------------------------------------------------------- */

function MonitorPill() {
  return (
    <span
      title="Monitoring — changes to properties here raise alerts"
      className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[0.62rem] tracking-[0.1em] uppercase rounded-[var(--radius-xs)] bg-[var(--color-copper-soft)] text-[var(--color-copper)] shrink-0"
    >
      <BellGlyph />
      <span>Monitoring</span>
    </span>
  );
}

function EmptyState({ sharedOnly }: { sharedOnly: boolean }) {
  return (
    <div className="px-6 py-12 text-center border border-dashed border-[var(--color-rule)] rounded-[var(--radius-md)]">
      <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
        {sharedOnly ? 'No collections of your own yet' : 'No collections yet'}
      </p>
      <p className="mt-2 text-sm text-[var(--color-ink-2)]">
        Use "+ Nová kolekce" below to start one. Then add listings from any
        listing's detail page.
      </p>
    </div>
  );
}
