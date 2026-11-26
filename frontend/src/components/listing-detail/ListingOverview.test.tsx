/* ListingOverview (Estimation Detail's subject) and the identity column it shares
 * with the property page's header: the key facts live in the price column, not as
 * their own row between header and description, and the lowest active price
 * (MS8) is a labelled line under the headline price. */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { ListingOverview, PropertyIdentity } from './ListingOverview';
import type { ListingPublic } from '@/lib/types';

const LISTING = {
  id: 1,
  sreality_id: 1,
  is_active: true,
  first_seen_at: '2026-01-01T00:00:00Z',
  last_seen_at: '2026-01-02T00:00:00Z',
  source: 'sreality',
  category_main: 'byt',
  category_type: 'prodej',
  price_czk: 5_000_000,
  disposition: '2+kk',
  description: 'Světlý byt po rekonstrukci.',
  building_type: 'cihlova',
} as unknown as ListingPublic;

function precedes(a: HTMLElement, b: HTMLElement): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe('<ListingOverview> section order', () => {
  it('renders the key facts in the price column, ahead of the description', () => {
    render(
      <MemoryRouter>
        <ListingOverview listing={LISTING} />
      </MemoryRouter>,
    );

    const price = screen.getByRole('heading', { level: 1 });
    const facts = screen.getByText('Building');
    const description = screen.getByText('Description');

    expect(price.parentElement).toContainElement(facts);
    expect(precedes(price, facts)).toBe(true);
    expect(precedes(facts, description)).toBe(true);
  });

  it('renders the description, then the photos', () => {
    render(
      <MemoryRouter>
        <ListingOverview listing={LISTING} />
      </MemoryRouter>,
    );

    expect(precedes(screen.getByText('Description'), screen.getByText('Photos'))).toBe(true);
  });
});

/* MS8: the property page's lowest active price — labelled, display only. */
describe('<PropertyIdentity> price note', () => {
  const NOTE = { text: 'Nejnižší aktivní cena: 4 900 000 Kč · iDNES Reality', title: 'jen pro informaci' };

  it('sits under the headline price and leaves the price alone', () => {
    render(
      <MemoryRouter>
        <PropertyIdentity listing={LISTING} priceNote={NOTE} />
      </MemoryRouter>,
    );

    const h1 = screen.getByRole('heading', { level: 1 });
    const note = screen.getByText(NOTE.text);
    expect(h1).not.toHaveTextContent(NOTE.text);
    expect(note).toHaveAttribute('title', NOTE.title);
    expect(precedes(h1, note)).toBe(true);
  });

  it('takes the headline’s place, still labelled, when the listing states no price', () => {
    render(
      <MemoryRouter>
        <PropertyIdentity listing={{ ...LISTING, price_czk: null }} priceNote={NOTE} />
      </MemoryRouter>,
    );

    const h1 = screen.getByRole('heading', { level: 1 });
    expect(h1).toHaveTextContent(NOTE.text);
    expect(h1).toHaveAttribute('title', NOTE.title);
    expect(screen.queryByText('Cena na vyžádání')).toBeNull();
  });
});

describe('<PropertyIdentity> register marks', () => {
  const COMPARED = { ...LISTING, building_type: 'cihla', has_lift: true, cellar: true, floor: 2, total_floors: 5 } as ListingPublic;
  const identity = (registerMark?: (fact: string) => JSX.Element) =>
    render(
      <MemoryRouter>
        <PropertyIdentity listing={COMPARED} registerMark={registerMark} />
      </MemoryRouter>,
    );

  it('asks the page for a mark beside each fact the register states too, and only those', () => {
    identity((fact) => <i data-testid="mark">{fact}</i>);
    expect(screen.getAllByTestId('mark').map((m) => m.textContent)).toEqual(['floors', 'construction', 'lift']);
    expect(screen.getByText('Lift').closest('li')).toContainElement(screen.getByText('lift'));
    expect(screen.getByText('Cellar').closest('li')!.querySelector('[data-testid="mark"]')).toBeNull();
  });

  it('marks nothing on a surface that passes no mark (Estimation Detail)', () => {
    identity();
    expect(screen.queryByTestId('mark')).toBeNull();
  });
});
