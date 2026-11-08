/* A broker as a saved contact (lib/vcard): ONE mapping, read by the phone page's contact
 * strip and by the broker card on every surface, so a contact saved anywhere reads the
 * same. The note says which property and which ads the contact came from: what the
 * operator needs when the broker calls back. Built only when there is a number or an
 * e-mail to save; a masked contact (has_* without the value) offers nothing. */

import { dialable } from './brokers';
import { listingTypeLabel } from './enums';
import { fmtArea } from './format';
import { areaKindOf } from './measure';
import { portalBadge } from './portals';
import type { PropertyPublic } from './queries';
import { ROUTES } from './routes';
import type { PropertySource } from './types';
import type { VCardContact } from './vcard';

/* The property the contact is saved from: its address and one line naming it. */
export interface ContactAbout {
  propertyId: number;
  line: string;
}

/* "Byt 3+kk · 68 m² · Praha 5 – Smíchov": kind, area, place. No price: the note is kept
 * for good and a price moves; the property's address in the card has today's. */
export function contactAbout(
  p: Pick<PropertyPublic, 'property_id' | 'category_main' | 'subtype' | 'disposition' | 'area_m2' | 'display_label'>,
): ContactAbout {
  const kind = [listingTypeLabel(p), p.disposition].filter(Boolean).join(' ');
  const area = p.area_m2 != null ? fmtArea(p.area_m2, areaKindOf(p.category_main)) : null;
  return { propertyId: p.property_id, line: [kind, area, p.display_label?.trim()].filter(Boolean).join(' · ') };
}

export function brokerContact({
  brokerId,
  name,
  firm,
  phone,
  email,
  ads = [],
  about,
}: {
  brokerId: number;
  name: string | null;
  firm: string | null;
  phone: string | null | undefined;
  email: string | null | undefined;
  ads?: ReadonlyArray<Pick<PropertySource, 'source' | 'source_url' | 'ad_reference'>>;
  about?: ContactAbout | null;
}): VCardContact | null {
  if (!phone && !email) return null;
  const origin = window.location.origin;
  const note = [
    about ? `Nemovitost: ${about.line}` : null,
    ...ads
      .filter((ad) => ad.ad_reference)
      .map((ad) => `${portalBadge(ad.source, ad.source_url)}: č. zakázky ${ad.ad_reference}`),
  ]
    .filter(Boolean)
    .join('\n');
  return {
    uid: `${origin}${ROUTES.brokerDetail.build({ id: brokerId })}`,
    name,
    org: firm,
    title: 'Realitní makléř',
    phone: phone ? dialable(phone) : null,
    email: email || null,
    url: about ? `${origin}${ROUTES.property.build({ propertyId: about.propertyId })}` : null,
    note: note || null,
  };
}
