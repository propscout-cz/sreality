/* Shared "what is this property" pieces — the identity column (kind, price, the
 * place and a dense facts strip), the location map, the description and the
 * photo grid. The property page composes them into its own hero and tabs
 * (PropertyHero, PropertyTabs); the Estimation Detail page renders its subject
 * with ListingOverview below: the SAME pieces, one surface, not two. Driven by a
 * ListingPublic-shaped row: the property page passes the property as its Browse
 * card shows it, the estimation page the subject's resolved listings row. */
import { Suspense, useLayoutEffect, useRef, useState } from 'react';
import { lazyChunk } from '@/lib/lazyChunk';
import { fmtCzk, fmtArea, fmtFloor, fmtMeasuredPricePerM2, fmtAbsolute } from '@/lib/format';
import { areaKindOf, ppm2BasisFromToken } from '@/lib/measure';
import type { ImagePublic, ListingPublic } from '@/lib/types';
import { listingKindParts, priceUnitLabel } from '@/lib/enums';
import {
  buildFacts,
  buildAmenities,
  FactsList,
  AmenityChips,
} from '@/lib/listingFacts';
import { Hairline, SectionLabel } from '@/components/section';

const DetailMap = lazyChunk(() => import('@/components/listing-detail/DetailMap'));
const Gallery = lazyChunk(() => import('@/components/listing-detail/Gallery'));

/* A labelled, display-only price line beside the headline price (the property
 * page's lowest active price, MS8). */
export interface PriceNote {
  text: string;
  title: string;
}

export function ListingOverview({
  listing,
  images = [],
  imagesLoading = false,
  showStatus = true,
}: {
  listing: ListingPublic;
  images?: ImagePublic[];
  imagesLoading?: boolean;
  showStatus?: boolean;
}) {
  return (
    <>
      <div className="mt-4 grid gap-x-8 gap-y-5 lg:grid-cols-[minmax(0,1fr)_minmax(300px,400px)] items-start">
        <PropertyIdentity listing={listing} showStatus={showStatus} />
        <LocationMap listing={listing} />
      </div>
      <DescriptionBlock listing={listing} />
      <Hairline />
      <GalleryBlock
        images={images}
        isActive={listing.is_active}
        loading={imagesLoading}
      />
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Identity + map                                                              */
/* -------------------------------------------------------------------------- */

/* The identity column: kind, area and floor, the status, the headline price
 * (with the lowest-active-price note, MS8, when the page passes one), the place,
 * the id and per-m² figure, then the key facts. */
export function PropertyIdentity({
  listing,
  priceNote,
  showStatus = true,
  statusHref,
}: {
  listing: ListingPublic;
  /* Rendered under the headline price, or in its place when the listing states
   * none. Estimation Detail leaves it empty. */
  priceNote?: PriceNote;
  showStatus?: boolean;
  /* The status pill opens this advert on its portal (the property page: the
   * latest active advert's stored URL). */
  statusHref?: string | null;
}) {
  // Identity tokens, most-specific first: subtype ("Ubytování", "Rodinný dům")
  // and/or disposition ("2+kk"). Commercial/houses keep their kind instead of
  // the old bare "—"; apartments are unchanged (subtype NULL → disposition).
  const kindParts = listingKindParts(listing);
  /* A pozemek's area_m2 is its PLOT, not a floor plan — the header has room to
   * say which, so it does. */
  const area = fmtArea(listing.area_m2, areaKindOf(listing.category_main));
  const floor = fmtFloor(listing.floor, listing.total_floors);
  // A null price means the seller hid it (often quoting it in the description);
  // it's real source state, not missing data — surface it as such.
  const hasPrice = listing.price_czk != null;
  const price = hasPrice ? fmtCzk(listing.price_czk) : 'Cena na vyžádání';
  /* THE measure and THE label, both straight off listings_public (migration
   * 425) — this used to divide price by area in the component, which printed a
   * figure for rows the platform elsewhere refuses to give one, and printed it
   * with a sale unit even on a monthly rent. */
  const ppm = fmtMeasuredPricePerM2(
    listing.price_per_m2,
    ppm2BasisFromToken(listing.price_per_m2_basis),
  );
  const unit = hasPrice && listing.price_unit ? ` / ${priceUnitLabel(listing.price_unit)}` : '';
  // Only sreality carries a real portal id; other sources hold a synthetic
  // (negative) sreality_id that must never surface as an "ID".
  const hasId = listing.source === 'sreality';

  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <p className="font-mono tabular-nums text-[var(--color-ink-2)] text-sm">
          <span>{kindParts.length > 0 ? kindParts.join(' · ') : '—'}</span>
          <span className="mx-2 text-[var(--color-ink-4)]">·</span>
          <span>{area}</span>
          {floor != null && (
            <>
              <span className="mx-2 text-[var(--color-ink-4)]">·</span>
              <span className="text-[var(--color-ink)]">{floor}</span>
            </>
          )}
        </p>
        {showStatus && (
          <StatusPill isActive={listing.is_active} lastSeenAt={listing.last_seen_at} href={statusHref} />
        )}
      </div>
      <h1
        className={[
          'mt-1.5 leading-[1.05] tabular-nums',
          hasPrice ? 'text-[2.6rem]' : 'text-[1.6rem] text-[var(--color-ink-3)]',
        ].join(' ')}
        style={{ fontFamily: 'var(--font-display)', fontWeight: 600 }}
        title={!hasPrice && priceNote ? priceNote.title : undefined}
      >
        {!hasPrice && priceNote ? priceNote.text : price}
        <span className="text-base font-sans font-normal text-[var(--color-ink-3)] tracking-wide">
          {unit}
        </span>
      </h1>
      {hasPrice && priceNote && (
        <p className="mt-1 text-sm tabular-nums text-[var(--color-ink-2)]" title={priceNote.title}>
          {priceNote.text}
        </p>
      )}
      <p className="mt-2 text-sm text-[var(--color-ink-2)]">
        {listing.display_label ?? '—'}
      </p>
      {(hasId || ppm !== '—') && (
        <p className="text-[0.7rem] tracking-[0.14em] uppercase text-[var(--color-ink-4)] mt-2">
          {hasId && (
            <>
              ID{' '}
              <span className="font-mono tabular-nums text-[var(--color-ink-3)] normal-case tracking-normal">
                {listing.sreality_id}
              </span>
            </>
          )}
          {ppm !== '—' && (
            <>
              {hasId && <span className="mx-2">·</span>}
              <span className="font-mono tabular-nums text-[var(--color-ink-3)] normal-case tracking-normal">
                {ppm}
              </span>
            </>
          )}
        </p>
      )}
      <KeyFactsBlock listing={listing} />
    </div>
  );
}

/* The location map, or an explicit note where it would be — silence would read
 * as a render failure, not a data fact. `footer` renders under the map only when
 * there is one (the property page's Explore area + external map links). */
export function LocationMap({
  listing,
  heightClass = 'h-[190px]',
  footer,
}: {
  listing: ListingPublic;
  heightClass?: string;
  footer?: React.ReactNode;
}) {
  const { lat, lng } = listing;
  const hasMap = lat != null && lng != null;
  return (
    <div className="w-full">
      {hasMap ? (
        <Suspense
          fallback={
            <div className={`${heightClass} rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)]`} />
          }
        >
          <DetailMap
            lat={lat}
            lng={lng}
            isActive={listing.is_active}
            heightClass={heightClass}
          />
        </Suspense>
      ) : (
        <div className={`${heightClass} flex items-center justify-center text-sm text-[var(--color-ink-3)] border border-dashed border-[var(--color-rule)] rounded-[var(--radius-md)]`}>
          No coordinates recorded
        </div>
      )}
      {hasMap && footer && <div className="mt-2">{footer}</div>}
    </div>
  );
}

function StatusPill({
  isActive,
  lastSeenAt,
  href,
}: {
  isActive: boolean;
  lastSeenAt: string;
  href?: string | null;
}) {
  const seen = lastSeenAt ? `Last seen ${fmtAbsolute(lastSeenAt)}` : undefined;
  const className = [
    'shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1 text-[0.7rem] tracking-wide rounded-[var(--radius-sm)] border',
    isActive
      ? 'bg-[var(--color-copper-soft)] text-[var(--color-copper)] border-[var(--color-copper)]/20'
      : 'bg-[var(--color-brick-soft)] text-[var(--color-brick)] border-[var(--color-brick)]/20',
    href ? 'hover:border-current transition-colors' : '',
  ].join(' ');
  const body = (
    <>
      {isActive && <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-sage)]" aria-hidden />}
      {isActive ? 'Active' : 'Inactive'}
      {href && <span aria-hidden>↗</span>}
    </>
  );
  if (!href) {
    return (
      <span className={className} title={seen}>
        {body}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={className}
      title={[isActive ? 'Otevřít poslední aktivní inzerát' : 'Otevřít naposledy viděný inzerát', seen]
        .filter(Boolean)
        .join(' · ')}
    >
      {body}
    </a>
  );
}

/* -------------------------------------------------------------------------- */
/* Gallery                                                                    */
/* -------------------------------------------------------------------------- */

export function GalleryBlock({
  images,
  isActive,
  loading,
  label = 'Photos',
  emptyText = 'No photos recorded.',
}: {
  images: ImagePublic[];
  isActive: boolean;
  loading: boolean;
  label?: string;
  emptyText?: string;
}) {
  if (loading && images.length === 0) {
    return (
      <div>
        <SectionLabel>{label}</SectionLabel>
        <div className="mt-2 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-1.5">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="aspect-[4/3] rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)]"
            />
          ))}
        </div>
      </div>
    );
  }
  if (images.length === 0) {
    return (
      <div>
        <SectionLabel>{label}</SectionLabel>
        <p className="mt-2 text-sm text-[var(--color-ink-3)]">{emptyText}</p>
      </div>
    );
  }
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <SectionLabel>{label}</SectionLabel>
        <p className="text-[0.7rem] tracking-wide text-[var(--color-ink-4)] font-mono tabular-nums">
          {images.length}
        </p>
      </div>
      <div className="mt-3">
        <Suspense fallback={null}>
          <Gallery images={images} isActive={isActive} />
        </Suspense>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Description (original sreality "Popis" free-text)                          */
/* -------------------------------------------------------------------------- */

function DescriptionBlock({ listing }: { listing: ListingPublic }) {
  const text = listing.description?.trim() ?? '';
  if (!text) return null;
  return <DescriptionBody text={text} />;
}

export function DescriptionBody({
  text,
  label = 'Description',
  className = 'mt-7',
  clamp = 'line-clamp-4',
  words = ['Show more', 'Show less'],
}: {
  text: string;
  label?: string;
  className?: string;
  /* The collapsed height, as a Tailwind line-clamp class. */
  clamp?: string;
  words?: readonly [more: string, less: string];
}) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLParagraphElement | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setOverflows(el.scrollHeight - el.clientHeight > 1);
  }, [text, clamp]);

  return (
    <div className={className}>
      <SectionLabel>{label}</SectionLabel>
      <p
        ref={ref}
        className={
          'mt-3 text-sm leading-relaxed text-[var(--color-ink)] whitespace-pre-wrap ' +
          (expanded ? '' : clamp)
        }
      >
        {text}
      </p>
      {(overflows || expanded) && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 inline-flex items-center text-[0.75rem] tracking-wide text-[var(--color-ink-3)] hover:text-[var(--color-copper)] transition-colors"
        >
          {expanded ? words[1] : words[0]}
        </button>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Key facts                                                                  */
/* -------------------------------------------------------------------------- */

/* One dense data strip instead of the old Property/Building grids: the kind
 * (subtype and/or disposition), area, floor and district live in the header.
 * What remains — lot/garden for houses, the building facts — renders as inline
 * label·value pairs on one wrapping line, with the amenity chips below. Rendered
 * inside the header's left column, under the price, so it fills the space beside
 * the map instead of pushing the description down. */
function KeyFactsBlock({ listing }: { listing: ListingPublic }) {
  const facts = buildFacts(listing);
  const amenities = buildAmenities(listing);
  if (facts.length === 0 && amenities.length === 0) {
    return null;
  }
  return (
    <div className="mt-5 space-y-3">
      <FactsList facts={facts} />
      <AmenityChips amenities={amenities} />
    </div>
  );
}
