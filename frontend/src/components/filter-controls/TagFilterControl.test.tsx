/* The tag filter's three states per chip, the portal control's shape. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { TagFilterControl, tagOptionsFor } from './TagFilterControl';

const TAGS = [
  { id: 5, name: 'k prohlídce', color: 'copper' as const },
  { id: 6, name: 'odmítnuto', color: 'brick' as const },
];

describe('<TagFilterControl>', () => {
  it('cycles a chip off → on → not → off and says which it is', () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <TagFilterControl tags={TAGS} value={null} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'k prohlídce' }));
    expect(onChange).toHaveBeenLastCalledWith({ set: null, in: [5], out: [] });

    rerender(<TagFilterControl tags={TAGS} value={{ set: null, in: [5], out: [] }} onChange={onChange} />);
    const on = screen.getByRole('button', { name: 'k prohlídce' });
    expect(on).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(on);
    expect(onChange).toHaveBeenLastCalledWith({ set: null, in: [], out: [5] });

    rerender(<TagFilterControl tags={TAGS} value={{ set: null, in: [], out: [5] }} onChange={onChange} />);
    const not = screen.getByRole('button', { name: 'k prohlídce (excluded)' });
    expect(not).toHaveTextContent('− k prohlídce');
    fireEvent.click(not);
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('explains AND only once two tags are required', () => {
    const { rerender } = render(
      <TagFilterControl tags={TAGS} value={{ set: null, in: [5], out: [] }} onChange={() => {}} />,
    );
    expect(screen.queryByText(/všechny vybrané/)).toBeNull();
    rerender(
      <TagFilterControl tags={TAGS} value={{ set: null, in: [5, 6], out: [] }} onChange={() => {}} />,
    );
    expect(screen.getByText(/všechny vybrané/)).toBeInTheDocument();
  });

  it('keeps a selected tag that no longer exists deselectable, named by id', () => {
    expect(tagOptionsFor(TAGS, { set: null, in: [99], out: [6] })).toEqual([
      ...TAGS,
      { id: 99, name: '#99', color: 'slate' },
    ]);
  });
});
