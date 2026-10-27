/* A Browse pin's popup (MapLibre takes it as an HTML string, so React's escaping
 * does not reach it). Ruling P13 (2026-10-27): how precisely the pin is placed and
 * when the ad was last seen are ONE line, "Přesná poloha · viděno před 2 h", each
 * half's detail in its tooltip. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { fmtAbsolute } from './format';
import { PPM2_BASIS_TOKEN } from './measure';
import { listingPopupHtml } from './listingPopupHtml';
import type { MapRow } from './queries';
import { pinPrecisionLabel } from './uncertaintyCircle';

const NOW = new Date('2026-10-27T12:00:00Z');
const SEEN = new Date(NOW.getTime() - 2 * 3600 * 1000).toISOString();

const ROW: MapRow = {
  listing_id: 111,
  property_id: 42,
  lat: 50.08,
  lng: 14.42,
  price_czk: 5_400_000,
  price_per_m2: 87_097,
  price_per_m2_basis: PPM2_BASIS_TOKEN.sale,
  category_main: 'byt',
  disposition: '2+kk',
  area_m2: 62,
  display_label: 'Sadová 12, Praha',
  uncertainty_radius_m: 10,
  granularity_rank: 100,
  last_seen_at: SEEN,
  is_active: false,
};

const popup = (r: MapRow = ROW) => {
  const el = document.createElement('div');
  el.innerHTML = listingPopupHtml(r);
  return el;
};
const words = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

describe('listingPopupHtml', () => {
  it('says precision and last seen on one line, each detail in its tooltip', () => {
    const el = popup();
    const lines = el.querySelectorAll('p.lp-precision');
    expect(lines).toHaveLength(1);
    expect(words(lines[0])).toBe('Přesná poloha · viděno před 2 h');
    const [precision, seen] = lines[0].querySelectorAll('[title]');
    expect(precision.getAttribute('title')).toBe(pinPrecisionLabel(ROW));
    expect(precision.getAttribute('title')).toBe('Přesná poloha (adresní bod)');
    expect(seen.getAttribute('title')).toBe(fmtAbsolute(SEEN));
    expect(el.querySelector('.lp-seen')).toBeNull();
    expect(el.textContent).not.toMatch(/last seen/);
  });

  it('names an approximate pin with its rung and radius in the tooltip', () => {
    const approx = { ...ROW, granularity_rank: 40, uncertainty_radius_m: 1000 };
    const line = popup(approx).querySelector('p.lp-precision');
    expect(words(line)).toBe('Přibližná poloha · viděno před 2 h');
    expect(line!.querySelector('[title]')!.getAttribute('title')).toBe(pinPrecisionLabel(approx));
    expect(pinPrecisionLabel(approx)).toMatch(/^Přibližná poloha: obec, ±1\skm$/);
  });

  it('keeps the price, the Inactive badge, the meta and place lines and the link', () => {
    const el = popup();
    expect(el.querySelector('.lp-price')?.textContent).toMatch(/5\s400\s000/);
    expect(el.querySelector('.lp-inactive')?.textContent).toBe('Inactive');
    expect(words(el.querySelector('.lp-meta'))).toMatch(/^2\+kk · 62\sm² ·\s?87\s097\sKč\/m²$/);
    expect(el.querySelector('.lp-district')?.textContent).toBe('Sadová 12, Praha');
    const link = el.querySelector('a.lp-link');
    expect(link?.textContent).toBe('View details →');
    expect(link?.getAttribute('href')).toMatch(/42/);
  });

  it('escapes what it interpolates, the place included', () => {
    const el = popup({ ...ROW, display_label: 'U "Lva" <b>12</b>' });
    expect(el.querySelector('.lp-district')?.textContent).toBe('U "Lva" <b>12</b>');
    expect(el.querySelector('b')).toBeNull();
  });
});
