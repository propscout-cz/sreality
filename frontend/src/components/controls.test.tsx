/* CollapsibleGroup's accessible name.
 *
 * The copper "there are filters set under here" dot used to carry
 * aria-label="has active filters" on a bare <span> INSIDE the accordion
 * trigger, so the button announced "Essentials has active filters" — the
 * state was folded into the name and `getByRole('button', {name})` stopped
 * matching the band as soon as a filter was set. The dot is decorative now;
 * the state rides on aria-describedby instead. */

import { useState } from 'react';
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

  /* Typed key by key into a host that keeps the value: the box once showed
   * String(value), so "52," lost its comma and the next key gave 525. */
  it('keeps an unfinished number as typed, key by key', () => {
    const seen: Array<number | null> = [];
    function Host() {
      const [v, setV] = useState<number | null>(null);
      const onChange = (n: number | null) => {
        seen.push(n);
        setV(n);
      };
      return <DecimalField label="Plocha" value={v} onChange={onChange} />;
    }
    render(<Host />);
    const box = screen.getByRole('textbox', { name: 'Plocha' });
    for (const text of ['5', '52', '52,', '52,5', '-', '-1', '50.', '50.0']) {
      fireEvent.change(box, { target: { value: text } });
      expect(box).toHaveValue(text);
    }
    expect(seen).toEqual([5, 52, 52, 52.5, -1, 50, 50]);
  });

  it('shows the value with a decimal comma again on blur and on an outside change', () => {
    const { rerender } = render(<DecimalField label="Plocha" value={52.5} onChange={() => {}} />);
    const box = screen.getByRole('textbox', { name: 'Plocha' });
    expect(box).toHaveValue('52,5');
    fireEvent.change(box, { target: { value: '52 m' } });
    expect(box).toHaveValue('52 m');
    fireEvent.blur(box);
    expect(box).toHaveValue('52,5');
    rerender(<DecimalField label="Plocha" value={60} onChange={() => {}} />);
    expect(box).toHaveValue('60');
  });

  /* The filter forms' box (Browse, Brokers): NumberCell's look, whole numbers. */
  it('truncates when `integer`, and draws the NumberCell box when `cell`', () => {
    const onChange = vi.fn();
    render(<DecimalField cell integer label="Cena" value={null} onChange={onChange} placeholder="Kč" />);
    const box = screen.getByRole('textbox', { name: 'Cena' });
    fireEvent.change(box, { target: { value: '1500,9' } });
    expect(onChange.mock.calls).toEqual([[1500]]);
    expect(box).toHaveAttribute('inputmode', 'numeric');
    expect(box).toHaveAttribute('placeholder', 'Kč');
    expect(box).toHaveClass('w-full', 'px-2', 'py-1.5');
  });

  /* A filter box shows a value as it always did ("7.5"); only typed text gains the comma. */
  it('shows a `cell` value as String(v), and a blur leaves the typed text alone', () => {
    const { rerender } = render(<DecimalField cell label="Sleva" value={7.5} onChange={() => {}} />);
    const box = screen.getByRole('textbox', { name: 'Sleva' });
    expect(box).toHaveValue('7.5');
    fireEvent.change(box, { target: { value: '-' } });
    fireEvent.blur(box);
    expect(box).toHaveValue('-');
    rerender(<DecimalField cell label="Sleva" value={2.25} onChange={() => {}} />);
    expect(box).toHaveValue('2.25');
  });
});
