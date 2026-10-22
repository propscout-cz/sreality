/* The map rail opens one panel at a time; the same button, the panel's close,
 * Escape and a click outside the rail close it. Its geometry (beside the rail,
 * a bottom sheet under 520 px) is CSS, measured in a real browser, not here. */

import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { RailButton, RailFlyout, RailGroup, useRailPanel } from './MapRail';

function Rail() {
  const rail = useRailPanel<'a' | 'b'>();
  return (
    <div>
      <p>mapa</p>
      <div ref={rail.rootRef}>
        <RailGroup>
          <RailButton label="Vrstvy" expanded={rail.open === 'a'} badge={2} onClick={() => rail.toggle('a')}>
            V
          </RailButton>
          <RailButton label="Piny a ceny" expanded={rail.open === 'b'} onClick={() => rail.toggle('b')}>
            P
          </RailButton>
        </RailGroup>
        {rail.open === 'a' && <RailFlyout title="Vrstvy" onClose={rail.close}>obsah A</RailFlyout>}
        {rail.open === 'b' && <RailFlyout title="Piny a ceny" onClose={rail.close}>obsah B</RailFlyout>}
      </div>
    </div>
  );
}

const button = (name: string) => screen.getByRole('button', { name });
const panel = (name: string) => screen.queryByRole('region', { name });

describe('<MapRail>', () => {
  it('opens one panel at a time, and the same button closes it', () => {
    render(<Rail />);
    fireEvent.click(button('Vrstvy'));
    expect(panel('Vrstvy')).toHaveTextContent('obsah A');
    expect(button('Vrstvy')).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(button('Piny a ceny'));
    expect(panel('Vrstvy')).toBeNull();
    expect(panel('Piny a ceny')).toHaveTextContent('obsah B');
    fireEvent.click(button('Piny a ceny'));
    expect(panel('Piny a ceny')).toBeNull();
  });

  it('closes on its close button, on Escape and on a click outside the rail', () => {
    render(<Rail />);
    fireEvent.click(button('Vrstvy'));
    fireEvent.click(button('Zavřít'));
    expect(panel('Vrstvy')).toBeNull();

    fireEvent.click(button('Vrstvy'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(panel('Vrstvy')).toBeNull();

    fireEvent.click(button('Vrstvy'));
    fireEvent.pointerDown(screen.getByText('obsah A'));
    expect(panel('Vrstvy')).not.toBeNull();
    fireEvent.pointerDown(screen.getByText('mapa'));
    expect(panel('Vrstvy')).toBeNull();
  });

  it('counts what is on beside the button', () => {
    render(<Rail />);
    expect(button('Vrstvy')).toHaveTextContent('2');
  });
});
