/* One card of the feed's roll, drawn as the W0 prototype draws it (#1762): a
 * full-width 4:3 photo strip — the 1080 px copies (#1765), opening on the cover
 * the Browse card would open on, up to eight photos a swipe apart
 * (CardPhotoStrip) — with the margin of marks over its right edge and the stage
 * wash on every photo when the property is filed; under it three lines. Line 1 the price in
 * Fraunces and the price per m² in mono WITH ITS BASIS (rule 23: the server's
 * measure and label, through lib/measure + fmtMeasuredPricePerM2, never price ÷
 * area; no figure at all on a missing or mixed basis). Line 2 layout · area ·
 * place. Line 3, muted: monochrome portal chips, how many portals, days on the
 * market, the price move, the MF yield on sale flats.
 *
 * Colour means one thing here, the stage: the delta and the MF badge are ink.
 * The text opens the property page through the price link stretched over it;
 * the photos open it slide by slide, since a link stretched over the strip
 * would swallow the swipe. The margin's controls sit beside both. */

import { Link } from 'react-router-dom';

import { czPlural, fmtArea, fmtCzk, fmtMeasuredPricePerM2, fmtTomDays } from '@/lib/format';
import { listingTypeLabel } from '@/lib/enums';
import { useCardPhotos } from '@/lib/hydration';
import type { CoverTag } from '@/lib/imageTags';
import { propertyPath } from '@/lib/listingUrl';
import { areaKindOf, plotBesideArea, ppm2BasisFromToken } from '@/lib/measure';
import { portalShort } from '@/lib/portals';
import type { CardRow } from '@/lib/queries';

import CardPhotoStrip from './CardPhotoStrip';
import MarginMarks, { usePipelineLook } from './MarginMarks';

const NBSP = ' ';
const MINUS = '−';

/* The margin column's width; the wash keeps its badge clear of it. */
const MARGIN_PX = 44;

/* The price as line 1 prints it; `perMonth` adds the muted "/měs" of a rent. */
export function feedPrice(r: Pick<CardRow, 'price_czk' | 'category_type'>): {
  text: string | null;
  perMonth: boolean;
} {
  if (r.price_czk == null) return { text: null, perMonth: false };
  return { text: fmtCzk(r.price_czk), perMonth: r.category_type === 'pronajem' };
}

/* The per-m² figure with its unit, or null when there is nothing honest to show:
 * no measure, no basis, or a basis that names no single unit. */
export function feedPricePerM2(r: Pick<CardRow, 'price_per_m2' | 'price_per_m2_basis'>): string | null {
  const basis = ppm2BasisFromToken(r.price_per_m2_basis);
  if (r.price_per_m2 == null || basis == null || basis === 'mixed') return null;
  return fmtMeasuredPricePerM2(r.price_per_m2, basis);
}

/* "2+kk", "Dům 5+1", "Pozemek" — the disposition is the noun for a flat. */
export function feedLayout(r: Pick<CardRow, 'category_main' | 'disposition' | 'subtype'>): string {
  if (r.category_main === 'byt' && r.disposition) return r.disposition;
  return [listingTypeLabel(r), r.disposition].filter(Boolean).join(' ');
}

/* Line 2: layout · area · lot · place. */
export function feedLine2(
  /* The lot is optional: the phone property page shares this line and reads
   * properties_public, which carries no plot measure. */
  r: Pick<CardRow, 'category_main' | 'disposition' | 'subtype' | 'area_m2' | 'display_label'> &
    Partial<Pick<CardRow, 'plot_area_m2'>>,
): string {
  const parts = [feedLayout(r)];
  if (r.area_m2 != null) parts.push(fmtArea(r.area_m2, areaKindOf(r.category_main)));
  const plot = plotBesideArea(r);
  if (plot != null) parts.push(fmtArea(plot, 'plot'));
  if (r.display_label) parts.push(r.display_label);
  return parts.join(' · ');
}

/* "−5 %", "+3 %", "−0,4 %": signed, whole percent from 1 % up. Null when the
 * price has fewer than two observations or never moved. */
export function feedDelta(pct: number | null | undefined): string | null {
  if (pct == null || !Number.isFinite(pct) || pct === 0) return null;
  const abs = Math.abs(pct);
  const body = abs.toLocaleString('cs-CZ', { maximumFractionDigits: abs < 1 ? 1 : 0 });
  return `${pct < 0 ? MINUS : '+'}${body}${NBSP}%`;
}

/* The portals the chips name: those with a live ad, else every portal the
 * property ever had an ad on (MS19's portal rule). */
export function feedPortals(r: Pick<CardRow, 'active_sources' | 'all_sources'>): string[] {
  return r.active_sources.length ? r.active_sources : r.all_sources;
}

export const portalsWord = (n: number): string =>
  `${n}${NBSP}${czPlural(n, 'portál', 'portály', 'portálů')}`;

/* MF gross yield, sale flats only (the column is null elsewhere; the gate says so). */
export function feedMfYield(
  r: Pick<CardRow, 'mf_gross_yield_pct' | 'category_main' | 'category_type'>,
): string | null {
  if (r.mf_gross_yield_pct == null || r.category_main !== 'byt' || r.category_type !== 'prodej') return null;
  return `MF ${r.mf_gross_yield_pct.toLocaleString('cs-CZ', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}${NBSP}%`;
}

export const feedDays = (tomDays: number | null): string | null =>
  tomDays == null || tomDays < 0 ? null : tomDays === 0 ? 'dnes' : fmtTomDays(tomDays);

export default function FeedCard({ r, coverTag }: { r: CardRow; coverTag: CoverTag }) {
  const strip = useCardPhotos(r.listing_id, coverTag);
  const { member, badge, accent } = usePipelineLook(r.property_id);
  const to = propertyPath(r.property_id, r.listing_id);

  const price = feedPrice(r);
  const ppm2 = feedPricePerM2(r);
  const line2 = feedLine2(r);
  const portals = feedPortals(r);
  const days = feedDays(r.tom_days);
  const delta = feedDelta(r.total_price_change_pct);
  const mf = feedMfYield(r);
  const priceLabel = price.text ? `${price.text}${price.perMonth ? ' /měs' : ''}` : 'Cena na vyžádání';

  return (
    <article className="relative border-t border-[var(--color-rule)] last-of-type:border-b">
      <div className="relative">
        <CardPhotoStrip
          to={to}
          photos={strip.photos}
          total={strip.total}
          pending={strip.pending}
          wash={member ? { color: accent.fg, badge, label: member.stage_label } : null}
          insetRight={MARGIN_PX}
        />
        <MarginMarks
          subject={{ property_id: r.property_id, text: line2, figure: price.text ? priceLabel : null }}
          photoCount={strip.pending ? null : strip.total}
        />
      </div>
      {/* `relative`: the price link's stretched hit area covers this text, not the strip. */}
      <div className="relative flex flex-col gap-1 px-4 pb-4 pt-3">
        <div className="flex items-baseline justify-between gap-3">
          <Link
            to={to}
            aria-label={`${priceLabel}, ${line2}`}
            className="stretched-link whitespace-nowrap font-display text-[22px] font-medium leading-[1.15] tracking-[-0.01em] text-[var(--color-ink)] [font-variant-numeric:lining-nums_tabular-nums]"
          >
            {price.text ?? <span className="font-sans text-[15px] text-[var(--color-ink-3)]">Cena na vyžádání</span>}
            {price.perMonth && (
              <span className="ml-0.5 font-sans text-[13px] font-normal tracking-normal text-[var(--color-ink-3)]">
                /měs
              </span>
            )}
          </Link>
          {ppm2 && (
            <span className="whitespace-nowrap font-mono text-[12px] tabular-nums text-[var(--color-ink-3)]">
              {ppm2}
            </span>
          )}
        </div>
        <p className="text-[14px] text-[var(--color-ink-2)]">{line2}</p>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-[var(--color-ink-3)]">
          {portals.length > 0 && (
            <span className="inline-flex gap-1">
              {portals.map((p) => (
                <span
                  key={p}
                  className="inline-flex h-5 items-center whitespace-nowrap rounded-[var(--radius-xs)] border border-[var(--color-rule-strong)] px-1 text-[11px] text-[var(--color-ink-2)]"
                >
                  {portalShort(p)}
                </span>
              ))}
            </span>
          )}
          {r.all_sources.length > 0 && <span>{portalsWord(r.all_sources.length)}</span>}
          {days && <span className="whitespace-nowrap font-mono text-[11px] tabular-nums">{days}</span>}
          {delta && (
            <span
              title="Změna oproti první zaznamenané ceně"
              className="whitespace-nowrap font-mono text-[11px] tabular-nums text-[var(--color-ink-2)]"
            >
              {delta}
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
      </div>
    </article>
  );
}
