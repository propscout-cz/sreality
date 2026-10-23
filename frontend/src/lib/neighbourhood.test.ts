import { describe, expect, it } from 'vitest';
import { DEFAULT_FILTERS, effectiveBbox } from './filters';
import { DEFAULT_SCOPE, isPropertyCard, neighbourhoodFilters } from './neighbourhood';
import { marketSample } from './stats';

const FLAT = {
  lat: 50.1,
  lng: 14.45,
  category_main: 'byt',
  category_type: 'pronajem',
  disposition: '2+kk',
  ownership: 'osobni',
} as const;

describe('neighbourhoodFilters — one neighbourhood for the tile and the explore window', () => {
  it('takes type, deal, disposition and ownership from the property, the rest unset, centre + radius around it', () => {
    const { filters, dropped } = neighbourhoodFilters(FLAT, DEFAULT_SCOPE)!;
    expect(dropped).toEqual([]);
    expect(filters).toEqual({
      ...DEFAULT_FILTERS,
      categoryMain: ['byt'],
      categoryType: 'pronajem',
      dispositions: ['2+kk'],
      ownership: ['osobni'],
      buildingMaterial: [],
      conditionMatch: [],
      bounds: null,
      locationMode: 'center_radius',
      centerRadius: { lat: 50.1, lng: 14.45, radius_m: 500 },
    });
    // The box every Browse reader filters on: ±500 m around the point.
    const box = effectiveBbox(filters)!;
    expect((box.north - box.south) * 111.32).toBeCloseTo(1, 1);
  });

  it('leaves an attribute the property does not state unset, and names it', () => {
    const { filters, dropped } = neighbourhoodFilters({ ...FLAT, disposition: null, ownership: null }, DEFAULT_SCOPE)!;
    expect(filters.dispositions).toEqual([]);
    expect(filters.ownership).toEqual([]);
    expect(dropped).toEqual(['dispozice', 'vlastnictví']);
  });

  it('carries the user\'s radius, building and condition', () => {
    const { filters } = neighbourhoodFilters(FLAT, {
      ...DEFAULT_SCOPE, radiusM: 2000, buildingMaterial: ['cihla'], conditionMatch: ['velmi_dobry'],
    })!;
    expect(filters.centerRadius?.radius_m).toBe(2000);
    expect(filters.buildingMaterial).toEqual(['cihla']);
    expect(filters.conditionMatch).toEqual(['velmi_dobry']);
  });

  it('has no neighbourhood without a point', () => {
    expect(neighbourhoodFilters({ ...FLAT, lat: null }, DEFAULT_SCOPE)).toBeNull();
  });

  it('counts under the Stats tab\'s lenses: sold market by default, everything under Vše', () => {
    const { filters } = neighbourhoodFilters(FLAT, DEFAULT_SCOPE)!;
    expect(marketSample(filters, 'sold')).toMatchObject({ status: 'inactive', showDismissed: true, tomDaysMin: 2, tomDaysMax: 60 });
    expect(marketSample(filters, 'all')).toMatchObject({ status: 'any', showDismissed: true, tomDaysMin: null, tomDaysMax: null, tomObservedOnly: false });
  });
});

describe('isPropertyCard', () => {
  it('is the account\'s own dashboard marked for the property page, whatever its name; never a shared one', () => {
    expect(isPropertyCard({ owner_email: null, property_card: true })).toBe(true);
    expect(isPropertyCard({ owner_email: 'jana@example.com', property_card: true })).toBe(false);
    expect(isPropertyCard({ owner_email: null, property_card: false })).toBe(false);
    // An API from before migration 663 sends no flag: no card, never a crash.
    expect(isPropertyCard({ owner_email: null })).toBe(false);
  });
});
