/* Every map's one layer list: the Podklad bar (Mapa | Letecká), one toggle per
 * registry section, tri-state over the section's layers, the settings under one
 * that is on (per-layer toggles, zoom hint, note, provenance), ČÚZK's Reliéf a
 * vrstevnice and Katastrální mapa, the legend cards a map draws for the sections that are on (the
 * rent map's an area card, which also picks the size category), and the
 * compact chips the comparables map uses.
 * The registry is fixed here; the selection grammar itself is
 * lib/mapOverlays.test.ts's. */

import { describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';

import MapOverlayControls, { CadastreControls, MapOverlayLegends, TerrainControls } from './MapOverlayControls';
import type { OverlayPlaces } from '@/lib/mapOverlayDraw';
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
        { key: 'vk1', label: '1+kk, 1+1', prop: 'vk1', alt_prop: 'nov1', example_m2: 34 },
        { key: 'vk2', label: '2+kk, 2+1', prop: 'vk2', alt_prop: 'nov2', example_m2: 54 },
        { key: 'vk3', label: '3+kk, 3+1', prop: 'vk3', alt_prop: 'nov3', example_m2: 75 },
        { key: 'vk4', label: '4+kk, 4+1', prop: 'vk4', alt_prop: 'nov4', example_m2: 106 },
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

const renderLegends = (
  selection: string,
  registry: MapOverlayRegistry = REGISTRY,
  extra: { places?: OverlayPlaces; onChange?: (next: string) => void } = {},
) => render(<MapOverlayLegends registry={registry} selection={selection} {...extra} />);

const PLZEN = {
  name: 'Plzeň', kraj: 'Plzeňský kraj', level: 'ku',
  vk1: 284, vk2: 238, vk3: 219, vk4: 209, nov1: 346, nov2: 290, nov3: 267, nov4: 254,
};
const at = (props: Record<string, unknown> | null): OverlayPlaces => new Map([['rent_map', props]]);
const sizes = () => within(screen.getByRole('group', { name: 'Velikost bytu' })).getAllByRole('button');
const size = (label: string) => sizes().find((b) => b.textContent?.startsWith(label))!;

const chip = (name: string) => screen.getByRole('checkbox', { name });

describe('<MapOverlayControls> chips', () => {
  it('renders one chip per registry section between the base and ČÚZK’s own layers', () => {
    renderControls('');
    expect(chip('Metro')).not.toBeChecked();
    expect(chip('Záplavová území')).not.toBeChecked();
    expect(chip('Cenová mapa nájemného')).not.toBeChecked();
    expect(screen.getAllByRole('checkbox').map((c) => c.getAttribute('aria-label') ?? c.parentElement?.textContent))
      .toEqual(['Metro', 'Záplavová území', 'Cenová mapa nájemného', 'Reliéf a vrstevnice', 'Katastrální mapa']);
    expect(screen.getByRole('group', { name: 'Podklad mapy' })).toBeInTheDocument();
  });

  it('offers the basemap picks alone while the registry is missing', () => {
    render(<MapOverlayControls registry={undefined} selection="metro" onChange={vi.fn()} />);
    expect(screen.getByRole('group', { name: 'Podklad mapy' })).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    expect(chip('Reliéf a vrstevnice')).not.toBeChecked();
    expect(chip('Katastrální mapa')).not.toBeChecked();
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

  it('keeps the basemap picks through a registry change', () => {
    const onChange = renderControls('aerial,ghost,metro,relief');
    fireEvent.click(chip('Metro'));
    expect(onChange).toHaveBeenCalledWith('aerial,relief');
  });
});

/* The base: ČÚZK's aerial photo or the map, one key of the same selection. */
describe('<MapOverlayControls> Podklad', () => {
  const bar = () => screen.getByRole('group', { name: 'Podklad mapy' });

  it('lights Mapa until the photo is picked, and writes the pick beside every other key', () => {
    const onChange = renderControls('flood_q5,relief');
    expect(within(bar()).getByRole('button', { name: 'Mapa' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(bar()).getByRole('button', { name: 'Letecká' }));
    expect(onChange).toHaveBeenLastCalledWith('aerial,flood_q5,relief');
  });

  it('takes the photo away on Mapa', () => {
    const onChange = renderControls('aerial,metro');
    expect(within(bar()).getByRole('button', { name: 'Letecká' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(bar()).getByRole('button', { name: 'Mapa' }));
    expect(onChange).toHaveBeenLastCalledWith('metro');
  });

  it('switches the terrain in the same selection', () => {
    const onChange = renderControls('metro');
    fireEvent.click(chip('Reliéf a vrstevnice'));
    expect(onChange).toHaveBeenLastCalledWith('contours,metro,relief');
  });

  it('switches the cadastral map in the same selection', () => {
    const onChange = renderControls('aerial,metro');
    fireEvent.click(chip('Katastrální mapa'));
    expect(onChange).toHaveBeenLastCalledWith('aerial,cadastre,metro');
  });

  it('lays Browse’s own rows between the base and the registry', () => {
    render(
      <MapOverlayControls registry={REGISTRY} selection="" onChange={vi.fn()}>
        <div>Města</div>
      </MapOverlayControls>,
    );
    const list = screen.getByText('Města').parentElement!;
    const rows = [...list.children].map((row) => row.textContent ?? '');
    expect(rows[0]).toContain('Podklad');
    expect(rows[1]).toBe('Města');
    expect(rows[2]).toContain('Metro');
    expect(rows[3]).toContain('Reliéf a vrstevnice');
    expect(rows[4]).toContain('Katastrální mapa');
  });
});

/* ČÚZK's terrain in the list: one tri-state row for both, then each its own box. */
describe('<TerrainControls>', () => {
  it('turns both on from the one row, and shows nothing more while both are off', () => {
    const onChange = vi.fn();
    render(<TerrainControls relief={false} contours={false} zoom={14} onChange={onChange} />);
    expect(screen.queryByRole('checkbox', { name: 'Vrstevnice' })).toBeNull();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Reliéf a vrstevnice' }));
    expect(onChange).toHaveBeenLastCalledWith({ relief: true, contours: true });
  });

  it('reads one of two as half on, switches each alone, and names ČÚZK', () => {
    const onChange = vi.fn();
    render(<TerrainControls relief contours={false} zoom={17} onChange={onChange} />);
    const row = screen.getByRole('checkbox', { name: 'Reliéf a vrstevnice' }) as HTMLInputElement;
    expect(row.indeterminate).toBe(true);
    expect(screen.getByRole('checkbox', { name: 'Stínovaný reliéf' })).toBeChecked();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Vrstevnice' }));
    expect(onChange).toHaveBeenLastCalledWith({ contours: true });
    expect(screen.getByRole('link', { name: '© ČÚZK' })).toHaveAttribute('href', 'https://geoportal.cuzk.gov.cz/');
  });

  it('asks to zoom in only while the contours are on below zoom 16', () => {
    const { rerender } = render(<TerrainControls relief contours zoom={15} onChange={vi.fn()} />);
    expect(screen.getByText('Vrstevnice: přibližte mapu')).toBeInTheDocument();
    rerender(<TerrainControls relief contours zoom={16} onChange={vi.fn()} />);
    expect(screen.queryByText('Vrstevnice: přibližte mapu')).toBeNull();
    rerender(<TerrainControls relief contours={false} zoom={12} onChange={vi.fn()} />);
    expect(screen.queryByText('Vrstevnice: přibližte mapu')).toBeNull();
  });
});

/* ČÚZK's cadastral map in the list: one row; while on, the zoom hint or what a click does, and the source. */
describe('<CadastreControls>', () => {
  it('switches from its row and shows nothing more while off', () => {
    const onChange = vi.fn();
    render(<CadastreControls on={false} zoom={18} onChange={onChange} />);
    expect(screen.queryByRole('link', { name: '© ČÚZK' })).toBeNull();
    fireEvent.click(chip('Katastrální mapa'));
    expect(onChange).toHaveBeenLastCalledWith(true);
  });

  it('asks to zoom in below zoom 17, then says a click opens the parcel, and names ČÚZK', () => {
    const { rerender } = render(<CadastreControls on zoom={16} onChange={vi.fn()} />);
    expect(screen.getByText('Přibližte mapu')).toHaveAttribute('title', 'Katastrální mapa se kreslí od přiblížení 17');
    expect(screen.queryByText(/Klikněte na parcelu/)).toBeNull();
    rerender(<CadastreControls on zoom={17} onChange={vi.fn()} />);
    expect(screen.queryByText('Přibližte mapu')).toBeNull();
    expect(screen.getByText('Klikněte na parcelu pro její číslo a výměru')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '© ČÚZK' })).toHaveAttribute('href', 'https://geoportal.cuzk.gov.cz/');
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

describe('<MapOverlayLegends> the area card', () => {
  it('names the place under the centre and gives both rents of every size, the shown size lit', () => {
    renderLegends('rent_map', REGISTRY, { places: at(PLZEN) });
    expect(screen.getByText('Katastrální území · Plzeňský kraj')).toBeInTheDocument();
    expect(screen.getByText('Plzeň')).toBeInTheDocument();
    expect(screen.getByText(`Nájemné (${PPM2_UNIT.rent})`)).toBeInTheDocument();
    expect(screen.getByText('Novostavba')).toBeInTheDocument();
    expect(sizes().map((b) => b.textContent)).toEqual([
      '1+kk, 1+1284346', '2+kk, 2+1238290', '3+kk, 3+1219267', '4+kk, 4+1209254',
    ]);
    expect(sizes().map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false', 'false']);
  });

  it('names a place the MF priced whole as an obec', () => {
    renderLegends('rent_map', REGISTRY, { places: at({ name: 'Šanov', kraj: 'Jihomoravský kraj', level: 'obec', vk1: 272 }) });
    expect(screen.getByText('Obec · Jihomoravský kraj')).toBeInTheDocument();
  });

  it("works the shown size's example: its median area times its rent, per month", () => {
    renderLegends('rent_map:vk2', REGISTRY, { places: at(PLZEN) });
    expect(screen.getByText(/^Příklad:/)).toHaveTextContent('Příklad: 54 m² × 238 = 12 852 Kč/měs');
    expect(screen.getByText(/^Příklad:/)).toHaveAttribute(
      'title', '54 m² je medián plochy nabízených nájemních bytů 2+kk, 2+1',
    );
  });

  it('paints the map by a clicked size, the first one spelled as the bare key', () => {
    const onChange = vi.fn();
    renderLegends('rent_map:vk2', REGISTRY, { places: at(PLZEN), onChange });
    expect(size('2+kk, 2+1')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(size('3+kk, 3+1'));
    expect(onChange).toHaveBeenLastCalledWith('rent_map:vk3');
    fireEvent.click(size('1+kk, 1+1'));
    expect(onChange).toHaveBeenLastCalledWith('rent_map');
  });

  it('cannot pick without a writer', () => {
    renderLegends('rent_map', REGISTRY, { places: at(PLZEN) });
    expect(sizes().every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
  });

  it('dashes what the place lacks, and says so when the centre is on no place', () => {
    renderLegends('rent_map', REGISTRY, { places: at({ name: 'Plzeň', level: 'ku', vk1: 284, vk3: 219 }) });
    expect(sizes().map((b) => b.textContent)).toEqual([
      '1+kk, 1+1284—', '2+kk, 2+1——', '3+kk, 3+1219—', '4+kk, 4+1——',
    ]);
    cleanup();
    renderLegends('rent_map', REGISTRY, { places: at(null) });
    expect(screen.getByText('Střed mapy')).toBeInTheDocument();
    expect(screen.getByText('Bez údajů')).toBeInTheDocument();
    expect(screen.queryByText(/^Příklad:/)).toBeNull();
  });

  it('drops the second column and the example where the registry carries neither', () => {
    const bare: OverlaySection = {
      ...RENT,
      layers: [{
        ...RENT.layers[0],
        variants: RENT.layers[0].variants!.map(({ key, label, prop }) => ({ key, label, prop })),
      }],
    };
    renderLegends('rent_map', { sections: [bare] }, { places: at(PLZEN) });
    expect(screen.queryByText('Novostavba')).toBeNull();
    expect(sizes()[0]).toHaveTextContent('1+kk, 1+1284');
    expect(screen.queryByText(/^Příklad:/)).toBeNull();
  });

  it('leaves the size pick to the card: the Vrstvy panel has none', () => {
    renderControls('rent_map');
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByRole('group', { name: 'Velikost bytu' })).toBeNull();
  });
});

describe('<MapOverlayControls> legend', () => {
  /* The legend the rent map has always had: what is measured, the ramp, its ends —
   * on the map now, so it reads with the Vrstvy panel closed. */
  it('keys the rent scale by its caption and range, as the rent map always has', () => {
    renderLegends('rent_map');
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
    renderLegends('rent_map', { sections: [plain] });
    expect(screen.getByText('dB')).toBeInTheDocument();
  });

  it('lists every class of a one-layer section in its legend', () => {
    renderLegends('metro');
    const legend = screen.getByRole('list', { name: 'Legenda' });
    expect(within(legend).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Linka A',
      'Metro D – návrh',
      'Plánovaná stanice',
    ]);
  });

  it('keys only the sections that are on', () => {
    renderLegends('rent_map:vk2');
    expect(screen.getByText(RENT.label)).toBeInTheDocument();
    expect(size('2+kk, 2+1')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText(METRO.label)).toBeNull();
  });

  it('keeps the legend out of the panel, which carries the settings', () => {
    renderControls('metro');
    expect(screen.queryByRole('list', { name: 'Legenda' })).toBeNull();
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
    expect(screen.getAllByRole('checkbox')).toHaveLength(6);
    expect(screen.queryByRole('group', { name: 'Podklad mapy' })).toBeNull();
    expect(chip('Metro')).toBeChecked();
    expect(chip('Záplavová území')).toBePartiallyChecked();
    expect(screen.queryByText(/Data k/)).toBeNull();
    expect(screen.queryByText('Přibližte mapu')).toBeNull();
    expect(screen.queryByRole('list', { name: 'Legenda' })).toBeNull();
    fireEvent.click(chip('Metro'));
    expect(onChange).toHaveBeenCalledWith('flood_q5');
  });
});

describe('<MapOverlayControls compact> basemap chips', () => {
  it('carries the photo, the terrain and the cadastre as chips of their own', () => {
    const onChange = renderControls('relief', { compact: true });
    expect(chip('Letecká')).not.toBeChecked();
    expect(chip('Reliéf a vrstevnice')).toBePartiallyChecked();
    fireEvent.click(chip('Letecká'));
    expect(onChange).toHaveBeenLastCalledWith('aerial,relief');
    fireEvent.click(chip('Reliéf a vrstevnice'));
    expect(onChange).toHaveBeenLastCalledWith('contours,relief');
    fireEvent.click(chip('Katastrální mapa'));
    expect(onChange).toHaveBeenLastCalledWith('cadastre,relief');
  });
});
