/* SizeToggle is the joined bar (SegmentedBar) with a glyph per step: the
 * deal-pipeline board's Malé | Střední | Velké and, through ImageSizeToggle,
 * every small/large photo switch. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import SizeToggle, { LargeImageGlyph, MediumImageGlyph, SmallImageGlyph } from './SizeToggle';

const STEPS = [
  { value: 'sm', label: 'Malé', title: 'Víc karet', glyph: <SmallImageGlyph /> },
  { value: 'md', label: 'Střední', title: 'Fotka nahoře', glyph: <MediumImageGlyph /> },
  { value: 'lg', label: 'Velké', title: 'Velká fotka', glyph: <LargeImageGlyph /> },
] as const;

describe('<SizeToggle>', () => {
  it('draws the joined bar: one named track, the chosen step filled copper', () => {
    render(<SizeToggle label="Velikost karet" value="md" onChange={() => {}} steps={STEPS} />);
    const track = screen.getByRole('group', { name: 'Velikost karet' }).firstElementChild;
    expect(track).toHaveClass('inline-flex', 'rounded-[var(--radius-sm)]', 'bg-[var(--color-paper-2)]');
    const md = screen.getByRole('button', { name: 'Střední' });
    expect(md).toHaveAttribute('aria-pressed', 'true');
    expect(md).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(screen.getByRole('button', { name: 'Malé' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('keeps each step its glyph and its tooltip, outside the name', () => {
    render(<SizeToggle label="Velikost karet" value="sm" onChange={() => {}} steps={STEPS} />);
    const lg = screen.getByRole('button', { name: 'Velké' });
    expect(lg).toHaveAttribute('title', 'Velká fotka');
    expect(lg.querySelector('svg')).toHaveAttribute('aria-hidden');
  });

  it('reports the step clicked', () => {
    const onChange = vi.fn();
    render(<SizeToggle label="Velikost karet" value="sm" onChange={onChange} steps={STEPS} />);
    fireEvent.click(screen.getByRole('button', { name: 'Velké' }));
    fireEvent.click(screen.getByRole('button', { name: 'Malé' }));
    expect(onChange.mock.calls).toEqual([['lg'], ['sm']]);
  });
});
