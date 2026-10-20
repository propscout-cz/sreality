/* One tab is not a choice: since the Table tab went (2026-10-20) the broker modal,
 * which turns Stats off, is left with Properties alone, and draws no strip. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import Tabs from './Tabs';

describe('<Tabs>', () => {
  it('draws a strip of two and switches on a click', () => {
    const onChange = vi.fn();
    render(
      <Tabs
        tabs={[{ key: 'map', label: 'Properties', badge: '12' }, { key: 'stats', label: 'Stats' }]}
        active="map"
        onChange={onChange}
      />,
    );
    expect(screen.getAllByRole('tab')).toHaveLength(2);
    expect(screen.getByRole('tab', { name: /Properties/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: 'Stats' }));
    expect(onChange).toHaveBeenCalledWith('stats');
  });

  it('draws nothing for a single tab', () => {
    const { container } = render(
      <Tabs tabs={[{ key: 'map', label: 'Properties' }]} active="map" onChange={() => {}} />,
    );
    expect(screen.queryByRole('tablist')).toBeNull();
    expect(container).toBeEmptyDOMElement();
  });
});
