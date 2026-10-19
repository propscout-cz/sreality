/* CollapsibleGroup's accessible name.
 *
 * The copper "there are filters set under here" dot used to carry
 * aria-label="has active filters" on a bare <span> INSIDE the accordion
 * trigger, so the button announced "Essentials has active filters" — the
 * state was folded into the name and `getByRole('button', {name})` stopped
 * matching the band as soon as a filter was set. The dot is decorative now;
 * the state rides on aria-describedby instead. */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { CollapsibleGroup, DecimalField, Segmented } from './controls';

describe('<CollapsibleGroup>', () => {
  it('is named by its band title alone, with or without active filters', () => {
    const { rerender } = render(
      <CollapsibleGroup title="Essentials">
        <p>panel</p>
      </CollapsibleGroup>,
    );
    expect(screen.getByRole('button', { name: 'Essentials' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );

    rerender(
      <CollapsibleGroup title="Essentials" active>
        <p>panel</p>
      </CollapsibleGroup>,
    );
    const trigger = screen.getByRole('button', { name: 'Essentials' });
    expect(trigger).toHaveAccessibleName('Essentials');
    // Nothing is lost: the state is still announced, as a description.
    expect(trigger).toHaveAccessibleDescription('has active filters');
  });
});

/* The `variant` pass-through: a bar that mixes Segmented with MultiselectChips
 * (which hardcodes solid) needs Segmented to reach solid too — without moving
 * the callers that never asked for it. */
describe('<Segmented>', () => {
  const OPTIONS = [{ value: 'a', label: 'A' }];
  const cls = () => screen.getByRole('button', { name: 'A' }).className;

  it('stays soft by default and forwards solid when asked', () => {
    const { rerender } = render(<Segmented options={OPTIONS} value="a" onChange={() => {}} />);
    expect(cls()).toContain('bg-[var(--color-copper-soft)]');
    rerender(
      <Segmented options={OPTIONS} value="a" onChange={() => {}} variant="solid" />,
    );
    expect(cls()).toContain('bg-[var(--color-copper)]');
  });
});

/* The one decimal input (RunPanel's estimate inputs; the Valuo editor). */
describe('<DecimalField>', () => {
  const type = (text: string, props: { readOnly?: boolean } = {}) => {
    const onChange = vi.fn();
    const { unmount } = render(
      <DecimalField label="Plocha" value={40} onChange={onChange} suffix="m²" {...props} />,
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Plocha' }), { target: { value: text } });
    unmount();
    return onChange.mock.calls;
  };

  it('reads a comma as the decimal point and an empty box as null', () => {
    expect(type(' 52,5 ')).toEqual([[52.5]]);
    expect(type('')).toEqual([[null]]);
  });

  it('ignores text that is not a number, and every edit when read-only', () => {
    expect(type('52 m')).toEqual([]);
    expect(type('52', { readOnly: true })).toEqual([]);
  });
});
