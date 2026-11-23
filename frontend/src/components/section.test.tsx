/* The hairline's margin is the only thing seven copies of it disagreed on:
 * my-7 everywhere, my-5 inside the nested comparable modal. Pinning both
 * keeps `tight` from quietly becoming a no-op. */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { Hairline, SectionLabel } from './section';

describe('<Hairline>', () => {
  it('rides the section rhythm by default and tightens inside a modal', () => {
    const { container, rerender } = render(<Hairline />);
    expect(container.firstElementChild?.getAttribute('class')).toBe(
      'my-7 h-px bg-[var(--color-rule)]',
    );

    rerender(<Hairline tight />);
    expect(container.firstElementChild?.getAttribute('class')).toBe(
      'my-5 h-px bg-[var(--color-rule)]',
    );
  });
});

describe('<SectionLabel>', () => {
  it('is the L4 field label: small caps 11/14, weight 600, 0.08em, ink-3 (system.md § Section cards)', () => {
    render(<SectionLabel>First seen</SectionLabel>);
    expect(screen.getByText('First seen').getAttribute('class')).toBe(
      'text-[11px] leading-[14px] font-semibold tracking-[0.08em] uppercase text-[var(--color-ink-3)]',
    );
  });
});
