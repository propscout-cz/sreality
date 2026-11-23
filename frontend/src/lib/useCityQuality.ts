/* Resolve a property to its curated city's quality indexes — the read path
 * behind the index strip (CityIndexStrip). Since ruling P19 (2026-10-27) took it
 * off the pipeline card, the property page's Přehled shows it, as its own card.
 *
 * THE JOIN, AND WHY IT IS THIS ONE
 * There is no `properties.curated_city_id`. A curated city is keyed by the
 * RÚIAN code of its town (`curated_cities.obec_kod`, migration 726), and the
 * SQL rules match `obec_id = ANY(curated_cities_matching(...))` (migration 436).
 * `properties_public.obec_id` is the resolver's own obec code
 * (`listing_location.obec_kod`, migration 507), so `obec_id = obec_kod` is the
 * same integer equi-join the city-quality FILTER uses — no PostGIS, no RPC.
 *
 * COVERAGE. 206 curated cities out of ~6,250 Czech obce; about half of all
 * properties resolve to one. A card outside every curated city renders NO
 * strip — never a grey placeholder, and never the "nearby city" proximity
 * columns (`near_overall_*` et al), which answer a different question ("the
 * best city within 15 km scores X") and would silently mix two semantics in
 * one row of chips.
 */

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  cityQualityKeys,
  fetchCityIndexDefinitions,
  fetchCityIndexValues,
  fetchCuratedCities,
  type CityIndexDefinition,
  type CityIndexValue,
  type CuratedCity,
} from './queries';
import { CARD_INDEX_SLUGS } from './cityIndexes';

export interface CityQualityReading {
  index_name: string;
  /** null when this city has no row for this index — rendered as an em-dash
   *  cell, distinct from "the property is in no curated city" (no strip). */
  value: number | null;
  def: CityIndexDefinition;
}

export interface CityQuality {
  city_id: number;
  city_name: string;
  readings: CityQualityReading[];
}

/** obec_id → that municipality's curated-city readings, for the card slugs. */
export type CityQualityByObec = ReadonlyMap<number, CityQuality>;

const EMPTY: CityQualityByObec = new Map();

export function buildCityQualityByObec(
  cities: readonly CuratedCity[],
  defs: readonly CityIndexDefinition[],
  values: readonly CityIndexValue[],
  slugs: readonly string[] = CARD_INDEX_SLUGS,
): CityQualityByObec {
  const defBySlug = new Map(defs.map((d) => [d.index_name, d]));
  // Only the card slugs that actually have a definition — an unseeded slug is
  // skipped rather than rendered as a mystery cell.
  const wanted = slugs
    .map((s) => defBySlug.get(s))
    .filter((d): d is CityIndexDefinition => d != null);
  if (wanted.length === 0) return EMPTY;

  const valueByCityIndex = new Map<string, number>();
  for (const v of values) valueByCityIndex.set(`${v.city_id}:${v.index_name}`, v.value);

  const out = new Map<number, CityQuality>();
  for (const c of cities) {
    out.set(c.obec_kod, {
      city_id: c.city_id,
      city_name: c.name,
      readings: wanted.map((def) => ({
        index_name: def.index_name,
        value: valueByCityIndex.get(`${c.city_id}:${def.index_name}`) ?? null,
        def,
      })),
    });
  }
  return out;
}

/** The three source datasets are operator-static (a CSV upload a couple of
 *  times a year), so they cache forever and are shared by key with the Browse
 *  map — a surface that renders the strip pays no extra network once the map
 *  has loaded, and vice versa. `enabled` lets a surface skip the ~7 paged
 *  round-trips entirely when nothing on screen needs them. */
export function useCityQuality(enabled = true): {
  byObec: CityQualityByObec;
  isLoading: boolean;
} {
  const shared = { staleTime: Infinity, gcTime: Infinity, enabled } as const;
  const cities = useQuery({
    queryKey: cityQualityKeys.cities,
    queryFn: fetchCuratedCities,
    ...shared,
  });
  const defs = useQuery({
    queryKey: cityQualityKeys.definitions,
    queryFn: fetchCityIndexDefinitions,
    ...shared,
  });
  const values = useQuery({
    queryKey: cityQualityKeys.values,
    queryFn: fetchCityIndexValues,
    ...shared,
  });

  const byObec = useMemo(
    () =>
      cities.data && defs.data && values.data
        ? buildCityQualityByObec(cities.data, defs.data, values.data)
        : EMPTY,
    [cities.data, defs.data, values.data],
  );

  return {
    byObec,
    isLoading: cities.isLoading || defs.isLoading || values.isLoading,
  };
}
