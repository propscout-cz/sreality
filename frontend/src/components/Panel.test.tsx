/* The section card (operator ruling 2026-10-28, system.md § Section cards C1–C10). Pins, as class
 * strings so a drift shows: the card (C1), the L2 title (C2), the title bar (C3), the body (C4), the count,
 * note and actions on the title line (C6), a group (C7) and an empty body (C10). */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import Panel, { PanelEmpty, PanelGroup } from './Panel';

describe('<Panel>, the section card', () => {
  it('is a region named by its title alone, on paper-2 with a rule border and no shadow (C1)', () => {
    render(
      <Panel title="Portals" count={7} id="section-portals">
        <p>matrix</p>
      </Panel>,
    );
    const region = screen.getByRole('region', { name: 'Portals' });
    expect(region.id).toBe('section-portals');
    expect(region.getAttribute('class')).toBe(
      'scroll-mt-20 rounded-[var(--radius-md)] border border-[var(--color-rule)] bg-[var(--color-paper-2)]',
    );
    expect(region.getAttribute('class')).not.toMatch(/shadow/);
  });

  it('draws the title as an L2 Fraunces h2 in a title bar over a rule-soft line (C2, C3)', () => {
    render(
      <Panel title="Vývoj ceny">
        <p>chart</p>
      </Panel>,
    );
    const heading = screen.getByRole('heading', { level: 2, name: 'Vývoj ceny' });
    expect(heading.getAttribute('class')).toBe('font-display text-[17px] leading-6 font-semibold text-[var(--color-ink)]');
    expect(heading.getAttribute('class')).not.toMatch(/uppercase|tracking/);
    expect(heading.parentElement?.getAttribute('class')).toBe(
      'flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-[var(--color-rule-soft)] px-4 pt-[13px] pb-3 sm:px-5',
    );
  });

  it('pads the body 16/20/18 and stacks its blocks 12 px apart (C4)', () => {
    render(
      <Panel title="Valuo">
        <p>figure</p>
      </Panel>,
    );
    expect(screen.getByText('figure').parentElement?.getAttribute('class')).toBe('space-y-3 px-4 pt-4 pb-[18px] sm:px-5');
  });

  it('puts a bare count beside the title, outside its name, and the note and actions pushed right (C6)', () => {
    render(
      <Panel title="Registered sales" count={7} note="realized prices, not asking prices" accessory={<button type="button">Nový odhad</button>}>
        <p>rows</p>
      </Panel>,
    );
    const heading = screen.getByRole('heading', { name: 'Registered sales' });
    const count = screen.getByText('7');
    expect(heading).not.toContainElement(count);
    expect(count.getAttribute('class')).toBe('font-mono text-[13px] tabular-nums text-[var(--color-ink-3)]');
    const right = screen.getByRole('button', { name: 'Nový odhad' }).parentElement;
    expect(right?.getAttribute('class')).toMatch(/\bml-auto\b/);
    expect(right).toContainElement(screen.getByText('realized prices, not asking prices'));
  });

  it('draws a count of 0, and no count when none is given', () => {
    const { unmount } = render(<Panel title="Moje fotky" count={0} />);
    expect(screen.getByText('0')).toBeInTheDocument();
    unmount();
    const { container } = render(<Panel title="Valuo" />);
    expect(container.querySelector('.font-mono')).toBeNull();
  });

  it('sets the lede as the body’s first line', () => {
    render(
      <Panel title="Engine" lede="One sentence on what this panel holds.">
        <p>rows</p>
      </Panel>,
    );
    const lede = screen.getByText('One sentence on what this panel holds.');
    expect(lede.parentElement?.firstElementChild).toBe(lede);
  });

  it('keeps the actions in the title bar', () => {
    render(
      <Panel title="Engine" accessory={<button type="button">Acknowledge</button>}>
        <p>rows</p>
      </Panel>,
    );
    const bar = screen.getByRole('heading', { name: 'Engine' }).parentElement;
    expect(bar).toContainElement(screen.getByRole('button', { name: 'Acknowledge' }));
  });
});

describe('<PanelGroup>, a group inside a card (C7)', () => {
  it('is an L3 Inter h3 with a rule-soft line above, never a box', () => {
    render(
      <Panel title="Odhady">
        <PanelGroup title="Odhad prodeje">a</PanelGroup>
        <PanelGroup title="Odhad nájmu" count={9}>b</PanelGroup>
      </Panel>,
    );
    const h3 = screen.getByRole('heading', { level: 3, name: 'Odhad nájmu' });
    expect(h3.getAttribute('class')).toBe('text-sm leading-5 font-semibold text-[var(--color-ink)]');
    const group = h3.parentElement?.parentElement;
    expect(group?.getAttribute('class')).toBe(
      'space-y-2 border-t border-[var(--color-rule-soft)] pt-3 first:border-t-0 first:pt-0',
    );
    expect(group?.getAttribute('class')).not.toMatch(/rounded|border-dashed|bg-/);
  });
});

describe('<PanelEmpty>, an empty section (C10)', () => {
  it('is one muted line and the action, inside the card that stays', () => {
    render(
      <Panel title="Moje fotky" count={0}>
        <PanelEmpty action={<button type="button">Nahrát</button>}>Zatím žádné vlastní fotky.</PanelEmpty>
      </Panel>,
    );
    expect(screen.getByRole('region', { name: 'Moje fotky' })).toBeInTheDocument();
    expect(screen.getByText('Zatím žádné vlastní fotky.').getAttribute('class')).toBe('text-sm text-[var(--color-ink-3)]');
    expect(screen.getByRole('button', { name: 'Nahrát' })).toBeInTheDocument();
  });
});
