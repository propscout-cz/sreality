/* The phone card's photo strip: the cover the Browse card would open on, then
 * the ad's other photos in portal order, at most CARD_STRIP_CAP of them.
 *
 * NO READ OF ITS OWN. The phone rolls already hydrate every photo of each card's
 * canonical ad through `useListingPhotos` (images_public, ONE request per 24-card
 * page, keyed on listing_id, ordered by sequence then id, kept up to the rolls'
 * PHOTOS_PER_CARD — the same cache entry Browse's carousel reads). The strip is
 * a projection of that, not a second, position-capped read: the cover is chosen
 * DINOv3-first over the ad's WHOLE set (`coverIndex`), so it can sit at any
 * position, and `total` is the count the card's margin already prints. A
 * `sequence <= 8` request would lose both and add a request per page. */

import { useMemo } from 'react';

import { coverIndex, type CoverTag } from '@/lib/imageTags';
import type { ImagePublic } from '@/lib/types';

import { useCardHydration } from './CardHydration';

/* Enough to see the rooms without leaving the roll; past this the strip's last
 * slide sends to the property page, which holds every photo. */
export const CARD_STRIP_CAP = 8;

export interface CardPhotos {
  cover: ImagePublic | null;
  /* The cover first, then the rest in position order; at most the cap. */
  photos: readonly ImagePublic[];
  /* Every photo the card's read holds for the ad (the margin's "N fot."). */
  total: number;
}

const NO_STRIP: CardPhotos = { cover: null, photos: [], total: 0 };

/* Position order as the read gives it, restated so the projection does not
 * lean on its caller: sequence ascending, nulls last, then id. */
const byPosition = (a: ImagePublic, b: ImagePublic): number =>
  (a.sequence ?? Number.POSITIVE_INFINITY) - (b.sequence ?? Number.POSITIVE_INFINITY) || a.id - b.id;

export function cardPhotos(
  all: readonly ImagePublic[],
  coverTag: CoverTag,
  cap: number = CARD_STRIP_CAP,
): CardPhotos {
  if (all.length === 0) return NO_STRIP;
  const ordered = [...all].sort(byPosition);
  const at = coverIndex(ordered, coverTag);
  const cover = ordered[at];
  const rest = ordered.filter((_, i) => i !== at);
  return { cover, photos: [cover, ...rest].slice(0, cap), total: ordered.length };
}

/* One card's strip from the hydration above it. `pending` is true only while
 * the first page of photos is still in flight and this ad has none yet — a
 * resolved "no photos" is an answer, not a skeleton. */
export function useCardPhotos(
  listingId: number | null | undefined,
  coverTag: CoverTag,
): CardPhotos & { pending: boolean } {
  const { photosFor, photosPending } = useCardHydration();
  const all = photosFor(listingId);
  const strip = useMemo(() => cardPhotos(all, coverTag), [all, coverTag]);
  return { ...strip, pending: photosPending && all.length === 0 };
}
