/* The Costs chart's Hour | Day switch is a pick-one, so it is the ONE joined bar
 * (operator ruling 2026-10-27, system.md § Option groups): a pressed state a
 * screen reader hears, and --color-on-copper text (paper-3 failed in the dark
 * schemes). */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import GrainToggle from './GrainToggle';

describe('<GrainToggle>', () => {
  it('is one named joined bar, the chosen segment filled copper with on-copper text', () => {
    render(<GrainToggle grain="day" onChange={() => {}} />);
    const bar = screen.getByRole('group', { name: 'Granularity' });
    expect(bar).toHaveClass('inline-grid', 'border', 'bg-[var(--color-paper-2)]');
    const day = screen.getByRole('button', { name: 'Day' });
    expect(day).toHaveAttribute('aria-pressed', 'true');
    expect(day).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(day).not.toHaveAttribute('style');
    expect(screen.getByRole('button', { name: 'Hour' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('sets the grain clicked', () => {
    const onChange = vi.fn();
    render(<GrainToggle grain="day" onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Hour' }));
    expect(onChange).toHaveBeenCalledWith('hour');
  });
});
