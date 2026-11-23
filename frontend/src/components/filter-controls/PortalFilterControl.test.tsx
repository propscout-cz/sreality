/* PortalFilterControl — one chip per portal over the shared cyclePortal
 * transition; the "any ad / active ads only" switch exists only while
 * something is excluded. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { NO_PORTALS } from '@/lib/portals';

import { PortalFilterControl } from './PortalFilterControl';

describe('<PortalFilterControl>', () => {
  it('cycles a chip through the shared transition', () => {
    const onChange = vi.fn();
    const { rerender } = render(<PortalFilterControl value={NO_PORTALS} onChange={onChange} />);
    expect(screen.queryByRole('group', { name: 'Which ads exclude a property' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Sreality' }));
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_PORTALS, portals: ['sreality'] });

    const out = { ...NO_PORTALS, portalsExcluded: ['sreality'] };
    rerender(<PortalFilterControl value={out} onChange={onChange} />);
    const chip = screen.getByRole('button', { name: 'Sreality (excluded)' });
    expect(chip).toHaveTextContent('− Sreality');
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(chip);
    expect(onChange).toHaveBeenLastCalledWith(NO_PORTALS);
  });

  it('offers "active ads only" once a portal is excluded', () => {
    const onChange = vi.fn();
    const out = { ...NO_PORTALS, portalsExcluded: ['sreality'] };
    render(<PortalFilterControl value={out} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'active ads only' }));
    expect(onChange).toHaveBeenLastCalledWith({ ...out, portalsExcludedActiveOnly: true });
  });

  /* A pick-one is the one joined bar (operator ruling 2026-10-27), not two chips. */
  it('draws "Exclude by" as the joined bar, the chosen side filled copper', () => {
    const onChange = vi.fn();
    const out = { ...NO_PORTALS, portalsExcluded: ['sreality'] };
    render(<PortalFilterControl value={out} onChange={onChange} />);
    const group = screen.getByRole('group', { name: 'Which ads exclude a property' });
    const [any, active] = within(group).getAllByRole('button');
    expect([any.textContent, active.textContent]).toEqual(['any ad', 'active ads only']);
    expect(any.parentElement).toBe(active.parentElement);
    expect(any.parentElement).toHaveClass('border', 'bg-[var(--color-paper-2)]');
    expect(any).not.toHaveClass('border');
    expect(any).toHaveAttribute('aria-pressed', 'true');
    expect(any).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(screen.getByText('Exclude by')).toBeInTheDocument();
    fireEvent.click(any);
    expect(onChange).toHaveBeenLastCalledWith({ ...out, portalsExcludedActiveOnly: false });
  });
});
