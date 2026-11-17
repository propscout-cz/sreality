import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { loopSlots, useLoopStrip } from './useLoopStrip';

function Strip({ count }: { count: number }) {
  const { ref, handlers } = useLoopStrip(count);
  return <div ref={ref} data-testid="strip" {...handlers} />;
}

describe('useLoopStrip', () => {
  it('lays the photos between a copy of the last and a copy of the first', () => {
    expect(loopSlots(3).map((s) => `${s.index}${s.copy ? 'c' : ''}`)).toEqual(['2c', '0', '1', '2', '0c']);
    expect(loopSlots(1)).toEqual([{ index: 0, copy: false }]);
  });

  it('keeps its place when the photo count changes under it', () => {
    const { getByTestId, rerender } = render(<Strip count={4} />);
    const el = getByTestId('strip');
    Object.defineProperty(el, 'clientWidth', { configurable: true, value: 390 });
    Object.defineProperty(el, 'scrollLeft', { configurable: true, writable: true, value: 3 * 390 });
    rerender(<Strip count={6} />);
    expect(el.scrollLeft).toBe(3 * 390);
  });
});
