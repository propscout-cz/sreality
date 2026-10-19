/* The strip names its three numbers through the caller's caption, prints the
 * two ends and the middle with the caller's format, and places the copper
 * middle tick by its share of the span. */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import RangeStrip from './RangeStrip';

describe('<RangeStrip>', () => {
  it('shows the caption, the formatted ends and middle, and places the middle', () => {
    const { container } = render(
      <RangeStrip
        label="Odhad ceny"
        range={{ low: 100, mid: 150, high: 300 }}
        caption="min · průměr · max (Valuo)"
        format={(n) => `${n} Kč`}
      />,
    );
    expect(screen.getByText('min · průměr · max (Valuo)')).toBeInTheDocument();
    for (const text of ['100 Kč', '150 Kč', '300 Kč']) expect(screen.getByText(text)).toBeInTheDocument();
    expect(container.querySelector<HTMLElement>('[style]')?.style.left).toBe('calc(25% - 1px)');
  });

  it('reads marks in ink against the band, pinning one outside it to the end it passed', () => {
    const { container } = render(
      <RangeStrip
        range={{ low: 100, mid: 150, high: 300 }}
        caption="min · průměr · max (Valuo)"
        format={(n) => `${n} Kč`}
        marks={[{ label: 'nabídka', value: 200 }, { label: 'MF', value: 400 }]}
      />,
    );
    expect(screen.queryByText('150 Kč')).not.toBeInTheDocument(); // no label: the caller states the middle
    expect(screen.getByText('nabídka')).toBeInTheDocument();
    expect(screen.getByText('400 Kč')).toBeInTheDocument();
    const pinned = screen.getByText('MF · mimo pásmo');
    expect(pinned.className).toContain('-translate-x-full'); // grows inward from the right end
    const lefts = [...container.querySelectorAll<HTMLElement>('[style]')].map((e) => e.style.left);
    expect(lefts).toEqual(['50%', '100%', 'calc(25% - 1px)', 'calc(50% - 0.5px)', 'calc(100% - 0.5px)']);
  });
});
