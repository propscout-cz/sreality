import { useId, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
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
  removePropertyFromCollection,
  shareCollection,
  unshareCollection,
  updateCollection,
} from '@/lib/api';
import { isMine, revalidateCollections, settleCollectionWrite } from '@/lib/collectionCache';
import { curationKeys } from '@/lib/queries';
import { propertyPath } from '@/lib/listingUrl';
import { usePageTitle } from '@/lib/pageTitle';
import { DeliveryChannelsPicker } from '@/components/DeliveryChannelsPicker';
import { Field } from '@/components/controls';
import ErrorBanner from '@/components/ErrorBanner';
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
import { Hairline, SectionLabel } from '@/components/section';
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
  /* A collection shared with me is view-only: no Edit, Monitoring, Sharing or
   * per-row Remove (the API would 404 / no-op them anyway). */
  const own = isMine(collection);
  return (
    <Page>
      <Crumb />
      <Header collection={collection} />
      <Hairline />
      {own && (
        <>
          <EditBlock collection={collection} />
          <Hairline />
          {!acting && (
            <>
              <MonitoringBlock collection={collection} />
              <Hairline />
            </>
          )}
          <SharingBlock collection={collection} />
          <Hairline />
        </>
      )}
      <PropertiesBlock collectionId={collection.id} properties={properties} editable={own} />
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

function Header({ collection }: { collection: Collection }) {
  return (
    <div className="mt-5">
      <p className="text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)]">
        Collection
      </p>
      <h1
        className="mt-1.5 text-[2.6rem] leading-[1.05]"
        style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
      >
        {collection.name}
      </h1>
      {collection.description && (
        <p className="mt-2 text-sm text-[var(--color-ink-2)] max-w-2xl">
          {collection.description}
        </p>
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
      {!isMine(collection) && <ViewerLine collection={collection} />}
    </div>
  );
}

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
      revalidateCollections(qc, { collection_id: collection.id, shareChanged: true });
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
/* Edit block (rename / description) + delete                                 */
/* -------------------------------------------------------------------------- */

function EditBlock({ collection }: { collection: Collection }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(collection.name);
  const [description, setDescription] = useState(collection.description ?? '');
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const dirty =
    name.trim() !== collection.name ||
    (description.trim() === '' ? null : description.trim()) !==
      collection.description;

  const save = useMutation({
    mutationFn: () =>
      updateCollection(collection.id, {
        name: name.trim(),
        description: description.trim() === '' ? null : description.trim(),
      }),
    onSuccess: () => {
      setError(null);
      revalidateCollections(qc, { collection_id: collection.id });
    },
    onError: (err: Error) => setError(err.message || 'Failed to save'),
  });

  const del = useMutation({
    mutationFn: () => deleteCollection(collection.id),
    onSuccess: () => {
      revalidateCollections(qc);
      navigate(ROUTES.collections.build(), { replace: true });
    },
    onError: (err: Error) => setError(err.message || 'Failed to delete'),
  });

  return (
    <div>
      <SectionLabel>Edit</SectionLabel>
      <div className="mt-3 flex flex-col gap-2.5 max-w-2xl">
        <Field label="Name" as="control">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={200}
            placeholder="Name"
            disabled={collection.is_system}
            title={collection.is_system ? "The default collection can't be renamed." : undefined}
            className="w-full px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] disabled:opacity-50 disabled:cursor-not-allowed"
          />
        </Field>
        <Field label="Description" as="control">
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description (optional)"
            className="w-full px-3 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]"
          />
        </Field>
        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={!dirty || save.isPending || name.trim().length === 0}
            onClick={() => save.mutate()}
            className="px-4 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-copper)] text-white hover:bg-[var(--color-copper-2)] disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {save.isPending ? 'Saving…' : 'Save changes'}
          </button>
          <div className="ml-auto">
            {collection.is_system ? (
              <span className="text-[0.72rem] text-[var(--color-ink-4)]">
                Default collection — can't be renamed or deleted.
              </span>
            ) : (
              <TwoStep
                idle="Delete collection"
                confirm={del.isPending ? 'Deleting…' : `Delete "${collection.name}"`}
                confirming={confirmingDelete}
                setConfirming={setConfirmingDelete}
                pending={del.isPending}
                onConfirm={() => del.mutate()}
              />
            )}
          </div>
        </div>
        {error && (
          <p className="text-[0.75rem] text-[var(--color-brick)]">{error}</p>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Monitoring toggle                                                          */
/* -------------------------------------------------------------------------- */

function MonitoringBlock({ collection }: { collection: Collection }) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const invalidate = () => {
    setError(null);
    revalidateCollections(qc, { collection_id: collection.id });
  };

  const toggle = useMutation({
    mutationFn: (next: boolean) =>
      updateCollection(collection.id, { monitoring_enabled: next }),
    onSuccess: invalidate,
    onError: (err: Error) => setError(err.message || 'Failed to update'),
  });

  const channels = useMutation({
    mutationFn: (next: string[]) =>
      updateCollection(collection.id, { notify_channels: next }),
    onSuccess: invalidate,
    onError: (err: Error) => setError(err.message || 'Failed to update'),
  });

  const on = collection.monitoring_enabled;
  const labelId = useId();

  return (
    <div>
      <SectionLabel>
        <span id={labelId}>Monitoring</span>
      </SectionLabel>
      <div className="mt-3 flex items-start gap-3 max-w-2xl">
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-labelledby={labelId}
          disabled={toggle.isPending}
          onClick={() => toggle.mutate(!on)}
          className={[
            'mt-0.5 relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50',
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
        <div className="text-sm text-[var(--color-ink-2)]">
          <p>
            {on
              ? 'On — a property in this collection raises an alert when its price changes or it is delisted.'
              : 'Off — properties here are not monitored for changes.'}
          </p>
          <p className="mt-1 text-[0.78rem] text-[var(--color-ink-4)]">
            Alerts always appear in the in-app Notifications feed.
          </p>
        </div>
      </div>
      {on && (
        <div className="mt-4 max-w-2xl">
          <SectionLabel>Delivery</SectionLabel>
          <div className="mt-2">
            <DeliveryChannelsPicker
              value={collection.notify_channels ?? []}
              onChange={(next) => channels.mutate(next)}
              disabled={channels.isPending}
            />
          </div>
        </div>
      )}
      {error && (
        <p className="mt-2 text-[0.75rem] text-[var(--color-brick)]">{error}</p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Sharing (owner only)                                                       */
/* -------------------------------------------------------------------------- */

/* Share by email, read-only (migration 610) — the one ShareBlock, without modes. */
function SharingBlock({ collection }: { collection: Collection }) {
  const qc = useQueryClient();
  const changed = () =>
    revalidateCollections(qc, { collection_id: collection.id, shareChanged: true });
  return (
    <ShareBlock
      title="Sharing"
      description="People you share with see this collection and its properties, and read the notes you mark shared. They can't change it, and alerts stay yours."
      recipients={collection.shared_with}
      onShare={(email) => shareCollection(collection.id, email).then(changed)}
      onRevoke={(account_id) => unshareCollection(collection.id, account_id).then(changed)}
    />
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
    <div>
      <SectionLabel>Properties</SectionLabel>
      {properties.length === 0 ? (
        <p className="mt-3 text-sm text-[var(--color-ink-3)]">
          {editable
            ? `No properties yet. Use the "Add to collection" picker on a listing's detail page to put something here.`
            : 'No properties in this collection yet.'}
        </p>
      ) : (
        <div className="mt-3 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] overflow-hidden">
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
    </div>
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

  const remove = useMutation({
    mutationFn: () => removePropertyFromCollection(collectionId, row.property_id),
    onSuccess: () => settleCollectionWrite(qc, row.property_id, collectionId, false),
  });

  return (
    <tr className="border-b border-[var(--color-rule-soft)] last:border-b-0 hover:bg-[var(--color-copper-soft)]/40 transition-colors">
      <td className="px-3 py-2.5 align-middle font-mono tabular-nums text-[var(--color-ink-3)]">
        <Link
          to={propertyPath(row.property_id)}
          className="hover:text-[var(--color-copper)] hover:underline underline-offset-2"
        >
          {/* Only sreality has a meaningful public id; other portals carry a
            * synthetic surrogate that must never render as an "ID". */}
          {row.source === 'sreality' ? row.sreality_id : '—'}
        </Link>
      </td>
      <td className="px-3 py-2.5 align-middle text-[var(--color-ink-2)]">
        {row.display_label ?? <span className="text-[var(--color-ink-4)]">—</span>}
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
