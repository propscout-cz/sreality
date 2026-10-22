/* The property page on the phone (#1767; Q10 of #1760: every non-map section as
 * reading plus curation, no estimation runs from the phone), drawn as the W0
 * prototype's "Nemovitost" screen (#1762): the shell's header with the back
 * affordance and the property's number; the canonical ad's photos as a swiper
 * (bare — the stage is the curation row's); the price in Fraunces with its move and its
 * per-m² under its basis (rule 23, through lib/measure — never price ÷ area); the
 * curation row; then hairline-separated sections, Poloha first (a streamed Mapy
 * static image through the API, never a WebGL map on the phone).
 *
 * PropertyDetail owns every read and hands them down, so the phone and the
 * desktop page are two drawings of ONE set of queries. Out on the phone: the
 * header map (Poloha's static image stands in), Explore area, the comparables map (RunPanel skips it under the
 * phone shell) and the new-estimation / re-run buttons. */

import { Suspense, useMemo, useState } from 'react';

import Skeleton from '@/components/Skeleton';
import FreshnessBlock from '@/components/listing-detail/FreshnessBlock';
import type { PriceNote } from '@/components/listing-detail/ListingOverview';
import PriceHistoryBlock from '@/components/listing-detail/PriceHistoryBlock';
import DownloadPropertyButton from '@/components/listing-detail/DownloadPropertyButton';
import { fmtShortDate, fmtTomDays } from '@/lib/format';
import { lazyChunk } from '@/lib/lazyChunk';
import { buildPriceSeries, priceChangeEvents, type PriceAdvert } from '@/lib/priceHistory';
import type { PropertyPublic } from '@/lib/queries';
import type {
  ImagePublic,
  ListingFreshnessCheckPublic,
  ListingPublic,
  ListingSnapshotPublic,
  PropertySource,
} from '@/lib/types';

import { NO_FOCUS_ZOOM } from './chips';
import CurationRow from './CurationRow';
import {
  feedDelta,
  feedLine2,
  feedMfYield,
  feedPrice,
  feedPricePerM2,
  portalsWord,
} from './FeedCard';
import { usePhoneHeaderAside } from './PhoneShell';
import PropertyGallery from './PropertyGallery';
import {
  AdsSection,
  BrokersSection,
  DescriptionSection,
  FactsSection,
  LocationSection,
  SoldSection,
} from './PropertySections';

const EstimationsBlock = lazyChunk(() => import('@/components/listing-detail/EstimationsBlock'));
const ValuoSection = lazyChunk(() => import('@/components/listing-detail/ValuoSection'));
const PhoneTagsAndNotes = lazyChunk(() =>
  import('@/components/listing-detail/CurationBlock').then((m) => ({ default: m.PhoneTagsAndNotes })),
);

export const NOTES_SECTION_ID = 'poznamky';

/* A reused block's frame: the block brings its own label; an empty block (it
 * rendered nothing) takes no room and draws no rule. */
const BLOCK = 'min-w-0 px-4 py-4 empty:hidden';


/* "Na trhu 12 dní · 3 portály · cena snížena 7. 10." */
export function metaLine({
  property,
  portals,
  lastMove,
}: {
  property: PropertyPublic;
  portals: number;
  lastMove: { t: number; pct: number } | null;
}): string {
  const parts: string[] = [];
  if (!property.is_active) parts.push(`staženo ${fmtShortDate(property.last_seen_at)}`);
  if (property.tom_days != null && property.tom_days >= 0) {
    parts.push(property.tom_days === 0 ? 'nová dnes' : `na trhu ${fmtTomDays(property.tom_days)}`);
  }
  if (portals > 0) parts.push(portalsWord(portals));
  if (lastMove) {
    parts.push(`cena ${lastMove.pct < 0 ? 'snížena' : 'zvýšena'} ${fmtShortDate(new Date(lastMove.t).toISOString())}`);
  }
  const line = parts.join(' · ');
  return line.charAt(0).toUpperCase() + line.slice(1);
}

export interface PhonePropertyProps {
  property: PropertyPublic;
  sources: PropertySource[];
  sourcesFailed: boolean;
  onRetrySources: () => void;
  /* Each ad's listings_public row (its deal type), the merged-adverts read. */
  adDetails: Map<number, ListingPublic> | undefined;
  images: ImagePublic[];
  imagesLoading: boolean;
  chartAdverts: PriceAdvert[];
  snapshots: ListingSnapshotPublic[];
  checks: ListingFreshnessCheckPublic[];
  priceNote: PriceNote | undefined;
  advertIds: number[];
  /* `?advert=` — the ad a pasted link or an old advert address named. */
  openedAdvertId: number | null;
}

export default function PhoneProperty({
  property,
  sources,
  sourcesFailed,
  onRetrySources,
  adDetails,
  images,
  imagesLoading,
  chartAdverts,
  snapshots,
  checks,
  priceNote,
  advertIds,
  openedAdvertId,
}: PhonePropertyProps) {
  const propertyId = property.property_id;
  usePhoneHeaderAside(`#${propertyId}`);

  /* The latest move of any ad's own price — the chart's events, newest first. */
  const [now] = useState(() => Date.now());
  const lastMove = useMemo(
    () => priceChangeEvents(buildPriceSeries(chartAdverts, snapshots, now))[0] ?? null,
    [chartAdverts, snapshots, now],
  );

  const price = feedPrice(property);
  const ppm2 = feedPricePerM2(property);
  const delta = feedDelta(property.total_price_change_pct);
  const mf = feedMfYield(property);
  const line2 = feedLine2(property);
  const portals = sources.length > 0 ? new Set(sources.map((s) => s.source)).size : 1;
  const priceText = price.text ? `${price.text}${price.perMonth ? ' /měs' : ''}` : null;

  const toNotes = () => {
    const section = document.getElementById(NOTES_SECTION_ID);
    section?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    section?.querySelector('textarea')?.focus({ preventScroll: true });
  };

  return (
    <article aria-label="Nemovitost" className={`pb-6 ${NO_FOCUS_ZOOM}`}>
      <PropertyGallery images={images} loading={imagesLoading} isActive={property.is_active} />

      <header className="flex flex-col gap-1 px-4 pb-4 pt-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <p className="whitespace-nowrap font-display text-[32px] font-medium leading-[1.1] tracking-[-0.02em] text-[var(--color-ink)] [font-variant-numeric:lining-nums_tabular-nums]">
            {price.text ?? (
              <span className="font-sans text-[20px] text-[var(--color-ink-3)]">
                {priceNote ? priceNote.text : 'Cena na vyžádání'}
              </span>
            )}
            {price.perMonth && (
              <span className="ml-1 font-sans text-[15px] font-normal tracking-normal text-[var(--color-ink-3)]">
                /měs
              </span>
            )}
          </p>
          {delta && (
            <span
              title="Změna oproti první zaznamenané ceně"
              className="whitespace-nowrap font-mono text-[12px] tabular-nums text-[var(--color-ink-2)]"
            >
              {delta}
            </span>
          )}
        </div>
        {(ppm2 || mf) && (
          <div className="flex items-center gap-2">
            {ppm2 && (
              <span className="whitespace-nowrap font-mono text-[13px] tabular-nums text-[var(--color-ink-3)]">
                {ppm2}
              </span>
            )}
            {mf && (
              <span
                title="Hrubý výnos dle cenové mapy nájemného MF (nájem ÷ cena)"
                className="inline-flex h-5 items-center whitespace-nowrap rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1 font-mono text-[11px] tabular-nums text-[var(--color-ink-2)]"
              >
                {mf}
              </span>
            )}
          </div>
        )}
        {price.text && priceNote && (
          <p title={priceNote.title} className="text-[12px] text-[var(--color-ink-3)]">
            {priceNote.text}
          </p>
        )}
        <p className="mt-1 text-[15px] text-[var(--color-ink)]">{line2}</p>
        <p className="text-[12px] text-[var(--color-ink-3)]">{metaLine({ property, portals, lastMove })}</p>
      </header>

      <CurationRow subject={{ property_id: propertyId, text: line2, figure: priceText }} onNote={toNotes} />

      <div className="divide-y divide-[var(--color-rule)] border-t border-[var(--color-rule)]">
        <LocationSection propertyId={propertyId} />
        <FactsSection property={property} />
        <DescriptionSection text={property.description} />
        <AdsSection
          sources={sources}
          failed={sourcesFailed}
          onRetry={onRetrySources}
          canonicalId={property.id}
          openedId={openedAdvertId}
          details={adDetails}
        />
        <div className={BLOCK}>
          <PriceHistoryBlock property={property} adverts={chartAdverts} snapshots={snapshots} label="Vývoj ceny" />
        </div>
        <BrokersSection
          adverts={sources}
          canonicalId={property.id}
          advertsFailed={sourcesFailed}
          onRetryAdverts={onRetrySources}
        />
        <SoldSection property={property} />
        <div className={BLOCK}>
          <Suspense fallback={<Skeleton height={140} />}>
            <EstimationsBlock listing={property} listingIds={advertIds} readOnly bare label="Odhady" />
          </Suspense>
        </div>
        <div className={BLOCK}>
          {/* No placeholder: most properties hold no Valuo answer, so the block takes no room. */}
          <Suspense fallback={null}>
            <ValuoSection property={property} listingIds={advertIds} readOnly bare />
          </Suspense>
        </div>
        {property.sreality_id != null && (
          <div className={BLOCK}>
            <FreshnessBlock sreality_id={property.sreality_id} checks={checks} label="Čerstvost" />
          </div>
        )}
        <section id={NOTES_SECTION_ID} aria-label="Štítky a poznámky" className={`scroll-mt-16 ${BLOCK}`}>
          <Suspense fallback={<Skeleton height={140} />}>
            <PhoneTagsAndNotes property_id={propertyId} listing_id={property.id} />
          </Suspense>
        </section>
        <div className="px-4 py-4">
          <DownloadPropertyButton propertyId={propertyId} images={images} variant="phone" />
        </div>
      </div>
    </article>
  );
}
