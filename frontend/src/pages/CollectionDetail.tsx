import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ROUTES } from '@/lib/routes';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  ApiError,
  deleteCollection,
  getCollection,
  isApiConfigured,
  removePropertyFromCollection,
  shareCollection,
  unshareCollection,
  updateCollection,
} from '@/lib/api';
import { settleCollectionWrite } from '@/lib/collectionCache';
import { revalidateTables } from '@/lib/curationSync';
import { trimDraft, useServerDraft } from '@/lib/useServerDraft';
import { isMine } from '@/lib/collectionRules';
import { useCollectionCards } from '@/lib/collectionCards';
import {
  DEFAULT_SORT,
  curationKeys,
  fetchBrowseCount,
  parseSort,
  sortToParam,
  type SortSpec,
} from '@/lib/queries';
import { browseKeys } from '@/lib/browseKeys';
import { useCardCoverTag, useCardImageLarge } from '@/lib/browseLayout';
import { CardHydrationProvider } from '@/lib/hydration';
import { useCardEstimates } from '@/lib/useCardEstimates';
import { propertyPath } from '@/lib/listingUrl';
import { useNewTabProps } from '@/lib/linkGestures';
import { usePageTitle } from '@/lib/pageTitle';
import { Field } from '@/components/controls';
import ErrorBanner from '@/components/ErrorBanner';
import ListingCards, { CARD_PHOTOS_PER_CARD } from '@/components/ListingCards';
import OverflowMenu, { type OverflowMenuItem } from '@/components/OverflowMenu';
import Tabs, { type Tab } from '@/components/Tabs';
import {
  fmtAbsolute,
  fmtArea,
  fmtCount,
  fmtCzk,
  fmtRelative,
} from '@/lib/format';
import { listingKindLabel } from '@/lib/enums';
import type {
  Collection,
  CollectionPropertyRow,
  CollectionWithProperties,
} from '@/lib/types';
import Panel, { PanelEmpty } from '@/components/Panel';
import ShareBlock from '@/components/ShareBlock';
import { TwoStep } from '@/components/TwoStep';
import { useActing } from '@/lib/acting';
import { Th } from '@/components/table';
import { usePhoneShell } from '@/lib/phoneShell';
import { PhoneSuspense } from '@/components/phone/RollParts';
import { lazyChunk } from '@/lib/lazyChunk';

const PhoneCollectionDetail = lazyChunk(() => import('./phone/PhoneCollectionDetail'));

/* Under the phone shell: the members as feed cards, removal through the
 * card's collection sheet (#1760 Q11); editing and monitoring stay here. */
export default function CollectionDetail() {
  return usePhoneShell() ? (
    <PhoneSuspense>
      <PhoneCollectionDetail />
    </PhoneSuspense>
  ) : <CollectionDetailDesktop />;
}

function CollectionDetailDesktop() {
  const { id: idParam } = useParams();
  const id = idParam && /^\d+$/.test(idParam) ? Number(idParam) : null;

  const q = useQuery<CollectionWithProperties, Error>({
    queryKey: id != null ? curationKeys.collection(id) : ['curation', 'collection', 'invalid'],
    queryFn: () => getCollection(id as number),
    enabled: id != null,
    staleTime: 30_000,
  });

  usePageTitle(q.data ? q.data.collection.name : null);
  /* Acting as a pipeline's owner (lib/acting) the API refuses monitoring (403):
   * the alerts are the owner's, never a delegate's to change. */
  const acting = useActing() != null;

  if (id == null) {
    return <NotFoundState reason="invalid" id={idParam ?? null} />;
  }

  if (q.isLoading) {
    return (
      <Page>
        <Crumb />
        <div className="mt-8 text-sm text-[var(--color-ink-3)]">Loading…</div>
      </Page>
    );
  }

  if (q.error) {
    const apiErr = q.error as ApiError | Error;
    if (apiErr instanceof ApiError && apiErr.status === 404) {
      return <NotFoundState reason="missing" id={String(id)} />;
    }
    return (
      <Page>
        <Crumb />
        <ErrorBanner title="Failed to load:" message={q.error.message} onRetry={() => void q.refetch()} />
      </Page>
    );
  }

  if (!q.data) {
    return <NotFoundState reason="missing" id={String(id)} />;
  }

  const { collection, properties } = q.data;
  /* A collection shared with me is view-only: no rename, monitoring, sharing,
   * delete or per-row Remove (the API would 404 / no-op them anyway). */
  const own = isMine(collection);
  return (
    <Page>
      <Crumb />
      <Header collection={collection} own={own} acting={acting} />
      <div className="mt-6">
        <Members collection={collection} properties={properties} editable={own} />
      </div>
    </Page>
  );
}

/* -------------------------------------------------------------------------- */
/* Layout primitives                                                          */
/* -------------------------------------------------------------------------- */

function Page({ children }: { children: React.ReactNode }) {
  return <div className="px-6 py-8 max-w-5xl mx-auto">{children}</div>;
}

function Crumb() {
  return (
    <Link
      to={ROUTES.collections.build()}
      className="inline-flex items-center gap-1.5 text-[0.75rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-copper)] transition-colors"
    >
      <BackArrow />
      <span>All collections</span>
    </Link>
  );
}

/* -------------------------------------------------------------------------- */
/* Header                                                                     */
/* -------------------------------------------------------------------------- */

/* Ruling P31: the owner's actions sit in the header — the Sledovat switch,
 * "Sdílet · N" and ⋯ (Přejmenovat, Smazat kolekci) — each opening what its page
 * block held: the switch itself, the sharing block in a panel under the header,
 * the edit fields in place of the name, the delete confirm. */
function Header({
  collection,
  own,
  acting,
}: {
  collection: Collection;
  own: boolean;
  acting: boolean;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelId = useId();
  /* OverflowMenu keeps its trigger to itself; the editor hands focus back to it. */
  const menuRef = useRef<HTMLSpanElement>(null);
  const focusMenu = () =>
    menuRef.current?.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')?.focus();

  const del = useMutation({
    mutationFn: () => deleteCollection(collection.id),
    onSuccess: () => {
      // Migration 202 cascades its memberships away.
      void revalidateTables(qc, ['collections', 'collection_properties']);
      navigate(ROUTES.collections.build(), { replace: true });
    },
    onError: (err: Error) => setError(err.message || 'Failed to delete'),
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
      : [{ label: 'Smazat kolekci', danger: true, onSelect: () => setConfirmingDelete(true) }]),
  ];

  return (
    <div className="mt-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          {editing ? (
            <HeaderEditor
              collection={collection}
              onDone={() => {
                setEditing(false);
                focusMenu();
              }}
            />
          ) : (
            <>
              <h1
                className="text-[2.6rem] leading-[1.05]"
                style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
              >
                {collection.name}
              </h1>
              {collection.description && (
                <p className="mt-2 text-sm text-[var(--color-ink-2)] max-w-2xl">
                  {collection.description}
                </p>
              )}
            </>
          )}
          <p className="mt-2 text-[0.7rem] tracking-wide text-[var(--color-ink-4)]">
            <span className="font-mono tabular-nums text-[var(--color-ink-3)]">
              {fmtCount(collection.listing_count)}
            </span>{' '}
            {collection.listing_count === 1 ? 'property' : 'properties'} · updated{' '}
            <span className="cursor-help" title={fmtAbsolute(collection.updated_at)}>
              {fmtRelative(collection.updated_at)}
            </span>
          </p>
          {!own && <ViewerLine collection={collection} />}
        </div>
        {own && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 pt-2">
            {!acting && <MonitorSwitch collection={collection} setError={setError} />}
            <button
              type="button"
              onClick={() => setSharing((v) => !v)}
              aria-expanded={sharing}
              aria-controls={sharing ? panelId : undefined}
              className={HEADER_BUTTON}
            >
              Sdílet
              {recipients > 0 && (
                <>
                  {' · '}
                  <span className="ml-1 font-mono tabular-nums">{fmtCount(recipients)}</span>
                </>
              )}
            </button>
            <span ref={menuRef} className="contents">
              <OverflowMenu label="Collection actions" items={items} />
            </span>
            {confirmingDelete && (
              <TwoStep
                idle="Delete collection"
                confirm={del.isPending ? 'Deleting…' : `Delete "${collection.name}"`}
                confirming
                setConfirming={setConfirmingDelete}
                pending={del.isPending}
                onConfirm={() => del.mutate()}
              />
            )}
          </div>
        )}
      </div>
      {error && (
        <p className="mt-3 text-[0.75rem] text-[var(--color-brick)]">{error}</p>
      )}
      {own && sharing && (
        <div className="mt-5">
          <SharingBlock collection={collection} id={panelId} />
        </div>
      )}
    </div>
  );
}

/* Pipeline's header button, open while its panel shows (a disclosure, so the
 * state is aria-expanded, never also aria-pressed). */
const HEADER_BUTTON =
  'inline-flex items-center h-8 px-3 whitespace-nowrap rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] text-[0.8rem] text-[var(--color-ink-2)] hover:border-[var(--color-rule-strong)] hover:text-[var(--color-ink)] aria-expanded:border-[var(--color-copper)] aria-expanded:text-[var(--color-copper)] transition-colors';

/* The recipient's side: who shared it, that it is read-only, and the one write
 * a recipient has — leaving (its own grant, the only row in `shared_with`). */
function ViewerLine({ collection }: { collection: Collection }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const grant = collection.shared_with[0];

  const leave = useMutation({
    mutationFn: (account_id: string) => unshareCollection(collection.id, account_id),
    onSuccess: () => {
      navigate(ROUTES.collections.build(), { replace: true });
      void revalidateTables(qc, ['collection_shares']);
    },
  });

  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
      <p className="text-[0.8rem] text-[var(--color-ink-2)]">
        Shared by <span className="text-[var(--color-ink)]">{collection.owner_email}</span>
        <span className="text-[var(--color-ink-4)]"> · view only</span>
      </p>
      {grant && (
        <TwoStep
          idle="Remove from my list"
          confirm={leave.isPending ? 'Removing…' : 'Remove'}
          confirming={confirming}
          setConfirming={setConfirming}
          pending={leave.isPending}
          onConfirm={() => leave.mutate(grant.account_id)}
        />
      )}
      {leave.error && (
        <p className="w-full text-[0.75rem] text-[var(--color-brick)]">{leave.error.message}</p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Rename / description, in place of the header's name (⋯ → Přejmenovat)       */
/* -------------------------------------------------------------------------- */

function HeaderEditor({
  collection,
  onDone,
}: {
  collection: Collection;
  /* Saved or cancelled: the header flips back. */
  onDone: () => void;
}) {
  const qc = useQueryClient();
  /* Both fields follow the collection while untouched, and only what the user
   * changed is saved: a rename made elsewhere is never written back (D3,
   * lib/useServerDraft). */
  const name = useServerDraft(collection.name, trimDraft);
  const description = useServerDraft(collection.description ?? '', trimDraft);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const descriptionRef = useRef<HTMLInputElement>(null);

  /* The default collection's name is locked, so its first field is the description. */
  useEffect(() => {
    (collection.is_system ? descriptionRef : nameRef).current?.focus();
  }, [collection.is_system]);

  const nextName = name.edited();
  const nextDescription = description.edited();
  const dirty = nextName != null || nextDescription != null;

  const save = useMutation({
    mutationFn: () =>
      updateCollection(collection.id, {
        ...(nextName != null && { name: nextName }),
        /* A cleared description goes as '': PATCH /collections reads null as "not
         * given" (coalesce), so null alone is refused and never clears it. */
        ...(nextDescription != null && { description: nextDescription }),
      }),
    onSuccess: () => {
      setError(null);
      void revalidateTables(qc, ['collections']);
      onDone();
    },
    onError: (err: Error) => setError(err.message || 'Failed to save'),
  });

  return (
    <div
      className="flex flex-col gap-2.5 max-w-2xl"
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          onDone();
        }
      }}
    >
      <Field label="Name" as="control">
        <input
          ref={nameRef}
          type="text"
          value={name.value}
          onChange={(e) => name.onChange(e.target.value)}
          onFocus={name.onFocus}
          onBlur={name.onBlur}
          maxLength={200}
          placeholder="Name"
          disabled={collection.is_system}
          title={collection.is_system ? "The default collection can't be renamed." : undefined}
          className="w-full px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] disabled:opacity-50 disabled:cursor-not-allowed"
        />
      </Field>
      <Field label="Description" as="control">
        <input
          ref={descriptionRef}
          type="text"
          value={description.value}
          onChange={(e) => description.onChange(e.target.value)}
          onFocus={description.onFocus}
          onBlur={description.onBlur}
          placeholder="Description (optional)"
          className="w-full px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]"
        />
      </Field>
      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={!dirty || save.isPending || name.value.trim().length === 0}
          onClick={() => save.mutate()}
          className="px-4 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-[var(--color-on-copper)] hover:bg-[var(--color-copper-2)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {save.isPending ? 'Saving…' : 'Save changes'}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="px-2.5 py-1.5 text-sm tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)] transition-colors"
        >
          Cancel
        </button>
        {collection.is_system && (
          <span className="ml-auto text-[0.72rem] text-[var(--color-ink-4)]">
            Default collection — can't be renamed or deleted.
          </span>
        )}
      </div>
      {error && (
        <p className="text-[0.75rem] text-[var(--color-brick)]">{error}</p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Monitoring switch ("Sledovat", in the header)                              */
/* -------------------------------------------------------------------------- */

/* MonitoringBlock's switch as it was drawn; its sentences, which sat beside it,
 * now describe it (aria-describedby) and are its tooltip. */
function MonitorSwitch({
  collection,
  setError,
}: {
  collection: Collection;
  setError: (message: string | null) => void;
}) {
  const qc = useQueryClient();

  const toggle = useMutation({
    mutationFn: (next: boolean) =>
      updateCollection(collection.id, { monitoring_enabled: next }),
    onSuccess: () => {
      setError(null);
      void revalidateTables(qc, ['collections']);
    },
    onError: (err: Error) => setError(err.message || 'Failed to update'),
  });

  const on = collection.monitoring_enabled;
  const switchId = useId();
  const labelId = useId();
  const descriptionId = useId();
  const description = `${
    on
      ? 'On — a property in this collection raises an alert when its price changes or it is delisted.'
      : 'Off — properties here are not monitored for changes.'
  } Alerts appear in the Notifications feed.`;

  return (
    <div className="flex h-8 items-center gap-2 pr-1" title={description}>
      <label
        id={labelId}
        htmlFor={switchId}
        className="cursor-pointer select-none text-[0.8rem] text-[var(--color-ink-2)]"
      >
        Sledovat
      </label>
      <button
        id={switchId}
        type="button"
        role="switch"
        aria-checked={on}
        aria-labelledby={labelId}
        aria-describedby={descriptionId}
        disabled={toggle.isPending}
        onClick={() => toggle.mutate(!on)}
        className={[
          'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50',
          on ? 'bg-[var(--color-copper)]' : 'bg-[var(--color-rule-strong)]',
        ].join(' ')}
      >
        <span
          aria-hidden="true"
          className={[
            'inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform',
            on ? 'translate-x-[1.15rem]' : 'translate-x-[0.15rem]',
          ].join(' ')}
        />
      </button>
      <span id={descriptionId} className="sr-only">
        {description}
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Sharing (owner only)                                                       */
/* -------------------------------------------------------------------------- */

/* Share by email, read-only (migration 610) — the one ShareBlock, without modes;
 * the panel "Sdílet · N" opens under the header. */
function SharingBlock({ collection, id }: { collection: Collection; id: string }) {
  const qc = useQueryClient();
  const changed = () => revalidateTables(qc, ['collection_shares']);
  return (
    <ShareBlock
      id={id}
      title="Sharing"
      description="People you share with see this collection and its properties, and read the notes you mark shared. They can't change it, and alerts stay yours."
      recipients={collection.shared_with}
      onShare={(email) => shareCollection(collection.id, email).then(changed)}
      onRevoke={(account_id) => unshareCollection(collection.id, account_id).then(changed)}
    />
  );
}

/* -------------------------------------------------------------------------- */
/* Members: Browse's cards first, the table second (ruling P32)               */
/* -------------------------------------------------------------------------- */

type MembersTab = 'cards' | 'table';
const MEMBER_TABS: ReadonlyArray<Tab<MembersTab>> = [
  { key: 'cards', label: 'Cards' },
  { key: 'table', label: 'Table' },
];

/* The tab and the card sort live in the address (?tab=table, ?sort=), so Back
 * from a property lands on the view it was opened from. */
function Members({
  collection,
  properties,
  editable,
}: {
  collection: Collection;
  properties: CollectionPropertyRow[];
  editable: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const ids = useId();
  const tab: MembersTab = params.get('tab') === 'table' ? 'table' : 'cards';
  const setTab = (next: MembersTab) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        if (next === 'table') out.set('tab', 'table');
        else out.delete('tab');
        return out;
      },
      { replace: true },
    );

  /* An empty collection has nothing to draw as cards: its sentence alone. */
  if (properties.length === 0) {
    return <PropertiesBlock collectionId={collection.id} properties={properties} editable={editable} />;
  }
  return (
    <div>
      <Tabs tabs={MEMBER_TABS} active={tab} onChange={setTab} ariaLabel="Zobrazení nemovitostí" idPrefix={ids} />
      <div role="tabpanel" id={`${ids}panel-${tab}`} aria-labelledby={`${ids}tab-${tab}`} className="mt-5">
        {tab === 'table' ? (
          <PropertiesBlock collectionId={collection.id} properties={properties} editable={editable} />
        ) : (
          <CardsTab collectionId={collection.id} members={properties} onOpenTable={() => setTab('table')} />
        )}
      </div>
    </div>
  );
}

const NO_HOVER: ReadonlySet<number> = new Set();
const noop = () => {};

/* Browse's card grid over the collection (lib/collectionCards), with its notes,
 * pipeline, collection and estimate marks; no map, so no hover sync, and no
 * merge mode. The page scrolls it, not a column of its own. Browse's
 * per-browser image size and cover choice apply. */
function CardsTab({
  collectionId,
  members,
  onOpenTable,
}: {
  collectionId: number;
  members: CollectionPropertyRow[];
  onOpenTable: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const sort = parseSort(params.get('sort'));
  const onSort = (next: SortSpec) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        if (sortToParam(next) === sortToParam(DEFAULT_SORT)) out.delete('sort');
        else out.set('sort', sortToParam(next));
        return out;
      },
      { replace: true },
    );

  const { filters, list, uncarded } = useCollectionCards({ collectionId, members, sort });
  const countQ = useQuery<number, Error>({
    queryKey: browseKeys.count(filters),
    queryFn: ({ signal }) => fetchBrowseCount(filters, { signal }),
    staleTime: 60_000,
  });
  const imageLarge = useCardImageLarge();
  const coverTag = useCardCoverTag();
  const restorationKey = `collection:${collectionId}:${sortToParam(sort)}`;
  const estimates = useCardEstimates({ rows: list.rows, enabled: isApiConfigured(), resetKey: restorationKey });
  const listingIds = useMemo(() => list.rows.map((r) => r.listing_id), [list.rows]);

  /* MS16: a failed read is a retry, never an empty grid; the table still lists every member.
   * A failed next page or re-read keeps the cards already drawn, the banner under them; a
   * failed next page's retry asks for that page alone. */
  const failed = list.isError ? (
    <ErrorBanner
      flush={list.rows.length === 0}
      title="Failed to load:"
      message={list.error?.message ?? 'The cards did not load.'}
      onRetry={list.isFetchNextPageError ? list.fetchNextPage : list.refetch}
    />
  ) : null;
  if (failed && list.rows.length === 0) return failed;
  return (
    <>
      <CardHydrationProvider listingIds={listingIds} renders={{ photos: CARD_PHOTOS_PER_CARD, floors: true }}>
        <ListingCards
          scroll="page"
          rows={list.isLoading ? null : list.rows}
          total={countQ.data ?? null}
          sort={sort}
          imageLarge={imageLarge.value}
          coverTag={coverTag.value}
          onCoverTag={coverTag.set}
          isLoading={list.isLoading}
          isError={list.isError}
          isFetchingNextPage={list.isFetchingNextPage}
          hasNextPage={list.hasNextPage}
          /* A failed next page waits for the banner's retry: the end of the grid stays
             in view, and would ask a page that keeps failing again in a loop. */
          onReachEnd={list.isFetchNextPageError ? noop : list.fetchNextPage}
          restorationKey={restorationKey}
          hasFilters={false}
          hasBounds={false}
          hoveredIds={NO_HOVER}
          onHover={noop}
          onSort={onSort}
          onClearFilters={noop}
          onClearBounds={noop}
          merge={null}
          estimates={estimates.estimates}
          estimatingIds={estimates.estimatingIds}
          estimateRefusals={estimates.estimateRefusals}
          onEstimate={estimates.onEstimate}
          empty={
            <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--color-rule)] bg-[var(--color-paper-2)] p-8 text-center">
              <p className="text-sm text-[var(--color-ink-2)]">
                None of these properties has a card yet: Browse draws one once a property's
                location is confirmed and it is published. The table lists every member.
              </p>
              <button
                type="button"
                onClick={onOpenTable}
                className="mt-3 text-[0.75rem] tracking-wide uppercase text-[var(--color-copper)] hover:underline"
              >
                Open the table
              </button>
            </div>
          }
        />
      </CardHydrationProvider>
      {/* Under a grid only: with no card at all, the grid's empty node says it. */}
      {list.rows.length > 0 && uncarded.length > 0 && (
        <p className="mt-3 text-[0.78rem] text-[var(--color-ink-3)]">
          <span className="font-mono tabular-nums text-[var(--color-ink-2)]">{fmtCount(uncarded.length)}</span>{' '}
          more without a card yet (location not confirmed, or not published yet).{' '}
          <button
            type="button"
            onClick={onOpenTable}
            className="text-[var(--color-copper)] hover:underline underline-offset-2"
          >
            Open the table
          </button>
        </p>
      )}
      {failed}
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Listings table                                                             */
/* -------------------------------------------------------------------------- */

function PropertiesBlock({
  collectionId,
  properties,
  editable,
}: {
  collectionId: number;
  properties: CollectionPropertyRow[];
  /* The owner's view: the per-row Remove column and the invitation to add. */
  editable: boolean;
}) {
  return (
    <Panel title="Properties" count={properties.length}>
      {properties.length === 0 ? (
        <PanelEmpty>
          {editable
            ? `No properties yet. Use the "Add to collection" picker on a listing's detail page to put something here.`
            : 'No properties in this collection yet.'}
        </PanelEmpty>
      ) : (
        /* A table that scrolls keeps its own box (C9). */
        <div className="rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-[var(--color-paper-2)] border-b border-[var(--color-rule)]">
                <tr>
                  <Th align="left" size="sm">ID</Th>
                  <Th align="left" size="sm">District</Th>
                  <Th align="left" size="sm">Type</Th>
                  <Th align="right" size="sm">Area</Th>
                  <Th align="right" size="sm">Price</Th>
                  <Th align="left" size="sm">Last seen</Th>
                  <Th align="left" size="sm">Status</Th>
                  <Th align="left" size="sm">Added</Th>
                  {editable && <Th align="right" size="sm">{''}</Th>}
                </tr>
              </thead>
              <tbody>
                {properties.map((row) => (
                  <PropertyRowView
                    key={row.property_id}
                    row={row}
                    collectionId={collectionId}
                    editable={editable}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  );
}

function PropertyRowView({
  row,
  collectionId,
  editable,
}: {
  row: CollectionPropertyRow;
  collectionId: number;
  editable: boolean;
}) {
  const qc = useQueryClient();
  /* A collection is worked through row by row: a property opens in a new tab. */
  const newTab = useNewTabProps();

  const remove = useMutation({
    mutationFn: () => removePropertyFromCollection(collectionId, row.property_id),
    onSuccess: () => settleCollectionWrite(qc, row.property_id, collectionId, false),
  });

  return (
    <tr className="border-b border-[var(--color-rule-soft)] last:border-b-0 hover:bg-[var(--color-copper-soft)]/40 transition-colors">
      <td className="px-3 py-2.5 align-middle font-mono tabular-nums text-[var(--color-ink-3)]">
        {/* Only sreality has a meaningful public id; other portals carry a
          * synthetic surrogate that must never render as an "ID". */}
        {row.source === 'sreality' ? row.sreality_id : '—'}
      </td>
      <td className="px-3 py-2.5 align-middle text-[var(--color-ink-2)]">
        {/* The place names the property, so it is the row's link — the id
          * beside it is a dash for every portal but sreality. */}
        <Link
          to={propertyPath(row.property_id)}
          {...newTab}
          className={`hover:text-[var(--color-copper)] hover:underline underline-offset-2 ${
            row.display_label ? '' : 'text-[var(--color-ink-4)]'
          }`}
        >
          {row.display_label ?? 'Unknown location'}
        </Link>
      </td>
      <td className="px-3 py-2.5 align-middle font-mono tabular-nums text-[var(--color-ink-2)]">
        {listingKindLabel(row) ?? <span className="text-[var(--color-ink-4)]">—</span>}
      </td>
      <td className="px-3 py-2.5 align-middle text-right font-mono tabular-nums text-[var(--color-ink-2)]">
        {row.area_m2 != null ? fmtArea(row.area_m2) : <span className="text-[var(--color-ink-4)]">—</span>}
      </td>
      <td className="px-3 py-2.5 align-middle text-right font-mono tabular-nums text-[var(--color-ink)]">
        {fmtCzk(row.price_czk)}
      </td>
      <td
        className="px-3 py-2.5 align-middle text-[var(--color-ink-2)] cursor-help"
        title={fmtAbsolute(row.last_seen_at)}
      >
        {fmtRelative(row.last_seen_at)}
      </td>
      <td className="px-3 py-2.5 align-middle">
        <StatusDot active={row.is_active} />
      </td>
      <td
        className="px-3 py-2.5 align-middle text-[var(--color-ink-3)] text-[0.78rem] cursor-help"
        title={fmtAbsolute(row.added_at)}
      >
        {fmtRelative(row.added_at)}
      </td>
      {editable && (
        <td className="px-3 py-2.5 align-middle text-right">
          <button
            type="button"
            onClick={() => remove.mutate()}
            disabled={remove.isPending}
            aria-label="Remove listing from collection"
            title="Remove from collection"
            className="text-[0.7rem] tracking-wide text-[var(--color-ink-4)] hover:text-[var(--color-brick)] disabled:opacity-50 transition-colors"
          >
            {remove.isPending ? '…' : 'Remove'}
          </button>
        </td>
      )}
    </tr>
  );
}

function StatusDot({ active }: { active: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[0.7rem] tracking-wide">
      <span
        className="w-1.5 h-1.5 rounded-full"
        style={{ background: active ? 'var(--color-sage)' : 'var(--color-brick)' }}
        aria-hidden
      />
      <span className="text-[var(--color-ink-3)]">
        {active ? 'Active' : 'Inactive'}
      </span>
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Not-found state                                                            */
/* -------------------------------------------------------------------------- */

function NotFoundState({
  reason,
  id,
}: {
  reason: 'invalid' | 'missing';
  id: string | null;
}) {
  return (
    <Page>
      <Crumb />
      <div className="mt-12">
        <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
          Not found
        </p>
        <h1
          className="mt-2 text-2xl"
          style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
        >
          {reason === 'invalid' ? (
            'No collection requested'
          ) : (
            <>
              No collection with id{' '}
              <span className="font-mono tabular-nums text-[var(--color-ink-2)]">
                {id}
              </span>
            </>
          )}
        </h1>
        <p className="mt-3 text-sm text-[var(--color-ink-3)]">
          {reason === 'missing'
            ? 'The id may be wrong, the collection was deleted, or it is no longer shared with you.'
            : 'The id may be wrong, or the collection was deleted.'}
          <Link
            to={ROUTES.collections.build()}
            className="ml-1 text-[var(--color-copper)] hover:underline"
          >
            All collections
          </Link>
          .
        </p>
      </div>
    </Page>
  );
}

function BackArrow() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
      <polyline
        points="5.5,1.5 1.5,5 5.5,8.5"
        stroke="currentColor"
        strokeWidth="1.25"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line
        x1="1.5"
        y1="5"
        x2="9"
        y2="5"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </svg>
  );
}
