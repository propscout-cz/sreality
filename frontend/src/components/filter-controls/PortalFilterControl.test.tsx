/* PortalFilterControl — one chip per portal over the shared cyclePortal
 * transition; the "any ad / active ads only" switch exists only while
 * something is excluded. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

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
});
