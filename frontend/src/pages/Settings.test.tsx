/* Settings › Appearance: Light | Dark | System is a pick-one, so it is the ONE
 * joined bar (operator ruling 2026-10-27, system.md § Option groups), its glyphs
 * kept; the colour-scheme swatches stay tiles (a text bar loses the preview). */
import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { ThemeToggle } from './Settings';

describe('<Settings> appearance', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-theme');
    localStorage.clear();
  });

  it('draws light or dark as one joined bar with its glyphs, the chosen segment filled copper', () => {
    render(<ThemeToggle />);
    const bar = screen.getByRole('group', { name: 'Light or dark' });
    expect(bar.firstElementChild).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
    const segments = within(bar).getAllByRole('button');
    expect(segments.map((b) => b.textContent)).toEqual(['Light', 'Dark', 'System']);
    for (const b of segments) {
      expect(b.querySelector('svg')).not.toBeNull();
      expect(b).not.toHaveClass('border');
    }
    expect(within(bar).getByRole('button', { name: 'Light' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(bar).getByRole('button', { name: 'Light' })).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');

    fireEvent.click(within(bar).getByRole('button', { name: 'Dark' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(within(bar).getByRole('button', { name: 'Dark' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps the colour schemes as swatch tiles', () => {
    render(<ThemeToggle />);
    const med = screen.getByRole('button', { name: 'Měď' });
    expect(med).toHaveClass('border');
    expect(med.closest('[role="group"]')).toBeNull();
  });
});
