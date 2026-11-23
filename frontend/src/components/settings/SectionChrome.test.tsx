/* The settings pages' Compact | Detailed switch is a pick-one, so it is the ONE
 * joined bar (operator ruling 2026-10-27, system.md § Option groups). */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { InfoModeToggle } from './SectionChrome';

describe('<InfoModeToggle>', () => {
  it('is one joined bar named for its choice, the chosen segment filled copper', () => {
    render(<InfoModeToggle expanded={false} onChange={() => {}} />);
    const bar = screen.getByRole('group', { name: 'Description density' });
    expect(bar.firstElementChild).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
    const compact = within(bar).getByRole('button', { name: 'Compact' });
    expect(compact).toHaveAttribute('aria-pressed', 'true');
    expect(compact).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(compact).not.toHaveClass('border');
    expect(within(bar).getByRole('button', { name: 'Detailed' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('sets the mode clicked, the lit one included', () => {
    const onChange = vi.fn();
    render(<InfoModeToggle expanded onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Compact' }));
    fireEvent.click(screen.getByRole('button', { name: 'Detailed' }));
    expect(onChange.mock.calls).toEqual([[false], [true]]);
  });
});
