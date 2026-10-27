/* The map's pin-label switch is the one joined bar (operator ruling 2026-10-27):
 * its chosen segment filled copper, as every pick-one's is, never ink. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { PriceMetricControls } from './ListingMap';

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
