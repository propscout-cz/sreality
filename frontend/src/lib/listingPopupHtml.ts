/* A Browse pin's popup, as the HTML string MapLibre takes (ListingMap). Its own
 * module, free of MapLibre, so what the popup says is testable. The classes are
 * styled in globals.css (`.lp-*`). */
import { escapeHtml } from './escapeHtml';
import type { NewTabProps } from './linkGestures';
import { listingKindLabel } from './enums';
import { fmtAbsolute, fmtArea, fmtCzk, fmtMeasuredPricePerM2, fmtRelativeCs } from './format';
import { propertyPath } from './listingUrl';
import { areaKindOf, ppm2BasisFromToken } from './measure';
import type { MapRow } from './queries';
import { pinPrecisionLabel, pinPrecisionShort } from './uncertaintyCircle';

export function listingPopupHtml(r: MapRow, newTab: NewTabProps = {}): string {
  const price = fmtCzk(r.price_czk);
  /* The popup has room, so it says what the m² MEASURES: a pozemek's area_m2 is
   * its plot, not a floor plan (the Option-A fork keeps the column polymorphic). */
  const area = fmtArea(r.area_m2, areaKindOf(r.category_main));
  /* The server measure with its own basis suffix — the popup is where a pin's
   * label can be read next to the figure it was derived from. */
  const ppm = fmtMeasuredPricePerM2(
    r.price_per_m2,
    ppm2BasisFromToken(r.price_per_m2_basis),
  );
  const disposition = listingKindLabel(r) ?? '—';
  /* The ONE label (migration 503). `lp-district` stays the style hook -- it is a
   * class name in globals.css, not a claim about what the string is. */
  const place = r.display_label ?? '';
  /* Directly under the place it qualifies, one line with when the ad was last
   * seen (ruling P13): "Přesná poloha · viděno před 2 h". Each half's detail is
   * its tooltip — the rung and the radius the circle drawn behind this popup
   * shows (the TRUE one where that circle is capped), and the exact time. */
  const precision = pinPrecisionShort(r);
  const precisionDetail = pinPrecisionLabel(r);
  const seen = fmtRelativeCs(r.last_seen_at);
  const seenAbs = fmtAbsolute(r.last_seen_at);
  const inactive = !r.is_active;
  return `
    <div class="lp">
      <div class="lp-row">
        <p class="lp-price">${escapeHtml(price)}</p>
        ${inactive ? '<span class="lp-inactive">Inactive</span>' : ''}
      </div>
      <p class="lp-meta">
        <span class="lp-mono">${escapeHtml(disposition)}</span>
        <span class="lp-sep">·</span>
        <span class="lp-mono">${escapeHtml(area)}</span>
        ${ppm === '—' ? '' : `<span class="lp-sep">·</span><span class="lp-mono">${escapeHtml(ppm)}</span>`}
      </p>
      ${place ? `<p class="lp-district">${escapeHtml(place)}</p>` : ''}
      <p class="lp-precision">
        <span title="${escapeHtml(precisionDetail)}">${escapeHtml(precision)}</span>
        <span class="lp-sep">·</span>
        <span title="${escapeHtml(seenAbs)}">viděno <span class="lp-mono">${escapeHtml(seen)}</span></span>
      </p>
      <a href="${propertyPath(r.property_id, r.listing_id)}"${
        newTab.target ? ` target="${newTab.target}" rel="${newTab.rel}"` : ''
      } class="lp-link">View details →</a>
    </div>
  `;
}
