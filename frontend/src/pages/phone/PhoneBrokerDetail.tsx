/* A broker on the phone (#1769, #1760 Q12): the dossier the operator pulls up
 * on a call or at a viewing — who, which firm, the number to copy — then where
 * and what they sell as plain numbers (no map: the explore-on-map modal is a
 * desktop surface and is never opened here), the firms, and their live ads as
 * the feed's own cards.
 *
 * Reads are the desktop page's: `fetchBrokerDossier` (identity, firms, region
 * footprint; contacts masked server-side for a non-admin, rendered with the
 * desktop card's exact words) and `fetchBrokerListings` (the per-type counts),
 * both under the desktop's query keys. The cards are Browse's read scoped to the
 * broker (`brokerId`, the MS19 allowlist behind the desktop "explore" view),
 * live ads only — one cohort definition for both shells. */

import { useMemo, useRef, type ReactNode } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import DayRule from '@/components/phone/DayRule';
import { CardRoll } from '@/components/phone/CardRoll';
import { RollEmpty, RollSkeleton } from '@/components/phone/RollParts';
import { browseKeys } from '@/lib/browseKeys';
import {
  contactState,
  fetchBrokerDossier,
  fetchBrokerListings,
  prettyPhone,
  type BrokerListing,
  type BrokerMembership,
  type BrokerPublic,
  type BrokerRegionShare,
  type ContactState,
} from '@/lib/brokers';
import { categoryMainLabelPlural, categoryTypeLabel } from '@/lib/enums';
import { FEED_ALL_FILTERS, dayRuleLabel, pragueDayKey } from '@/lib/feed';
import type { ListingFilters } from '@/lib/filters';
import { COPY_WORDS, copyText } from '@/lib/clipboard';
import { czPlural, fmtCount } from '@/lib/format';
import type { KeysetCursor } from '@/lib/keyset';
import { usePageTitle } from '@/lib/pageTitle';
import { portalsWord } from '@/components/phone/FeedCard';
import { CARD_PAGE_SIZE, DEFAULT_SORT, fetchListingsForCards, type CardRow } from '@/lib/queries';
import { ROUTES } from '@/lib/routes';
import { useInfiniteList } from '@/lib/useInfiniteList';

import { Initials } from './PhoneBrokers';

const MASKED_TITLE = 'Kontakt je viditelný jen pro administrátory.';

/* The broker's live ads as a Browse cohort: every category, sale and rent. */
export function brokerCardFilters(brokerId: number): ListingFilters {
  return { ...FEED_ALL_FILTERS, brokerId };
}

/* Active ads per category × offer, largest first: "Byty · prodej  23". */
export function typeCounts(rows: ReadonlyArray<BrokerListing>): Array<{ key: string; label: string; count: number }> {
  const counts = new Map<string, { label: string; count: number }>();
  for (const l of rows) {
    if (!l.is_active) continue;
    const key = `${l.category_main ?? ''}|${l.category_type ?? ''}`;
    const label = [
      l.category_main ? categoryMainLabelPlural(l.category_main) : 'Ostatní',
      l.category_type ? categoryTypeLabel(l.category_type).toLowerCase() : null,
    ]
      .filter(Boolean)
      .join(' · ');
    const hit = counts.get(key);
    if (hit) hit.count += 1;
    else counts.set(key, { label, count: 1 });
  }
  return [...counts]
    .map(([key, v]) => ({ key, ...v }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'cs'));
}

export default function PhoneBrokerDetail() {
  const { id } = useParams<{ id: string }>();
  const brokerId = Number(id);
  const valid = Number.isInteger(brokerId) && brokerId > 0;
  const navigate = useNavigate();

  const dossierQ = useQuery({
    queryKey: ['broker-dossier', brokerId],
    queryFn: () => fetchBrokerDossier(brokerId),
    enabled: valid,
  });
  const listingsQ = useQuery({
    queryKey: ['broker-listings', brokerId],
    queryFn: () => fetchBrokerListings(brokerId),
    enabled: valid,
  });
  const dossier = dossierQ.data;
  const b = dossier?.broker;
  usePageTitle(b?.display_name ?? null);

  const toBoard = { label: 'Žebříček makléřů', onClick: () => navigate(ROUTES.brokers.build()) };

  if (!valid || (dossierQ.isSuccess && (!dossier || !b))) {
    return (
      <div className="pt-4">
        <RollEmpty title="Makléř nenalezen." detail="Odkaz je špatně, nebo byl makléř sloučen s jiným." action={toBoard} />
      </div>
    );
  }
  if (dossierQ.isPending) {
    return (
      <div aria-busy>
        <div aria-hidden className="flex items-center gap-3 px-4 pb-4 pt-5">
          <span className="h-12 w-12 rounded-full bg-[var(--color-inset)]" />
          <span className="flex flex-col gap-2">
            <span className="h-6 w-44 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
            <span className="h-3.5 w-28 rounded-[var(--radius-xs)] bg-[var(--color-inset)]" />
          </span>
        </div>
        <RollSkeleton count={1} />
      </div>
    );
  }
  if (dossierQ.isError || !dossier || !b) {
    /* A failed read is NOT "no such broker". */
    return (
      <div className="pt-4">
        <RollEmpty
          title="Makléře se nepodařilo načíst."
          detail={(dossierQ.error as Error | null)?.message ?? 'Zkuste to znovu.'}
          action={{ label: 'Zkusit znovu', onClick: () => void dossierQ.refetch() }}
        />
      </div>
    );
  }

  return (
    <div>
      <Dossier b={b} />
      <Facts b={b} />
      <Sec label="Působnost">
        <Footprint shares={dossier.region_shares} />
        <Types
          rows={listingsQ.data ?? null}
          loading={listingsQ.isPending}
          error={listingsQ.isError ? (listingsQ.error as Error) : null}
          total={b.listing_count}
        />
      </Sec>
      <Sec label="Firmy" aside={dossier.memberships.length > 0 ? fmtCount(dossier.memberships.length) : null}>
        <Firms rows={dossier.memberships} />
      </Sec>
      <Offer brokerId={brokerId} />
    </div>
  );
}

/* The prototype's section block: a micro heading, a mono aside, a hairline above. */
function Sec({ label, aside, children }: { label: string; aside?: string | null; children: ReactNode }) {
  return (
    <section aria-label={label} className="border-t border-[var(--color-rule)] p-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="font-sans text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-3)]">{label}</h3>
        {aside && <span className="font-mono text-[11px] tabular-nums text-[var(--color-ink-3)]">{aside}</span>}
      </div>
      {children}
    </section>
  );
}

function Dossier({ b }: { b: BrokerPublic }) {
  return (
    <header className="px-4 pb-4 pt-5">
      <div className="flex items-center gap-3">
        <Initials name={b.display_name} size="lg" />
        <div className="min-w-0">
          <h2 className="font-display text-[22px] font-medium leading-[1.15] tracking-[-0.01em] text-[var(--color-ink)]">
            {b.display_name ?? 'Neznámý makléř'}
          </h2>
          <p className="mt-0.5 truncate text-[13px] text-[var(--color-ink-3)]">
            {b.firm_name ?? b.firm_domain ?? 'nezávislý / neznámá kancelář'}
          </p>
        </div>
      </div>
      {(b.firm_is_franchise || b.distinct_source_count > 1) && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {b.firm_is_franchise && <Mark>franšíza</Mark>}
          {b.distinct_source_count > 1 && <Mark>{portalsWord(b.distinct_source_count)}</Mark>}
        </div>
      )}
      <PhoneLine state={contactState(b.primary_phone, b.has_phone)} />
      <EmailLine state={contactState(b.primary_email, b.has_email)} />
    </header>
  );
}

/* A monochrome tag, the feed card's portal-chip shape. */
function Mark({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-5 items-center whitespace-nowrap rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1.5 text-[11px] text-[var(--color-ink-2)]">
      {children}
    </span>
  );
}

/* The number on an inset strip with "Kopírovat" (lib/clipboard: the clipboard
 * when the page may write it, else the number selected for the system's own
 * copy). A masked number reads as masked, never as absent (the desktop card's
 * words). */
function PhoneLine({ state }: { state: ContactState }) {
  const numRef = useRef<HTMLAnchorElement>(null);
  if (state.state !== 'value') {
    const masked = state.state === 'masked';
    return (
      <p
        title={masked ? MASKED_TITLE : undefined}
        className="mt-4 font-mono text-[13px] text-[var(--color-ink-4)]"
      >
        telefon {masked ? '· kontakt na vyžádání' : '—'}
      </p>
    );
  }
  const pretty = prettyPhone(state.value);
  const copy = () => void copyText(pretty, numRef.current, COPY_WORDS.phone);
  return (
    <div className="mt-4 flex items-center justify-between gap-2 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-inset)] py-1 pl-3 pr-1">
      <a
        ref={numRef}
        href={`tel:${pretty.replace(/\s/g, '')}`}
        aria-label={`Zavolat ${pretty}`}
        className="min-w-0 select-all truncate font-mono text-[15px] tabular-nums text-[var(--color-ink)]"
      >
        {pretty}
      </a>
      <button
        type="button"
        onClick={copy}
        className="inline-flex h-9 shrink-0 items-center rounded-[var(--radius-sm)] border border-[var(--color-rule-strong)] bg-[var(--color-paper)] px-3 text-[13px] text-[var(--color-ink-2)] active:bg-[var(--color-inset)]"
      >
        Kopírovat
      </button>
    </div>
  );
}

function EmailLine({ state }: { state: ContactState }) {
  if (state.state !== 'value') {
    const masked = state.state === 'masked';
    return (
      <p title={masked ? MASKED_TITLE : undefined} className="mt-2 font-mono text-[13px] text-[var(--color-ink-4)]">
        e-mail {masked ? '· kontakt na vyžádání' : '—'}
      </p>
    );
  }
  return (
    <a
      href={`mailto:${state.value}`}
      className="mt-2 flex h-9 items-center truncate font-mono text-[13px] text-[var(--color-copper)]"
    >
      {state.value}
    </a>
  );
}

/* The prototype's facts grid: label left, mono figure right, two columns. */
function Facts({ b }: { b: BrokerPublic }) {
  const facts: Array<{ label: string; value: ReactNode }> = [
    { label: 'Nemovitosti', value: <Pair active={b.active_property_count} total={b.property_count} /> },
    { label: 'Inzeráty', value: <Pair active={b.active_listing_count} total={b.listing_count} /> },
    { label: 'Portály', value: fmtCount(b.distinct_source_count) },
    {
      label: 'Naposledy',
      value: b.last_seen_at ? dayRuleLabel(pragueDayKey(b.last_seen_at), pragueDayKey(Date.now())).toLowerCase() : '—',
    },
  ];
  return (
    <section aria-label="Přehled" className="border-t border-[var(--color-rule)] p-4">
      <dl className="grid grid-cols-2 gap-x-4">
        {facts.map((f, i) => (
          <div
            key={f.label}
            className={`flex min-w-0 items-baseline justify-between gap-2 py-2 ${i >= 2 ? 'border-t border-[var(--color-rule-soft)]' : 'pt-0'}`}
          >
            <dt className="text-[12px] text-[var(--color-ink-3)]">{f.label}</dt>
            <dd className="text-right font-mono text-[12px] tabular-nums text-[var(--color-ink)]">{f.value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-1 text-[11px] text-[var(--color-ink-4)]">aktivní / celkem</p>
    </section>
  );
}

function Pair({ active, total }: { active: number; total: number }) {
  return (
    <>
      {fmtCount(active)}
      {total > active && <span className="text-[var(--color-ink-4)]"> / {fmtCount(total)}</span>}
    </>
  );
}

/* One numbers list: name left, figure right, hairlines between. */
function NumberRows({ rows }: { rows: Array<{ key: string | number; label: string; value: ReactNode; extra?: ReactNode }> }) {
  return (
    <ul>
      {rows.map((r, i) => (
        <li
          key={r.key}
          className={`flex items-baseline justify-between gap-3 py-2 ${i > 0 ? 'border-t border-[var(--color-rule-soft)]' : 'pt-0'}`}
        >
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="truncate text-[13px] text-[var(--color-ink-2)]">{r.label}</span>
            {r.extra}
          </span>
          <span className="whitespace-nowrap font-mono text-[12px] tabular-nums text-[var(--color-ink)]">{r.value}</span>
        </li>
      ))}
    </ul>
  );
}

function SubHead({ label, aside }: { label: string; aside?: string }) {
  return (
    <p className="mb-1.5 flex items-baseline justify-between gap-2 text-[12px] text-[var(--color-ink-3)]">
      <span className="font-medium text-[var(--color-ink-2)]">{label}</span>
      {aside && <span className="text-[11px]">{aside}</span>}
    </p>
  );
}

/* The regional footprint as numbers (Q12: no map). */
function Footprint({ shares }: { shares: BrokerRegionShare[] }) {
  const sorted = [...shares].sort((a, b) => b.active_property_count - a.active_property_count);
  return (
    <div>
      <SubHead label="Kraje" aside="aktivní / celkem nemovitostí" />
      {sorted.length === 0 ? (
        <p className="text-[13px] text-[var(--color-ink-4)]">Bez aktivního regionu.</p>
      ) : (
        <NumberRows
          rows={sorted.map((s) => ({
            key: s.geo_id,
            label: s.name ?? '—',
            value: <Pair active={s.active_property_count} total={s.property_count} />,
          }))}
        />
      )}
    </div>
  );
}

function Types({
  rows,
  loading,
  error,
  total,
}: {
  rows: BrokerListing[] | null;
  loading: boolean;
  error: Error | null;
  total: number;
}) {
  const counts = useMemo(() => typeCounts(rows ?? []), [rows]);
  /* The listings route caps its page; say so rather than pass a partial count
   * off as the whole book. */
  const partial = rows != null && rows.length < total;
  return (
    <div className="mt-4">
      <SubHead
        label="Typy"
        aside={partial ? `aktivní inzeráty · načteno ${fmtCount(rows.length)} z ${fmtCount(total)}` : 'aktivní inzeráty'}
      />
      {loading ? (
        <p className="text-[13px] text-[var(--color-ink-3)]">Načítám…</p>
      ) : error ? (
        <p className="text-[13px] text-[var(--color-brick)]">Inzeráty se nepodařilo načíst: {error.message}</p>
      ) : counts.length === 0 ? (
        <p className="text-[13px] text-[var(--color-ink-4)]">Žádný aktivní inzerát.</p>
      ) : (
        <NumberRows rows={counts.map((c) => ({ key: c.key, label: c.label, value: fmtCount(c.count) }))} />
      )}
    </div>
  );
}

function Firms({ rows }: { rows: BrokerMembership[] }) {
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--color-ink-4)]">Žádná kancelář (nezávislý).</p>;
  }
  return (
    <NumberRows
      rows={rows.map((m) => ({
        key: m.firm_id,
        label: m.firm_name ?? m.firm_domain ?? '—',
        extra: m.is_current ? (
          <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--color-ink-3)]">
            aktuální
          </span>
        ) : undefined,
        value: (
          <>
            {fmtCount(m.listing_count)}{' '}
            <span className="font-sans text-[var(--color-ink-3)]">
              {czPlural(m.listing_count, 'inzerát', 'inzeráty', 'inzerátů')}
            </span>
          </>
        ),
      }))}
    />
  );
}

/* The broker's live ads as the feed's cards. */
function Offer({ brokerId }: { brokerId: number }) {
  const filters = useMemo(() => brokerCardFilters(brokerId), [brokerId]);
  const sort = DEFAULT_SORT;
  const list = useInfiniteList<CardRow>({
    queryKey: browseKeys.cards(filters, sort),
    queryFn: (cursor, signal) => fetchListingsForCards(filters, sort, cursor as KeysetCursor | null, { signal }),
    pageSize: CARD_PAGE_SIZE,
    getRowId: (r) => r.property_id,
  });
  return (
    <section aria-label="Nabídka" className="border-t border-[var(--color-rule)]">
      <DayRule label="Nabídka" />
      <CardRoll
        list={list}
        errorTitle="Nabídku se nepodařilo načíst."
        endLabel="Víc aktivních nabídek tento makléř nemá."
        empty={
          <RollEmpty
            title="Žádná aktivní nabídka."
            detail="Makléř teď nemá živý inzerát na nemovitosti, kterou přehled ukazuje."
          />
        }
      />
    </section>
  );
}
