/* The property page's first tab, Přehled (operator ruling 2026-10-22, design A):
 * one card per question, each with the answer and a way into its tab — the
 * description, then the brokers and how to reach them; is the price right; how
 * the price moved; where it is advertised; what flats nearby actually sold for.
 *
 * Every figure here is read from the same query, with the same rule, as the tab
 * it summarises, so the two can never disagree: our estimate is the estimations
 * block's latest successful run of the property's own deal type, Valuo's is its
 * latest answer of that type, the sold median is lib/soldComps' over the block's
 * default radius, and every per-m² figure carries its basis (rule 23). Nothing is
 * computed here that the tab does not already show. */
import { useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { FilterState } from '@/components/FilterForm';
import Skeleton from '@/components/Skeleton';
import Stat from '@/components/Stat';
import { SectionLabel } from '@/components/section';
import RangeStrip from '@/components/estimation/RangeStrip';
import { timeLabelFull } from '@/lib/chartAxis';
import {
  czPlural,
  fmtCount,
  fmtCzk,
  fmtMeasuredPricePerM2,
  fmtPct,
  fmtRelative,
  fmtShortDate,
  fmtTomDays,
} from '@/lib/format';
import { ppm2BasisFromToken } from '@/lib/measure';
import { mfShape } from '@/lib/mfReference';
import { portalLabel } from '@/lib/portals';
import { buildPriceSeries, priceChangeEvents, type PriceAdvert } from '@/lib/priceHistory';
import {
  fetchSoldComparables,
  SOLD_COMPS_LIMIT,
  useEstimationRuns,
  type PropertyPublic,
} from '@/lib/queries';
import { COVERED_CATEGORIES, RADII_M, summarizeSold } from '@/lib/soldComps';
import type { ListingSnapshotPublic, PropertySource, SoldComparable } from '@/lib/types';
import { band, shownPrice, useValuoPage } from '@/lib/valuo';
import BrokerList from './BrokerList';
import { DescriptionBody } from './ListingOverview';
import type { PropertyTabId } from './PropertyTabs';

const CARD =
  'min-w-0 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-5 pt-4 pb-5';
const WIDE = 'flex-[2_1_32rem]';
const NARROW = 'flex-[1_1_18rem]';

export interface LowestActive {
  price: number;
  portals: string;
}

export default function PropertyOverview({
  property,
  sources,
  sourcesFailed,
  onRetrySources,
  advertIds,
  chartAdverts,
  snapshots,
  lowest,
  onOpen,
}: {
  property: PropertyPublic;
  sources: PropertySource[];
  sourcesFailed: boolean;
  onRetrySources: () => void;
  advertIds: number[];
  chartAdverts: PriceAdvert[];
  snapshots: ListingSnapshotPublic[];
  /* The lowest active price when it differs from the header's (MS8). */
  lowest: LowestActive | null;
  onOpen: (tab: PropertyTabId) => void;
}) {
  const description = property.description?.trim() ?? '';
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-4">
        {description && (
          <div className={`${CARD} ${WIDE}`}>
            <DescriptionBody
              text={description}
              label="Popis"
              className=""
              clamp="line-clamp-6"
              words={['Celý popis', 'Méně']}
            />
          </div>
        )}
        <BrokerList
          adverts={sources}
          canonicalId={property.id}
          advertsFailed={sourcesFailed}
          onRetryAdverts={onRetrySources}
          className={`${CARD} ${NARROW}`}
        />
      </div>
      <div className="flex flex-wrap items-stretch gap-4">
        <ValueCard property={property} advertIds={advertIds} lowest={lowest} onOpen={() => onOpen('hodnota')} />
        <PriceCard property={property} adverts={chartAdverts} snapshots={snapshots} onOpen={() => onOpen('hodnota')} />
      </div>
      <div className="flex flex-wrap items-stretch gap-4">
        <AdvertsCard
          sources={sources}
          canonicalId={property.id}
          failed={sourcesFailed}
          onRetry={onRetrySources}
          onOpen={() => onOpen('inzeraty')}
        />
        <MarketCard property={property} onOpen={() => onOpen('trh')} />
      </div>
    </div>
  );
}

function Card({
  title,
  action,
  onAction,
  className,
  children,
}: {
  title: string;
  action: string;
  onAction: () => void;
  className: string;
  children: ReactNode;
}) {
  return (
    <section aria-label={title} className={`${CARD} ${className}`}>
      <div className="flex items-baseline justify-between gap-3">
        <SectionLabel>{title}</SectionLabel>
        <button
          type="button"
          onClick={onAction}
          className="shrink-0 text-[0.8rem] text-[var(--color-copper)] hover:underline underline-offset-2"
        >
          {action} →
        </button>
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Is the price right                                                         */
/* -------------------------------------------------------------------------- */

/* The ask against our estimate's p25–p75 band on the valuation ruler (system.md),
 * Valuo's figure and the lowest active ask as ticks; with no run of the deal's
 * type, against Valuo's own band. Like with like only: a sale property reads sale
 * estimates, a rental rent ones. */
function ValueCard({
  property,
  advertIds,
  lowest,
  onOpen,
}: {
  property: PropertyPublic;
  advertIds: number[];
  lowest: LowestActive | null;
  onOpen: () => void;
}) {
  const lease = property.category_type === 'pronajem';
  const runsQ = useEstimationRuns(advertIds);
  const valuoQ = useValuoPage(property.property_id);
  const runs = runsQ.data?.data ?? [];

  const ours = runs.find((r) => r.status === 'success' && (r.estimate_kind ?? 'rent') === (lease ? 'rent' : 'sale')) ?? null;
  const oursValue = ours ? (lease ? ours.estimated_monthly_rent_czk : ours.estimated_sale_price_czk) : null;
  const [p25, p75] = ours ? (lease ? [ours.rent_p25_czk, ours.rent_p75_czk] : [ours.sale_p25_czk, ours.sale_p75_czk]) : [null, null];
  const inFlight = runs.some((r) => r.status === 'pending' || r.status === 'running');

  const valuation =
    (valuoQ.data?.history ?? []).find(
      (v) => v.result?.avg_price != null && (ppm2BasisFromToken(v.ppm2_basis) === 'rent') === lease,
    ) ?? null;
  const valuoValue = valuation?.result?.avg_price != null ? shownPrice(valuation.result.avg_price, lease) : null;
  const valuoBand = valuation ? band(valuation, 'range_price') : null;

  const ask = property.price_czk;
  const perMonth = lease ? ' / měs' : '';
  const marks = (extra: { label: string; value: number | null }[]) =>
    [
      { label: 'nabídka', value: ask },
      { label: 'nejnižší aktivní', value: lowest?.price ?? null },
      ...extra,
    ].filter((m): m is { label: string; value: number } => m.value != null);

  let strip: ReactNode = null;
  if (oursValue != null && p25 != null && p75 != null) {
    strip = (
      <RangeStrip
        range={{ low: p25, mid: oursValue, high: p75 }}
        caption="p25 · náš odhad · p75"
        format={fmtCzk}
        marks={marks([{ label: 'Valuo', value: valuoValue }])}
      />
    );
  } else if (valuoValue != null && valuoBand) {
    strip = (
      <RangeStrip
        range={{ low: shownPrice(valuoBand[0], lease), mid: valuoValue, high: shownPrice(valuoBand[1], lease) }}
        caption="pásmo spolehlivosti (Valuo)"
        format={fmtCzk}
        marks={marks([])}
      />
    );
  }

  /* Against the exact figure, never the rounded one on screen. */
  const exactRef = oursValue ?? valuation?.result?.avg_price ?? null;
  const delta = ask != null && exactRef ? ((ask - exactRef) / exactRef) * 100 : null;
  const verdict =
    delta != null
      ? `Nabídka je o ${fmtPct(Math.abs(delta))} ${delta >= 0 ? 'nad' : 'pod'} ${oursValue != null ? 'naším odhadem' : 'průměrem Valuo'}.`
      : ask == null
        ? 'Cena je na vyžádání — není s čím srovnávat.'
        : runsQ.isLoading || valuoQ.isLoading
          ? null
          : 'Zatím nemáme vlastní odhad ani ocenění Valuo.';

  const mf = mfShape(property.mf_reference_rent);
  const yieldHint = property.mf_gross_yield_pct != null ? `hrubý výnos ${fmtPct(property.mf_gross_yield_pct)}` : undefined;

  return (
    <Card title="Je cena v pořádku?" action="Cena a hodnota" onAction={onOpen} className={WIDE}>
      {verdict && <p className="text-[0.95rem] text-[var(--color-ink)]">{verdict}</p>}
      {strip && <div className="mt-4">{strip}</div>}
      <div className="mt-4 grid grid-cols-[repeat(auto-fit,minmax(10rem,1fr))] gap-2">
        <Stat
          label="Náš odhad"
          value={oursValue != null ? fmtCzk(oursValue) : '—'}
          hint={
            ours
              ? `${perMonth ? 'měsíčně · ' : ''}běh #${ours.id} · ${fmtRelative(ours.created_at)}`
              : runsQ.isError
                ? 'nepodařilo se načíst'
                : runsQ.isLoading
                  ? 'načítám…'
                  : inFlight
                    ? 'odhaduje se…'
                    : 'zatím žádný odhad'
          }
        />
        <Stat
          label="Valuo"
          value={valuoValue != null ? fmtCzk(valuoValue) : '—'}
          hint={
            valuation
              ? `${perMonth ? 'měsíčně · ' : ''}${fmtShortDate(valuation.created_at)}`
              : valuoQ.isError
                ? 'nepodařilo se načíst'
                : valuoQ.isLoading
                  ? 'načítám…'
                  : 'zatím bez ocenění'
          }
        />
        {mf.kind !== 'none' && (
          <Stat
            label="Cenová mapa MF"
            value={
              mf.kind === 'value'
                ? fmtCzk(mf.ref.monthly_rent_czk)
                : mf.kind === 'range'
                  ? `${fmtCount(mf.range.rent_min_czk)}–${fmtCzk(mf.range.rent_max_czk)}`
                  : '—'
            }
            hint={mf.kind === 'note' ? mf.note : ['nájem měsíčně', yieldHint].filter(Boolean).join(' · ')}
          />
        )}
      </div>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* How the price moved                                                        */
/* -------------------------------------------------------------------------- */

const MOVES_SHOWN = 3;

function PriceCard({
  property,
  adverts,
  snapshots,
  onOpen,
}: {
  property: PropertyPublic;
  adverts: PriceAdvert[];
  snapshots: ListingSnapshotPublic[];
  onOpen: () => void;
}) {
  // Captured once (PriceHistoryBlock's #310 note): a per-render `now` re-derives the series.
  const [now] = useState(() => Date.now());
  const changes = useMemo(
    () => priceChangeEvents(buildPriceSeries(adverts, snapshots, now)),
    [adverts, snapshots, now],
  );
  return (
    <Card title="Vývoj ceny" action="Graf" onAction={onOpen} className={NARROW}>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Na trhu" value={fmtTomDays(property.tom_days)} />
        <Stat label="Změny ceny" value={String(property.price_change_count ?? 0)} />
        <Stat label="Celkem" value={fmtPct(property.total_price_change_pct, { signed: true })} />
      </div>
      {changes.length > 0 && (
        <ul aria-label="Poslední změny ceny" className="mt-3 divide-y divide-[var(--color-rule-soft)]">
          {changes.slice(0, MOVES_SHOWN).map((c) => (
            <li key={`${c.seriesId}-${c.t}`} className="flex flex-wrap items-baseline justify-between gap-x-3 py-1.5 text-[0.8rem]">
              <span className="text-[var(--color-ink-2)]">
                {c.label}{' '}
                <span className="font-mono tabular-nums text-[var(--color-ink-3)]">{timeLabelFull(c.t, 'day')}</span>
              </span>
              <span className="font-mono tabular-nums text-[var(--color-ink)]">
                {fmtCzk(c.to)} <span className="text-[var(--color-ink-3)]">{c.pct < 0 ? '↓' : '↑'} {fmtPct(c.pct, { signed: true })}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Where it is advertised                                                     */
/* -------------------------------------------------------------------------- */

function AdvertsCard({
  sources,
  canonicalId,
  failed,
  onRetry,
  onOpen,
}: {
  sources: PropertySource[];
  canonicalId: number;
  failed: boolean;
  onRetry: () => void;
  onOpen: () => void;
}) {
  const ordered = useMemo(
    () =>
      [...sources].sort(
        (a, b) =>
          Number(b.id === canonicalId) - Number(a.id === canonicalId) || Number(b.is_active) - Number(a.is_active),
      ),
    [sources, canonicalId],
  );
  return (
    <Card title={`Inzeráty · ${sources.length}`} action="Všechny inzeráty" onAction={onOpen} className={NARROW}>
      {failed ? (
        <p className="text-sm text-[var(--color-brick)]">
          Inzeráty se nepodařilo načíst
          <button type="button" onClick={onRetry} className="ml-3 font-medium underline underline-offset-2 hover:no-underline">
            Zkusit znovu
          </button>
        </p>
      ) : (
        <ul className="divide-y divide-[var(--color-rule-soft)]">
          {ordered.map((s) => {
            const portal = portalLabel(s.source) ?? s.source;
            return (
              <li key={s.id} className="flex items-center gap-3 py-2 text-sm">
                <span
                  aria-hidden
                  className={`h-2 w-2 shrink-0 rounded-full ${s.is_active ? 'bg-[var(--color-sage)]' : 'border-[1.5px] border-[var(--color-ink-3)]'}`}
                />
                <span className={`min-w-0 flex-1 truncate ${s.is_active ? 'text-[var(--color-ink)]' : 'text-[var(--color-ink-3)]'}`}>
                  {portal}
                  {s.id === canonicalId && (
                    <span className="ml-2 text-[0.6rem] uppercase tracking-[0.16em] text-[var(--color-ink-3)]">hlavní</span>
                  )}
                  {!s.is_active && <span className="ml-2 text-[0.75rem]">neaktivní</span>}
                </span>
                <span className={`font-mono tabular-nums ${s.is_active ? 'text-[var(--color-ink)]' : 'text-[var(--color-ink-3)]'}`}>
                  {fmtCzk(s.price_czk)}
                </span>
                {s.source_url ? (
                  <a
                    href={s.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`Inzerát na webu ${portal}`}
                    className="p-1 text-[var(--color-ink-3)] hover:text-[var(--color-copper)]"
                  >
                    ↗
                  </a>
                ) : (
                  <span className="w-5" aria-hidden />
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* What it sells for nearby                                                   */
/* -------------------------------------------------------------------------- */

/* The registered-sales block's own default read (its radius, the subject's own
 * kind), so opening the tab is a cache hit and the median is the one it shows. */
function MarketCard({ property, onOpen }: { property: PropertyPublic; onOpen: () => void }) {
  const { lat, lng, category_main } = property;
  const covered = COVERED_CATEGORIES.has(category_main ?? '');
  const ready = lat != null && lng != null && covered;
  const radiusM = RADII_M[0];
  const filters = useMemo<FilterState>(() => ({ category_main_in: [category_main as string] }), [category_main]);
  const rowsQ = useQuery<SoldComparable[], Error>({
    queryKey: ['sold-comps', lat, lng, radiusM, filters],
    queryFn: ({ signal }) => fetchSoldComparables(lat as number, lng as number, radiusM, filters, { signal }),
    staleTime: 5 * 60_000,
    enabled: ready,
  });
  const rows = (rowsQ.data ?? []).slice(0, SOLD_COMPS_LIMIT);
  const summary = rowsQ.isSuccess ? summarizeSold(rows) : null;
  const km = `${radiusM / 1000} km`;
  const ownBasis = ppm2BasisFromToken(property.price_per_m2_basis);

  let body: ReactNode;
  if (lat == null || lng == null) body = <Quiet>Bez souřadnic nelze hledat prodeje v okolí.</Quiet>;
  else if (!covered) body = <Quiet>reas.cz zveřejňuje registrované prodeje jen bytů a domů.</Quiet>;
  else if (rowsQ.isLoading) body = <Skeleton height={64} />;
  else if (rowsQ.isError) {
    body = (
      <p className="text-sm text-[var(--color-brick)]">
        Prodeje se nepodařilo načíst
        <button type="button" onClick={() => void rowsQ.refetch()} className="ml-3 font-medium underline underline-offset-2 hover:no-underline">
          Zkusit znovu
        </button>
      </p>
    );
  } else if (rows.length === 0) body = <Quiet>Do {km} zatím nemáme žádný registrovaný prodej.</Quiet>;
  else if (summary == null || summary.mixedKinds || summary.medianPpm2 == null) {
    body = (
      <Quiet>
        {rows.length} {czPlural(rows.length, 'registrovaný prodej', 'registrované prodeje', 'registrovaných prodejů')} do {km} —
        medián z nich nepočítáme, podrobnosti v kartě.
      </Quiet>
    );
  } else {
    body = (
      <>
        <p className="font-display text-[1.6rem] leading-none tabular-nums text-[var(--color-ink)]">
          {fmtMeasuredPricePerM2(summary.medianPpm2, summary.basis)}
        </p>
        <p className="mt-2 text-[0.8rem] text-[var(--color-ink-2)]">
          medián {summary.n} {czPlural(summary.n, 'registrovaného prodeje', 'registrovaných prodejů', 'registrovaných prodejů')} do {km}
        </p>
        {ownBasis === summary.basis && property.price_per_m2 != null && (
          <p className="mt-1 text-[0.8rem] text-[var(--color-ink-2)]">
            tato nabídka{' '}
            <span className="font-mono tabular-nums text-[var(--color-ink)]">
              {fmtMeasuredPricePerM2(property.price_per_m2, ownBasis)}
            </span>
          </p>
        )}
      </>
    );
  }
  return (
    <Card title="Trh v okolí" action="Prodeje" onAction={onOpen} className={NARROW}>
      {body}
      <p className="mt-3 text-[0.7rem] text-[var(--color-ink-3)]">
        Jediné skutečně zaplacené ceny na stránce · katastr přes reas.cz
      </p>
    </Card>
  );
}

const Quiet = ({ children }: { children: ReactNode }) => (
  <p className="text-sm text-[var(--color-ink-3)]">{children}</p>
);
