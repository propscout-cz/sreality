/* One collection on the phone (#1769, #1760 Q11): its name and count, then its
 * members as the feed's own cards — the margin marks work exactly as on Nové:
 * the funnel files, the eye hides, and the bookmark opens the collection sheet,
 * where unchecking THIS collection is the removal (the shared
 * `CollectionSaveList`, so the write is `removePropertyFromCollection` and the
 * revalidation is `revalidateCollections`, the desktop row's own pair). A
 * collection shared with me is read-only: the sheet lists only my own
 * collections, so it offers no removal here, and the header says whose it is.
 *
 * THE CARDS ARE BROWSE'S READ: `fetchListingsForCards` with the collection as
 * its property-id prefilter (`collections: [id]`, resolved through
 * `collection_properties_public`, rule 18), every status and category, hidden
 * properties included — a property the operator hid stays in the list he filed
 * it in. A removal shows at once: the card leaves when the shared member map
 * (the same map the prefilter reduces) stops listing it here. A member Browse
 * does not serve (no served location, rule 25) has no card; it stays reachable
 * as a plain line under the roll, from the collection's own read. */

import { useMemo } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { CardRoll } from '@/components/phone/CardRoll';
import { RollEmpty, RollSkeleton } from '@/components/phone/RollParts';
import DayRule from '@/components/phone/DayRule';
import { ChevronIcon } from '@/components/phone/phoneIcons';
import { ApiError, getCollection } from '@/lib/api';
import { browseKeys } from '@/lib/browseKeys';
import { isMine, useCollectionMembers } from '@/lib/collectionCache';
import { listingKindLabel } from '@/lib/enums';
import { FEED_ALL_FILTERS } from '@/lib/feed';
import type { ListingFilters } from '@/lib/filters';
import { fmtArea, fmtCount, fmtCzk } from '@/lib/format';
import type { KeysetCursor } from '@/lib/keyset';
import { propertyPath } from '@/lib/listingUrl';
import { usePageTitle } from '@/lib/pageTitle';
import {
  CARD_PAGE_SIZE,
  DEFAULT_SORT,
  curationKeys,
  fetchListingsForCards,
  type CardRow,
} from '@/lib/queries';
import { ROUTES } from '@/lib/routes';
import type { Collection, CollectionPropertyRow } from '@/lib/types';
import { useInfiniteList } from '@/lib/useInfiniteList';

import {
  DESKTOP_ONLY_NOTE,
  MonitoredMark,
  VIEWER_NOTE,
  foundedLabel,
  propertiesWord,
  sharedByLabel,
} from './PhoneCollections';

/* The collection as a Browse cohort: no category, sale and rent, any status,
 * hidden included, scoped to this one collection. */
export function collectionCardFilters(id: number): ListingFilters {
  return { ...FEED_ALL_FILTERS, status: 'any', showDismissed: true, collections: [id] };
}

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
  return (
    <header className="px-4 pb-1 pt-5">
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]">Sbírka</p>
      <h2 className="mt-1 font-display text-[26px] font-medium leading-[1.15] tracking-[-0.015em] text-[var(--color-ink)]">
        {collection.name}
      </h2>
      {collection.description && (
        <p className="mt-1.5 text-[14px] text-[var(--color-ink-2)]">{collection.description}</p>
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
        {isMine(collection) && collection.monitoring_enabled && (
          <>
            <span aria-hidden>·</span>
            <MonitoredMark />
          </>
        )}
      </p>
    </header>
  );
}

function Members({ collection, members }: { collection: Collection; members: CollectionPropertyRow[] }) {
  const navigate = useNavigate();
  const filters = useMemo(() => collectionCardFilters(collection.id), [collection.id]);
  const sort = DEFAULT_SORT;

  const list = useInfiniteList<CardRow>({
    queryKey: browseKeys.cards(filters, sort),
    queryFn: (cursor, signal) => fetchListingsForCards(filters, sort, cursor as KeysetCursor | null, { signal }),
    pageSize: CARD_PAGE_SIZE,
    getRowId: (r) => r.property_id,
  });

  /* The member map every collection glyph paints from; a remove in the sheet
   * revalidates it, and the card leaves this roll with it. A failed read hides
   * nothing (the roll's own read already scoped the cohort). */
  const memberMap = useCollectionMembers().data;
  const stillHere = (property_id: number): boolean =>
    memberMap == null || (memberMap.get(property_id) ?? []).includes(collection.id);

  const rows = list.rows.filter((r) => stillHere(r.property_id));

  /* Members with no card: only once the whole roll is in, never mid-paging. */
  const cardIds = new Set(list.rows.map((r) => r.property_id));
  const complete = !list.isLoading && !list.isError && !list.hasNextPage;
  const uncarded = complete
    ? members.filter((m) => !cardIds.has(m.property_id) && stillHere(m.property_id))
    : [];

  return (
    <div>
      <Header collection={collection} />
      <DayRule label="Nemovitosti" />
      <CardRoll
        list={list}
        rows={rows}
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
      {/* A viewer manages nothing; its one write, leaving, lives on the desktop too. */}
      <p className="px-4 pb-6 pt-4 text-[12px] text-[var(--color-ink-3)]">
        {isMine(collection) ? DESKTOP_ONLY_NOTE : VIEWER_NOTE}
      </p>
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
