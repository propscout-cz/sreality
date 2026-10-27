/* THE ONE AGREEMENT SWITCH (Rozhodnutí, Soudce ×2) is a pick-one, so it is the ONE
 * joined bar (operator ruling 2026-10-27, system.md § Option groups), drawn through
 * Segmented: "Neshody" first with its count, the chosen segment filled copper. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { AgreementSwitch } from './FilterBar';

describe('<AgreementSwitch>', () => {
  it('is one named joined bar, Neshody first, the chosen segment filled copper', () => {
    render(<AgreementSwitch label="Engine" facet={{ disagrees: 4, agrees: 9 }} value="" onChange={() => {}} />);
    const bar = screen.getByRole('group', { name: 'Engine' });
    expect(bar.firstElementChild).toHaveClass('inline-flex', 'border', 'bg-[var(--color-paper-2)]');
    expect(within(bar).getAllByRole('button').map((b) => b.textContent)).toEqual(['Neshody (4)', 'Vše', 'Souhlasí (9)']);
    const all = within(bar).getByRole('button', { name: 'Vše' });
    expect(all).toHaveAttribute('aria-pressed', 'true');
    expect(all).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    for (const b of within(bar).getAllByRole('button')) expect(b).not.toHaveClass('border');
  });

  it('sets the agreement clicked, Vše being the empty value', () => {
    const onChange = vi.fn();
    render(<AgreementSwitch label="Engine" facet={undefined} value="disagrees" onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Vše' }));
    expect(onChange).toHaveBeenCalledWith('');
    fireEvent.click(screen.getByRole('button', { name: 'Souhlasí' }));
    expect(onChange).toHaveBeenCalledWith('agrees');
  });
});
