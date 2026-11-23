/* The map's pin-label switch is the one joined bar (operator ruling 2026-10-27):
 * its chosen segment filled copper, as every pick-one's is, never ink. A city layer
 * that failed to load says so on the map, folded key or not (2026-11-22). */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { CityLoadAlert, PinPrecisionKey, PriceMetricControls } from './ListingMap';
import { MapLegendDock } from './OverlayChip';

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

describe('<CityLoadAlert>', () => {
  it('says the cities failed and retries on click', () => {
    const retry = vi.fn();
    render(<CityLoadAlert failure={{ retrying: false, retry }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Města se nepodařilo načíst.');
    fireEvent.click(screen.getByRole('button', { name: 'Zkusit znovu' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('offers no second retry while one runs', () => {
    render(<CityLoadAlert failure={{ retrying: true, retry: vi.fn() }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Města se načítají znovu…');
    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('<MapLegendDock> alert', () => {
  const FOLDED = 'sreality.browse.mapLegendsFolded';
  afterEach(() => localStorage.removeItem(FOLDED));

  it('keeps the alert in view while the keys are folded', () => {
    localStorage.setItem(FOLDED, '1');
    render(
      <MapLegendDock alert={<div role="alert">vrstva selhala</div>}>
        <div>klíč</div>
      </MapLegendDock>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('vrstva selhala');
    expect(screen.queryByText('klíč')).toBeNull();
    expect(screen.getByRole('button', { name: 'Ukázat legendu' })).toBeInTheDocument();
  });

  it('draws no fold button when there is no key to fold', () => {
    render(<MapLegendDock alert={<div role="alert">vrstva selhala</div>}>{null}</MapLegendDock>);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
