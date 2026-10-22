/* The Browse read surfaces' query keys — one table of roots, every reader and
 * every sweep derived from it, so a new surface cannot be read under a key the
 * merge / dismissal / pipeline sweeps never reach.
 *
 * Slot 0 is the surface, slot 1 the filters: lib/curationSettle judges a
 * curation write against `queryKey[1]`. No namespace prefix — the roots ARE the namespace, and
 * none of them may equal lib/hydration's (hydration.test.ts pins that). */

import type { ListingFilters } from '@/lib/filters';
import type { SortSpec } from '@/lib/queries';
import type { StatsSpec } from '@/lib/stats';

const root = {
  cards: 'cards',
  map: 'map',
  stats: 'stats',
  count: 'browse-count',
  noPriceCount: 'no-price-count',
} as const;

export const browseKeys = {
  cards: (f: ListingFilters, s: SortSpec) => [root.cards, f, s] as const,
  /* The pin limit is the server's point budget, so it decides the answer's SHAPE
   * (points or counts) and must be in the key. */
  map: (f: ListingFilters, pinLimit: number) => [root.map, f, pinLimit] as const,
  /* One Stats chart's pivot: the cohort AND the chart's spec (migration 626). */
  stats: (f: ListingFilters, spec: StatsSpec) => [root.stats, f, spec] as const,
  /* The cohort's resolved prefilter ids, shared by every chart of one filter set
   * (lib/stats fetchStatsPivot); under the stats root so the same sweeps reach it. */
  statsPrefilters: (f: ListingFilters) => [root.stats, f, 'prefilters'] as const,
  /** Every Stats read (each chart's pivot and the shared prefilters): the tab's spinner. */
  statsAll: [root.stats] as const,
  count: (f: ListingFilters) => [root.count, f] as const,
  noPriceCount: (f: ListingFilters) => [root.noPriceCount, f] as const,
  /** The paged lists: patched in place by a curation settle, never refetched for one. */
  lists: [[root.cards]] as const,
  /** Every other read of the browse read model. */
  aggregates: [[root.map], [root.stats], [root.count], [root.noPriceCount]] as const,
  /** Every Browse read surface: what a merge / split / link / restore sweeps. */
  all: Object.values(root).map((r) => [r] as const),
};
