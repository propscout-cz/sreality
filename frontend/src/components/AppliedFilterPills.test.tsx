/* Browse's applied filters as removable pills under the count (ruling P09,
 * 2026-10-27): one per filter set beyond the defaults, named as the sidebar names
 * it and valued in the sheet's words (in English, the screen's language); × resets
 * that one filter. The type and deal are the headline's words, never a pill. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { DEFAULT_FILTERS, type DistrictChip, type ListingFilters } from '@/lib/filters';
import type { Disposition } from '@/lib/types';

import AppliedFilterPills from './AppliedFilterPills';

const AREA = { west: 14.4, south: 50.06, east: 14.44, north: 50.1 };
const cz = (n: number) => n.toLocaleString('cs-CZ');
const at = (patch: Partial<ListingFilters>): ListingFilters => ({ ...DEFAULT_FILTERS, ...patch });

function renderPills(filters: ListingFilters) {
  const onChange = vi.fn();
  const onClearBounds = vi.fn();
  render(<AppliedFilterPills filters={filters} onChange={onChange} onClearBounds={onClearBounds} />);
  return { onChange, onClearBounds };
}
const pillNames = () =>
  within(screen.getByRole('list', { name: 'Applied filters' }))
    .getAllByRole('button')
    .map((b) => b.textContent?.replace('×', ''));
const pill = (name: string) => screen.getByRole('button', { name });

const PRAHA: DistrictChip = { name: 'Praha', context: null, level: 'obec', id: 554782 };
const ZIZKOV: DistrictChip = { name: 'Žižkov', context: null, level: 'obec', id: 727415, excluded: true };
const BRNO: DistrictChip = { name: 'Brno', context: null, level: 'obec', id: 582786 };

describe('<AppliedFilterPills>', () => {
  it('draws nothing on the defaults', () => {
    renderPills(DEFAULT_FILTERS);
    expect(screen.queryByRole('list', { name: 'Applied filters' })).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('draws one pill per filter, the sidebar\'s label with the value in words', () => {
    renderPills(at({
      categoryMain: ['byt', 'dum'],
      categoryType: 'pronajem',
      priceMin: 3_000_000,
      dispositions: ['2+kk', '3+kk'] as Disposition[],
      hasBalcony: 'yes',
    }));
    expect(pillNames()).toEqual([`Price: from ${cz(3_000_000)} Kč`, 'Disposition: 2+kk, 3+kk', 'Balcony: yes']);
  });

  it('sets numbers in mono and words in the label face', () => {
    renderPills(at({ priceMin: 3_000_000, status: 'active' }));
    expect(screen.getByText(/^from 3\s000\s000 Kč$/).className).toContain('font-mono');
    expect(screen.getByText('Aktivní').className).not.toContain('font-mono');
  });

  it('clears one filter on its ×, and nothing else', () => {
    const f = at({ priceMin: 3_000_000, priceMax: 5_000_000, dispositions: ['2+kk'] as Disposition[] });
    const { onChange, onClearBounds } = renderPills(f);
    fireEvent.click(pill(`Price: ${cz(3_000_000)}–${cz(5_000_000)} Kč`));
    expect(onChange).toHaveBeenCalledWith({ ...f, priceMin: null, priceMax: null });
    expect(onClearBounds).not.toHaveBeenCalled();
  });

  it('says what a click does, with the whole value, and hides no exclusion', () => {
    renderPills(at({ districts: [PRAHA, ZIZKOV, BRNO] }));
    const location = pill('Location: Praha +2');
    expect(location).toHaveAttribute('title', 'Clear Location (Praha, −Žižkov, Brno)');
  });

  it('keeps the map-area and broker chips\' words and tooltips', () => {
    const f = at({ bounds: AREA, brokerId: 7 });
    const { onChange, onClearBounds } = renderPills(f);
    expect(pill('Map area applied')).toHaveAttribute(
      'title',
      'Clear the map area filter and widen back to the full cohort',
    );
    expect(pill('Broker scope applied')).toHaveAttribute(
      'title',
      'Clear the broker scope and widen back to the full cohort',
    );
    fireEvent.click(pill('Map area applied'));
    expect(onClearBounds).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(pill('Broker scope applied'));
    expect(onChange).toHaveBeenCalledWith({ ...f, brokerId: null });
  });

  it('names both sides of the portal filter, so an exclusion is never invisible', () => {
    renderPills(at({ portals: ['idnes'], portalsExcluded: ['sreality'] }));
    expect(pillNames()).toEqual(['Source portal: on iDNES −Sreality']);
  });

  it('names an exclusion that counts active ads only', () => {
    renderPills(at({ portalsExcluded: ['sreality'], portalsExcludedActiveOnly: true }));
    expect(pillNames()).toEqual(['Source portal: −Sreality (active ads)']);
  });

  it('gives every active curated set a pill of its own, in Czech scope words', () => {
    renderPills(at({
      tags: { set: 'out', in: [], out: [] },
      collections: { set: null, in: [4], out: [] },
      pipeline: { set: 'out', in: [], out: [] },
    }));
    expect(pillNames()).toEqual(['Pipeline: mimo pipeline', 'Collections: v kolekci (1 kolekce)', 'Tags: bez štítků']);
  });

  it('clears both ends of a range no sidebar row draws', () => {
    const f = at({ gardenAreaMin: 50, gardenAreaMax: 200 });
    const { onChange } = renderPills(f);
    fireEvent.click(pill('Garden area'));
    expect(onChange).toHaveBeenCalledWith({ ...f, gardenAreaMin: null, gardenAreaMax: null });
  });
});
