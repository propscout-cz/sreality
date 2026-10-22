/* The admin card (wave 8, contract D3; wave 10 moved the four local Card copies onto it). Pins:
 * - a region named by its title; the panel classes, the title row and the body spacing, as
 *   class strings (the "no visual change" pin of every page that draws it);
 * - the title is an h2, in the small-caps style;
 * - the optional lede and accessory; an id makes it an anchor target. */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import Panel from './Panel';

describe('<Panel>', () => {
  it('is a region named by its title, in the admin card classes', () => {
    render(
      <Panel title="Portals" id="section-portals">
        <p>matrix</p>
      </Panel>,
    );
    const region = screen.getByRole('region', { name: 'Portals' });
    expect(region.id).toBe('section-portals');
    expect(region.getAttribute('class')).toBe(
      'scroll-mt-20 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] px-5 py-4',
    );
    const heading = screen.getByRole('heading', { level: 2, name: 'Portals' });
    expect(heading.getAttribute('class')).toBe(
      'text-[0.7rem] tracking-[0.18em] uppercase text-[var(--color-ink-3)] font-medium',
    );
    expect(heading.parentElement?.getAttribute('class')).toBe('flex items-center justify-between gap-3');
    expect(screen.getByText('matrix').parentElement?.getAttribute('class')).toBe('mt-3');
  });

  it('sets the lede under the title and the accessory at its right', () => {
    render(
      <Panel title="Engine" lede="One sentence on what this panel holds." accessory={<button type="button">Acknowledge</button>}>
        <p>rows</p>
      </Panel>,
    );
    const heading = screen.getByRole('heading', { name: 'Engine' });
    expect(heading.parentElement).toContainElement(screen.getByRole('button', { name: 'Acknowledge' }));
    expect(screen.getByText('One sentence on what this panel holds.').getAttribute('class')).toBe(
      'mt-2 text-[0.78rem] leading-relaxed text-[var(--color-ink-2)] max-w-[52rem]',
    );
  });

  it('draws no lede line when there is none', () => {
    const { container } = render(
      <Panel title="Platform">
        <p>rows</p>
      </Panel>,
    );
    expect(container.querySelectorAll('section > *')).toHaveLength(2);
  });
});
