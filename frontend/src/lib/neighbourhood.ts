/* ONE definition of "this property's neighbourhood" (operator 2026-10-23): the
 * property page's "Trh v okolí" tile counts it, and its "Prozkoumat okolí" opens
 * the explore window over exactly the same filter, so the glance and the deep
 * look always start from one sample.
 *
 * Taken from the property: its type (category_main), its deal (category_type),
 * its disposition and its ownership; a value the property does not state is left
 * unset and named in `dropped`, never matched as "unknown". Set by the user: the
 * radius (Browse's centre + radius, the square every Browse reader filters on —
 * lib/filters.effectiveBbox), the building material and the condition. Every
 * other filter is unset. The lens is the Stats tab's (lib/stats.marketSample). */
import {
  DEFAULT_FILTERS,
  OWNERSHIP_CANONICAL,
  type BuildingMaterial,
  type CategoryMain,
  type CategoryType,
  type ListingFilters,
} from '@/lib/filters';
import { FILTER_REGISTRY } from '@/lib/filterRegistry.generated';
import type { Disposition, ListingPublic } from '@/lib/types';

export const RADII_M = [250, 500, 1000, 2000] as const;
export type RadiusM = (typeof RADII_M)[number];
export type NeighbourhoodMarket = 'sold' | 'all';

export interface NeighbourhoodScope {
  radiusM: RadiusM;
  market: NeighbourhoodMarket;
  buildingMaterial: BuildingMaterial[];
  conditionMatch: string[];
}

export const DEFAULT_SCOPE: NeighbourhoodScope = {
  radiusM: 500,
  market: 'sold',
  buildingMaterial: [],
  conditionMatch: [],
};

/* The dashboard the property page's "Okolí dashboard" tab draws: the account's ONE
 * dashboard marked so (migration 664), under any name; Browse › Statistiky creates one
 * as "Okolí dashboard" or makes any own dashboard it (docs/design/saved-dashboards.md). */
export const PROPERTY_CARD_LABEL = 'Okolí dashboard';
export const isPropertyCard = (d: { owner_email: string | null; property_card?: boolean }): boolean =>
  d.owner_email == null && d.property_card === true;

const registryValues = (id: string): ReadonlySet<string> =>
  new Set((FILTER_REGISTRY.filters.find((f) => f.id === id)?.enum_values ?? []).map((o) => String(o.value)));
const CATEGORY_MAINS: ReadonlySet<string> = new Set(['byt', 'dum', 'komercni', 'pozemek', 'ostatni']);
const CATEGORY_TYPES: ReadonlySet<string> = new Set(['pronajem', 'prodej', 'drazba', 'podil']);
const DISPOSITIONS = registryValues('dispositions');
const OWNERSHIPS: ReadonlySet<string> = new Set(OWNERSHIP_CANONICAL);

export type NeighbourhoodProperty = Pick<
  ListingPublic,
  'lat' | 'lng' | 'category_main' | 'category_type' | 'disposition' | 'ownership'
>;

export interface Neighbourhood {
  filters: ListingFilters;
  /* The attributes taken from the property that the property does not state. */
  dropped: string[];
}

/* The neighbourhood filter BEFORE the lens (the explore window applies its own
 * lens switch over it); null when the property has no point. */
export function neighbourhoodFilters(p: NeighbourhoodProperty, scope: NeighbourhoodScope): Neighbourhood | null {
  if (p.lat == null || p.lng == null) return null;
  const dropped: string[] = [];
  const main = CATEGORY_MAINS.has(p.category_main ?? '') ? (p.category_main as CategoryMain) : null;
  if (main == null) dropped.push('typ');
  const deal = CATEGORY_TYPES.has(p.category_type ?? '') ? (p.category_type as CategoryType) : null;
  if (deal == null) dropped.push('nabídka');
  const disposition = DISPOSITIONS.has(p.disposition ?? '') ? (p.disposition as Disposition) : null;
  if (disposition == null) dropped.push('dispozice');
  const ownership = OWNERSHIPS.has(p.ownership ?? '') ? (p.ownership as string) : null;
  if (ownership == null) dropped.push('vlastnictví');
  return {
    filters: {
      ...DEFAULT_FILTERS,
      categoryMain: main ? [main] : [],
      categoryType: deal,
      dispositions: disposition ? [disposition] : [],
      ownership: ownership ? [ownership] : [],
      buildingMaterial: scope.buildingMaterial,
      conditionMatch: scope.conditionMatch,
      bounds: null,
      locationMode: 'center_radius',
      centerRadius: { lat: p.lat, lng: p.lng, radius_m: scope.radiusM },
    },
    dropped,
  };
}

export const fmtRadius = (m: number): string => (m >= 1000 ? `${m / 1000} km` : `${m} m`);
