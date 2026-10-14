/* The reference-overlay chips: one per registry section, tri-state over the
 * section's layers, the panel under a chip that is on (per-layer toggles,
 * variant radios, legend, zoom hint, note, provenance), and the compact chips
 * the small maps use. The registry is fixed here; the selection grammar itself
 * is lib/mapOverlays.test.ts's. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import MapOverlayControls from './MapOverlayControls';
import type { MapOverlayRegistry, OverlaySection } from '@/lib/mapOverlays';
import { PPM2_UNIT } from '@/lib/measure';

const base = {
  attribution: 'zdroj',
  licence: 'CC BY 4.0',
  source_url: 'https://example.invalid/',
  note: null,
  source_revised_at: null,
  fetched_at: '2026-10-14T10:00:00Z',
  version: 1,
  maxzoom: 12,
};

const METRO: OverlaySection = {
  ...base,
  key: 'metro',
  label: 'Metro',
  sort: 1,
  minzoom: 9,
  attribution: 'IPR Praha',
  note: 'Metropolitní plán hl. m. Prahy: stav, návrh a územní rezervy.',
  source_revised_at: '2026-09-01',
  layers: [
    {
      key: 'metro',
      label: 'Metro',
      sort: 1,
      legend: [
        { class: 'A', label: 'Linka A', color: '#00A562' },
        { class: 'D', label: 'Metro D – návrh', color: '#1F77D0', dash: true },
        { class: 'station', label: 'Plánovaná stanice', color: '#1F77D0', geom: 'point' },
      ],
      scale: null,
      variants: null,
      feature_count: 18,
    },
  ],
};

const flood = (key: string, sort: number, label: string) => ({
  key,
  label: key,
  sort,
  legend: [{ class: key, label, color: '#3B7DD8' }],
  scale: null,
  variants: null,
  feature_count: 900,
});

const FLOOD: OverlaySection = {
  ...base,
  key: 'flood',
  label: 'Záplavová území',
  sort: 2,
  minzoom: 10,
  attribution: 'VÚV TGM',
  note: 'Stanovená záplavová území (zákonný rozsah, orientační).',
  source_revised_at: '2025-08-31',
  source_url: 'https://ags2.vuv.cz/arcgis/rest/services/isvs_voda/isvs_voda/MapServer',
  layers: [
    flood('flood_q5', 1, 'Q5 – pětiletá voda'),
    flood('flood_q100', 3, 'Q100 – stoletá voda'),
    flood('flood_az', 5, 'Aktivní zóna'),
  ],
};

const RENT: OverlaySection = {
  ...base,
  key: 'rent_map',
  label: 'Cenová mapa nájemného',
  sort: 3,
  minzoom: 7,
  layers: [
    {
      key: 'rent_map',
      label: 'Velikost bytu',
      sort: 1,
      legend: [],
      scale: {
        prop: 'vk1',
        stops: [[150, '#eaf0f6'], [250, '#9ec3e6'], [350, '#5a7fd6'], [450, '#3b4fb5'], [550, '#2a2a8a'], [600, '#1a1a5e']],
        null_color: 'rgba(140, 140, 140, 0.35)',
        unit: 'rent_per_m2',
        opacity: 0.62,
      },
      variants: [
        { key: 'vk1', label: '1+kk, 1+1', prop: 'vk1' },
        { key: 'vk2', label: '2+kk, 2+1', prop: 'vk2' },
        { key: 'vk3', label: '3+kk, 3+1', prop: 'vk3' },
        { key: 'vk4', label: '4+kk, 4+1', prop: 'vk4' },
      ],
      feature_count: 7630,
    },
  ],
};

const REGISTRY: MapOverlayRegistry = { sections: [METRO, FLOOD, RENT] };

function renderControls(
  selection: string,
  extra: { zoom?: number | null; compact?: boolean; registry?: MapOverlayRegistry } = {},
) {
  const onChange = vi.fn();
  render(
    <MapOverlayControls
      registry={extra.registry ?? REGISTRY}
      selection={selection}
      onChange={onChange}
      zoom={extra.zoom}
      compact={extra.compact}
    />,
  );
  return onChange;
}

const chip = (name: string) => screen.getByRole('checkbox', { name });

describe('<MapOverlayControls> chips', () => {
  it('renders one chip per registry section, nothing without a registry', () => {
    renderControls('');
    expect(chip('Metro')).not.toBeChecked();
    expect(chip('Záplavová území')).not.toBeChecked();
    expect(chip('Cenová mapa nájemného')).not.toBeChecked();
    expect(screen.getAllByRole('checkbox')).toHaveLength(3);

    const { container } = render(
      <MapOverlayControls registry={undefined} selection="metro" onChange={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('turns every layer of a section on, in canonical order', () => {
    const onChange = renderControls('metro');
    fireEvent.click(chip('Záplavová území'));
    expect(onChange).toHaveBeenCalledWith('flood_az,flood_q100,flood_q5,metro');
  });

  it('turns a section off without touching the others', () => {
    const onChange = renderControls('flood_q5,metro');
    fireEvent.click(chip('Metro'));
    expect(onChange).toHaveBeenCalledWith('flood_q5');
  });

  it('drops keys the registry does not know on the first change', () => {
    const onChange = renderControls('ghost,metro');
    fireEvent.click(chip('Metro'));
    expect(onChange).toHaveBeenCalledWith('');
  });
});

describe('<MapOverlayControls> tri-state', () => {
  it('shows a partly-on section as mixed, and checking it turns the rest on', () => {
    const onChange = renderControls('flood_q5');
    expect(chip('Záplavová území')).toBePartiallyChecked();
    expect(chip('Záplavová území')).not.toBeChecked();
    fireEvent.click(chip('Záplavová území'));
    expect(onChange).toHaveBeenCalledWith('flood_az,flood_q100,flood_q5');
  });

  it('toggles one layer from the panel, each wearing its class swatch and label', () => {
    const onChange = renderControls('flood_q5');
    expect(chip('Q5 – pětiletá voda')).toBeChecked();
    fireEvent.click(chip('Q100 – stoletá voda'));
    expect(onChange).toHaveBeenLastCalledWith('flood_q100,flood_q5');
    fireEvent.click(chip('Q5 – pětiletá voda'));
    expect(onChange).toHaveBeenLastCalledWith('');
  });

  it('is fully checked when every layer is on', () => {
    renderControls('flood_az,flood_q100,flood_q5');
    expect(chip('Záplavová území')).toBeChecked();
    expect(chip('Záplavová území')).not.toBePartiallyChecked();
  });
});

describe('<MapOverlayControls> variants and legend', () => {
  it('offers the variants as radios, the first one shown by default', () => {
    const onChange = renderControls('rent_map');
    const group = screen.getByRole('radiogroup', { name: 'Velikost bytu' });
    expect(within(group).getAllByRole('radio').map((radio) => radio.closest('label')?.textContent)).toEqual([
      '1+kk, 1+1',
      '2+kk, 2+1',
      '3+kk, 3+1',
      '4+kk, 4+1',
    ]);
    expect(within(group).getByRole('radio', { name: '1+kk, 1+1' })).toBeChecked();
    fireEvent.click(within(group).getByRole('radio', { name: '2+kk, 2+1' }));
    expect(onChange).toHaveBeenCalledWith('rent_map:vk2');
  });

  it('spells a return to the first variant as the bare key', () => {
    const onChange = renderControls('rent_map:vk2');
    expect(screen.getByRole('radio', { name: '2+kk, 2+1' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: '1+kk, 1+1' }));
    expect(onChange).toHaveBeenCalledWith('rent_map');
  });

  /* The legend the rent map has always had: what is measured, the ramp, its ends. */
  it('keys the rent scale by its caption and range, as the rent map always has', () => {
    renderControls('rent_map');
    expect(screen.getByText(`Nájemné (${PPM2_UNIT.rent})`)).toBeInTheDocument();
    expect(screen.getByText('150')).toBeInTheDocument();
    expect(screen.getByText('600')).toBeInTheDocument();
    expect(screen.queryByText('bez dat')).toBeNull();
  });

  it('captions any other scale with its own unit token', () => {
    const plain: OverlaySection = {
      ...RENT,
      layers: [{ ...RENT.layers[0], scale: { ...RENT.layers[0].scale!, unit: 'dB' } }],
    };
    renderControls('rent_map', { registry: { sections: [plain] } });
    expect(screen.getByText('dB')).toBeInTheDocument();
  });

  it('lists every class of a one-layer section in its legend', () => {
    renderControls('metro');
    const legend = screen.getByRole('list', { name: 'Legenda' });
    expect(within(legend).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Linka A',
      'Metro D – návrh',
      'Plánovaná stanice',
    ]);
  });
});

describe('<MapOverlayControls> hint, note and footer', () => {
  it('asks to zoom in below the section minzoom, and only then', () => {
    renderControls('flood_q5', { zoom: 9 });
    expect(screen.getByText('Přibližte mapu')).toBeInTheDocument();
  });

  it('stays quiet at minzoom', () => {
    renderControls('flood_q5', { zoom: 10 });
    expect(screen.queryByText('Přibližte mapu')).toBeNull();
  });

  it('stays quiet when the map zoom is unknown', () => {
    renderControls('flood_q5', { zoom: null });
    expect(screen.queryByText('Přibližte mapu')).toBeNull();
  });

  it('shows the note of a section that is on, never of one that is off', () => {
    renderControls('flood_q5');
    expect(screen.getByText('Stanovená záplavová území (zákonný rozsah, orientační).')).toBeInTheDocument();
    expect(screen.queryByText(/Metropolitní plán/)).toBeNull();
  });

  it('dates the data and credits its source, linked', () => {
    renderControls('flood_q5');
    expect(screen.getByText(/^Data k 31\. 8\. 2025/)).toHaveTextContent('Data k 31. 8. 2025 · VÚV TGM');
    const link = screen.getByRole('link', { name: 'VÚV TGM' });
    expect(link).toHaveAttribute('href', FLOOD.source_url);
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('falls back to the fetch date when the publisher gives none', () => {
    renderControls('rent_map');
    expect(screen.getByText(/^Data k/)).toHaveTextContent('Data k 14. 10. 2026 · zdroj');
  });
});

describe('<MapOverlayControls compact>', () => {
  it('shows the chips alone, with the same tri-state and the same writes', () => {
    const onChange = renderControls('flood_q5,metro', { compact: true, zoom: 3 });
    expect(screen.getAllByRole('checkbox')).toHaveLength(3);
    expect(chip('Metro')).toBeChecked();
    expect(chip('Záplavová území')).toBePartiallyChecked();
    expect(screen.queryByText(/Data k/)).toBeNull();
    expect(screen.queryByText('Přibližte mapu')).toBeNull();
    expect(screen.queryByRole('list', { name: 'Legenda' })).toBeNull();
    fireEvent.click(chip('Metro'));
    expect(onChange).toHaveBeenCalledWith('flood_q5');
  });
});
