/* The map's pin-label switch is the one joined bar (operator ruling 2026-10-27):
 * its chosen segment filled copper, as every pick-one's is, never ink. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { PinPrecisionKey, PriceMetricControls, TerrainControls } from './ListingMap';

describe('<PriceMetricControls>', () => {
  it('draws Celkem | Za m² as the joined bar, the chosen metric filled copper', () => {
    const onChange = vi.fn();
    render(<PriceMetricControls priceMetric="total" onPriceMetricChange={onChange} />);
    const group = screen.getByRole('group', { name: 'Popisky pinů' });
    const [total, perM2] = within(group).getAllByRole('button');
    expect([total.textContent, perM2.textContent]).toEqual(['Celkem', 'Za m²']);
    expect(total.parentElement).toBe(group);
    expect(perM2.parentElement).toBe(group);
    expect(group).toHaveClass('inline-grid', 'border', 'bg-[var(--color-paper-2)]');
    expect(total).toHaveAttribute('aria-pressed', 'true');
    expect(total).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(total.className).not.toContain('color-ink)');
    expect(perM2).toHaveAttribute('title', 'Popisky pinů: celková cena nebo cena za m²');
    expect(screen.getByText('Ceny na pinech')).toBeInTheDocument();
    fireEvent.click(perM2);
    expect(onChange).toHaveBeenLastCalledWith('per_m2');
  });

  it('is inert without a handler', () => {
    render(<PriceMetricControls priceMetric="per_m2" />);
    fireEvent.click(screen.getByRole('button', { name: 'Celkem' }));
    expect(screen.getByRole('button', { name: 'Za m²' })).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('<PinPrecisionKey>', () => {
  it('names exact and approximate pins and promises no circle', () => {
    render(<PinPrecisionKey exact={3} approx={5} inactive={1} />);
    expect(within(screen.getByRole('list', { name: 'Legenda pinů' })).getAllByRole('listitem')
      .map((li) => li.textContent)).toEqual(['Přesná poloha3', 'Přibližná poloha5', 'Neaktivní1']);
    const hint = screen.getByText(/^Černý obrys/);
    expect(hint.textContent).not.toMatch(/okruh|jak je velký/);
  });
});

/* ČÚZK's terrain in the Vrstvy panel: one tri-state row for both, then each its own box. */
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
