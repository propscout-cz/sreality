/* The phone property page's own sections (#1767, the prototype's "Nemovitost"):
 * each a hairline-separated block under a micro label. Where it is (Poloha: a
 * streamed Mapy static image, never a WebGL map), facts, description, the ads
 * with their portal links, the brokers and the registered sales near the
 * property — the desktop page's reads and rules, drawn at phone width. The
 * sections the phone reuses whole (price history, estimates, freshness, tags +
 * notes) are mounted by PhoneProperty. */

import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import BrokerContactCard from '@/components/BrokerContactCard';
import { SectionLabel } from '@/components/section';
import { priceLabel } from '@/components/autodedup/SplitPlanLines';
import { propertyBrokers } from '@/lib/brokers';
import { listingKindParts } from '@/lib/enums';
import {
  fmtArea,
  fmtCzk,
  fmtDistanceM,
  fmtFloor,
  fmtMeasuredPricePerM2,
  fmtShortDate,
} from '@/lib/format';
import { useListingBrokers } from '@/lib/hydration';
import { buildAmenities, buildFacts } from '@/lib/listingFacts';
import { areaKindOf, ppm2BasisFromToken } from '@/lib/measure';
import { portalBadge } from '@/lib/portals';
import {
  fetchSoldComparables,
  fetchSoldCoverage,
  SOLD_COMPS_LIMIT,
  type PropertyPublic,
} from '@/lib/queries';
import { ROUTES } from '@/lib/routes';
import { COVERED_CATEGORIES, RADII_M, summarizeSold } from '@/lib/soldComps';
import type { ListingPublic, PropertySource, SoldComparable, SoldCoverage } from '@/lib/types';

import LocationBody from './LocationBody';
import { ExternalIcon } from './phoneIcons';

/* -------------------------------------------------------------------------- */
/* The section frame                                                          */
/* -------------------------------------------------------------------------- */

export function PhoneSection({
  label,
  aside,
  id,
  children,
}: {
  label: string;
  /* A mono count or range beside the label ("3 portály", "14 do 1 km"). */
  aside?: ReactNode;
  id?: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={label} id={id} className="scroll-mt-16 px-4 py-4">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <SectionLabel>{label}</SectionLabel>
        {aside != null && (
          <span className="font-mono text-[11px] tabular-nums text-[var(--color-ink-3)]">{aside}</span>
        )}
      </div>
      {children}
    </section>
  );
}

const CHIP =
  'inline-flex h-5 shrink-0 items-center whitespace-nowrap rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1 text-[11px] text-[var(--color-ink-2)]';

/* -------------------------------------------------------------------------- */
/* Poloha                                                                      */
/* -------------------------------------------------------------------------- */

/* The feed sheet's body, inline: the map plate, the place, the note, the map apps. */
export function LocationSection({ propertyId }: { propertyId: number }) {
  return (
    <PhoneSection label="Poloha">
      <LocationBody propertyId={propertyId} />
    </PhoneSection>
  );
}

/* -------------------------------------------------------------------------- */
/* Fakta                                                                       */
/* -------------------------------------------------------------------------- */

/* listingFacts is the one definition of WHICH facts a property shows and when
 * (decision: header + building facts + amenities, nulls pruned); the phone
 * speaks them in Czech. */
const FACT_CS: Record<string, string> = {
  Lot: 'Pozemek',
  Garden: 'Zahrada',
  Building: 'Stavba',
  Condition: 'Stav',
  Energy: 'PENB',
  Ownership: 'Vlastnictví',
  Furnished: 'Vybavení',
  Balcony: 'Balkon',
  Terrace: 'Terasa',
  Lift: 'Výtah',
  Cellar: 'Sklep',
  Garage: 'Garáž',
  Parking: 'Parkování',
};

export function phoneFacts(p: ListingPublic): Array<{ label: string; value: string }> {
  const kind = listingKindParts(p);
  const floor = fmtFloor(p.floor, p.total_floors);
  const head = [
    kind.length > 0 ? { label: 'Dispozice', value: kind.join(' · ') } : null,
    p.area_m2 != null ? { label: 'Plocha', value: fmtArea(p.area_m2, areaKindOf(p.category_main)) } : null,
    floor != null ? { label: 'Patro', value: floor } : null,
  ].filter((f): f is { label: string; value: string } => f != null);
  const facts = buildFacts(p).map((f) => ({ label: FACT_CS[f.label] ?? f.label, value: f.value ?? '' }));
  const amenities = buildAmenities(p).map((a) => ({
    label: FACT_CS[a.label] ?? a.label,
    value: a.present ? (a.note ? `ano · ${a.note}` : 'ano') : 'ne',
  }));
  return [...head, ...facts, ...amenities];
}

export function FactsSection({ property }: { property: ListingPublic }) {
  const facts = phoneFacts(property);
  if (facts.length === 0) return null;
  return (
    <PhoneSection label="Fakta">
      <dl className="grid grid-cols-2 gap-x-4">
        {facts.map((f, i) => (
          <div
            key={f.label}
            className={[
              'flex min-w-0 items-baseline justify-between gap-2 py-2',
              i < 2 ? 'pt-0' : 'border-t border-[var(--color-rule-soft)]',
            ].join(' ')}
          >
            <dt className="shrink-0 text-[12px] text-[var(--color-ink-3)]">{f.label}</dt>
            <dd className="min-w-0 text-right font-mono text-[12px] tabular-nums text-[var(--color-ink)]">
              {f.value}
            </dd>
          </div>
        ))}
      </dl>
    </PhoneSection>
  );
}

/* -------------------------------------------------------------------------- */
/* Popis                                                                       */
/* -------------------------------------------------------------------------- */

export function DescriptionSection({ text }: { text: string | null }) {
  const body = text?.trim() ?? '';
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLParagraphElement | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el) setOverflows(el.scrollHeight - el.clientHeight > 1);
  }, [body]);
  if (!body) return null;
  return (
    <PhoneSection label="Popis">
      <p
        ref={ref}
        className={`whitespace-pre-wrap text-[14px] leading-relaxed text-[var(--color-ink-2)] ${open ? '' : 'line-clamp-6'}`}
      >
        {body}
      </p>
      {(overflows || open) && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="mt-1 h-8 text-[13px] font-medium text-[var(--color-copper)]"
        >
          {open ? 'Zkrátit' : 'Celý popis'}
        </button>
      )}
    </PhoneSection>
  );
}

/* -------------------------------------------------------------------------- */
/* Inzeráty                                                                    */
/* -------------------------------------------------------------------------- */

/* Every ad of the property, the canonical one marked — the rows the desktop's
 * merged-adverts section reads (property_sources_public + each ad's deal type
 * from listings_public). The link out is the stored `source_url`, never rebuilt,
 * and opens in the SAME window: an installed iOS app shows another origin in its
 * in-app Safari sheet, with "Hotovo" back to here (#1763). */
export function AdsSection({
  sources,
  failed,
  onRetry,
  canonicalId,
  openedId,
  details,
}: {
  sources: PropertySource[];
  failed: boolean;
  onRetry: () => void;
  canonicalId: number;
  /* The ad a pasted link or an old advert address asked for. */
  openedId: number | null;
  details: Map<number, ListingPublic> | undefined;
}) {
  const portals = new Set(sources.map((s) => s.source)).size;
  if (failed) {
    return (
      <PhoneSection label="Inzeráty">
        <p className="text-[13px] text-[var(--color-brick)]">
          Inzeráty se nepodařilo načíst.
          <button type="button" onClick={onRetry} className="ml-2 font-medium underline underline-offset-2">
            Zkusit znovu
          </button>
        </p>
      </PhoneSection>
    );
  }
  if (sources.length === 0) return null;
  return (
    <PhoneSection label="Inzeráty" aside={portalsAside(portals)}>
      <ul>
        {sources.map((s, i) => (
          <li
            key={s.id}
            className={`flex flex-col gap-0.5 pb-1 ${i > 0 ? 'border-t border-[var(--color-rule-soft)] pt-3' : ''}`}
          >
            <div className="flex items-center gap-2">
              <span className={CHIP}>{portalBadge(s.source, s.source_url)}</span>
              {s.id === canonicalId && (
                <span className="text-[11px] text-[var(--color-ink-3)]">hlavní inzerát</span>
              )}
              {s.id === openedId && s.id !== canonicalId && (
                <span className="text-[11px] text-[var(--color-ink-3)]">tento odkaz</span>
              )}
              <span className="ml-auto whitespace-nowrap font-mono text-[13px] tabular-nums text-[var(--color-ink)]">
                {priceLabel(s.price_czk, details?.get(s.id)?.category_type)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2 text-[12px] text-[var(--color-ink-3)]">
              <span>
                {s.is_active ? `aktivní od ${fmtShortDate(s.first_seen_at)}` : `staženo ${fmtShortDate(s.last_seen_at)}`}
              </span>
              {s.source_url && (
                <a
                  href={s.source_url}
                  className="inline-flex h-8 items-center gap-0.5 whitespace-nowrap text-[13px] font-medium text-[var(--color-copper)]"
                >
                  Otevřít na portálu
                  <ExternalIcon className="h-4 w-4" />
                </a>
              )}
            </div>
          </li>
        ))}
      </ul>
    </PhoneSection>
  );
}

const portalsAside = (n: number): string =>
  `${n} ${n === 1 ? 'portál' : n <= 4 ? 'portály' : 'portálů'}`;

/* -------------------------------------------------------------------------- */
/* Makléři                                                                     */
/* -------------------------------------------------------------------------- */

const initials = (name: string | null): string =>
  (name ?? '')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('') || '?';

/* MS7 (lib/brokers.propertyBrokers) over the batched read the desktop list uses,
 * and the desktop's own contact card under each name — the same channels, the
 * same ad references, the same copy behaviour (lib/clipboard) on both shells.
 * A failed read says so with a retry, never "no broker"; loading and a genuinely
 * unattributed property render nothing. */
export function BrokersSection({
  adverts,
  canonicalId,
  advertsFailed,
  onRetryAdverts,
}: {
  adverts: PropertySource[];
  canonicalId: number;
  advertsFailed: boolean;
  onRetryAdverts: () => void;
}) {
  const ids = useMemo(() => adverts.map((a) => a.id), [adverts]);
  const read = useListingBrokers(ids);
  if (advertsFailed || read.isError) {
    return (
      <PhoneSection label="Makléři">
        <p className="text-[13px] text-[var(--color-brick)]">
          Makléře se nepodařilo načíst.
          <button
            type="button"
            onClick={advertsFailed ? onRetryAdverts : read.refetch}
            className="ml-2 font-medium underline underline-offset-2"
          >
            Zkusit znovu
          </button>
        </p>
      </PhoneSection>
    );
  }
  const { brokers, fromInactive } = propertyBrokers(adverts, read.brokers, canonicalId);
  if (brokers.length === 0) return null;
  return (
    <PhoneSection label="Makléři" aside={fromInactive ? 'z neaktivních inzerátů' : brokers.length}>
      <ul className="space-y-5">
        {brokers.map((b) => (
          <li key={b.broker_id}>
            <div className="flex items-center gap-3">
              <span
                aria-hidden
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-[var(--color-rule-strong)] font-display text-[15px] text-[var(--color-ink-2)]"
              >
                {initials(b.broker_display_name)}
              </span>
              <div className="min-w-0">
                <Link
                  to={ROUTES.brokerDetail.build({ id: b.broker_id })}
                  className="block truncate text-[15px] text-[var(--color-ink)]"
                >
                  {b.broker_display_name ?? 'Neznámý makléř'}
                </Link>
                <p className="truncate text-[12px] text-[var(--color-ink-3)]">
                  {b.broker_firm_label ?? 'nezávislý / neznámá kancelář'}
                </p>
              </div>
            </div>
            <BrokerContactCard broker={b} ads={b.ads} className="mt-3" />
          </li>
        ))}
      </ul>
    </PhoneSection>
  );
}

/* -------------------------------------------------------------------------- */
/* Prodané v okolí                                                             */
/* -------------------------------------------------------------------------- */

/* The rows a compact list starts with, and how many more each tap adds. */
const SOLD_FIRST = 5;
const SOLD_STEP = 20;

/* The desktop block's first view (1 km, the property's own kind) under its own
 * query key, so the two share one read; the per-m² figures are the server's
 * measure under its basis (rule 23), the median `summarizeSold`'s. Radius and
 * filters stay on the desktop. */
export function SoldSection({ property }: { property: PropertyPublic }) {
  const { lat, lng, category_main: kind } = property;
  if (lat == null || lng == null) return null;
  if (!COVERED_CATEGORIES.has(kind ?? '')) {
    return (
      <PhoneSection label="Prodané v okolí">
        <p className="text-[13px] text-[var(--color-ink-3)]">
          reas.cz zveřejňuje prodeje jen u bytů a domů.
        </p>
      </PhoneSection>
    );
  }
  return <SoldList lat={lat} lng={lng} kind={kind as string} />;
}

function SoldList({ lat, lng, kind }: { lat: number; lng: number; kind: string }) {
  const radiusM = RADII_M[0];
  const filters = useMemo(() => ({ category_main_in: [kind] }), [kind]);
  const [shown, setShown] = useState(SOLD_FIRST);
  const coverageQ = useQuery<SoldCoverage | null, Error>({
    queryKey: ['sold-coverage', lat, lng],
    queryFn: ({ signal }) => fetchSoldCoverage(lat, lng, { signal }),
    staleTime: 5 * 60_000,
  });
  const rowsQ = useQuery<SoldComparable[], Error>({
    queryKey: ['sold-comps', lat, lng, radiusM, filters],
    queryFn: ({ signal }) => fetchSoldComparables(lat, lng, radiusM, filters, { signal }),
    staleTime: 5 * 60_000,
  });

  const page = rowsQ.data ?? [];
  const truncated = page.length > SOLD_COMPS_LIMIT;
  const rows = truncated ? page.slice(0, SOLD_COMPS_LIMIT) : page;
  const summary = rowsQ.isSuccess ? summarizeSold(rows) : null;
  const coverage = coverageQ.data ?? null;
  const km = `${radiusM / 1000} km`;
  const aside = rowsQ.isSuccess && rows.length > 0 ? `${rows.length}${truncated ? '+' : ''} do ${km}` : undefined;

  let note: ReactNode = null;
  if (rowsQ.isError) {
    note = <p className="text-[13px] text-[var(--color-brick)]">Prodeje se nepodařilo načíst.</p>;
  } else if (rowsQ.isSuccess && rows.length === 0) {
    note =
      coverage?.fetched_at == null && coverage?.last_attempt_at ? (
        <p className="text-[13px] text-[var(--color-brick)]">
          Stažení prodejů pro {coverage.obec_name} selhalo — chyba je u nás, ne prázdný trh.
        </p>
      ) : coverage?.fetched_at == null && !coverageQ.isLoading ? (
        <p className="text-[13px] text-[var(--color-ink-3)]">
          {coverage ? coverage.obec_name : 'Tuto oblast'} zatím nikdo neprověřil — prodeje se stahují jen
          pro obce s živou kartou v pipeline.
        </p>
      ) : coverage?.fetched_at != null ? (
        <p className="text-[13px] text-[var(--color-ink-3)]">Do {km} žádný registrovaný prodej.</p>
      ) : null;
  }

  return (
    <PhoneSection label="Prodané v okolí" aside={aside}>
      {rowsQ.isLoading ? (
        <p className="text-[13px] text-[var(--color-ink-3)]">Načítám…</p>
      ) : note ? (
        note
      ) : (
        <>
          <ul>
            {rows.slice(0, shown).map((r, i) => (
              <SoldRow key={`${r.source}:${r.source_record_id}`} row={r} first={i === 0} />
            ))}
          </ul>
          {summary && !summary.mixedKinds && summary.medianPpm2 != null && (
            <p className="mt-2 border-t border-[var(--color-rule)] pt-2 text-[12px] text-[var(--color-ink-3)]">
              Medián z {summary.n} prodejů{' '}
              <span className="font-mono tabular-nums text-[var(--color-ink)]">
                {fmtMeasuredPricePerM2(summary.medianPpm2, summary.basis)}
              </span>
            </p>
          )}
          {rows.length > shown && (
            <button
              type="button"
              onClick={() => setShown((n) => n + SOLD_STEP)}
              className="mt-1 h-8 text-[13px] font-medium text-[var(--color-copper)]"
            >
              {rows.length - shown <= SOLD_STEP
                ? `Zobrazit všech ${rows.length}`
                : `Zobrazit dalších ${SOLD_STEP} z ${rows.length}`}
            </button>
          )}
          <p className="mt-2 text-[11px] leading-snug text-[var(--color-ink-4)]">
            Skutečné ceny z katastru přes reas.cz — vzorek, ne celý registr; prodej se objeví asi 30 dní po
            převodu.
          </p>
        </>
      )}
    </PhoneSection>
  );
}

function SoldRow({ row, first }: { row: SoldComparable; first: boolean }) {
  const ppm2 = fmtMeasuredPricePerM2(row.price_per_m2, ppm2BasisFromToken(row.price_per_m2_basis));
  const what = [
    row.disposition,
    row.area_m2 != null ? fmtArea(row.area_m2) : null,
    row.address_text,
  ].filter(Boolean);
  const sold = new Date(row.sold_at);
  return (
    <li
      className={`flex items-baseline justify-between gap-3 py-2 ${first ? 'pt-0' : 'border-t border-[var(--color-rule-soft)]'}`}
    >
      <div className="min-w-0">
        <p className="truncate text-[13px] text-[var(--color-ink-2)]">{what.join(' · ') || '—'}</p>
        <p className="text-[11px] text-[var(--color-ink-3)]">
          prodáno {Number.isNaN(sold.getTime()) ? '—' : `${sold.getMonth() + 1}/${sold.getFullYear()}`} ·{' '}
          {fmtDistanceM(row.distance_m)}
        </p>
      </div>
      <span className="shrink-0 text-right">
        <span className="block whitespace-nowrap font-mono text-[12px] tabular-nums text-[var(--color-ink)]">
          {ppm2 !== '—' ? ppm2 : fmtCzk(row.price_czk)}
        </span>
        {ppm2 !== '—' && (
          <span className="block whitespace-nowrap font-mono text-[11px] tabular-nums text-[var(--color-ink-3)]">
            {fmtCzk(row.price_czk)}
          </span>
        )}
      </span>
    </li>
  );
}
