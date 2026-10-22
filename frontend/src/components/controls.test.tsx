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

import { CollapsibleGroup, DecimalField, NumberCell, Segmented, TextBox } from './controls';

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
  const type = (text: string, props: { readOnly?: boolean; integer?: boolean } = {}) => {
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

  it('in integer mode emits a whole number, a fraction truncated like the filters always did', () => {
    expect(type('-2', { integer: true })).toEqual([[-2]]);
    expect(type('2,5', { integer: true })).toEqual([[2]]);
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

/* The form box (the Valuo editor's controls) is opt-in: every other caller keeps
 * its classes byte for byte, pinned here; `box` adds one height, the dashed
 * absent state and the brick refusal, and puts DecimalField's unit inside. */
describe('the form box', () => {
  const RULE = 'rounded-[var(--radius-sm)] border border-[var(--color-rule)] text-[var(--color-ink)]';
  const INSET = 'w-full min-w-0 px-2 py-1.5 text-sm font-mono tabular-nums rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)] placeholder:text-[var(--color-ink-4)]';

  it('leaves every control without one as it was', () => {
    const { container } = render(
      <>
        <Segmented options={[{ value: 'a', label: 'A' }]} value="b" onChange={() => {}} />
        <DecimalField label="Nájem" value={1} onChange={() => {}} suffix="Kč" className="mt-1.5" />
        <NumberCell value={null} placeholder="" onChange={() => {}} ariaLabel="Cena" />
      </>,
    );
    expect(container.querySelector('div')).toHaveAttribute('class', 'flex flex-wrap gap-1');
    expect(screen.getByRole('button', { name: 'A' })).toHaveAttribute('class',
      'px-2 py-1.5 text-xs rounded-[var(--radius-sm)] border transition-colors bg-[var(--color-paper-2)] text-[var(--color-ink-2)] border-[var(--color-rule)] hover:border-[var(--color-rule-strong)] ');
    const rent = screen.getByRole('textbox', { name: 'Nájem' });
    expect(rent.parentElement).toHaveAttribute('class', 'flex items-stretch gap-2 min-w-0 mt-1.5');
    expect(rent).toHaveAttribute('class',
      `flex-1 min-w-0 px-3 py-2 text-sm font-mono tabular-nums ${RULE} placeholder:text-[var(--color-ink-4)] bg-[var(--color-inset)]`);
    expect(screen.getByText('Kč')).toHaveAttribute('class', 'self-center text-[0.78rem] tracking-wide text-[var(--color-ink-3)]');
    expect(screen.getByRole('textbox', { name: 'Cena' })).toHaveAttribute('class', INSET);
  });

  it('draws one 34 px box, dashed while absent, brick while refused, the unit inside', () => {
    const invalid = { 'aria-invalid': true } as const;
    render(
      <>
        <Segmented label="Novostavba" options={[{ value: 'a', label: 'A' }]} value="a" onChange={() => {}} box="absent" aria={invalid} />
        <DecimalField label="Plocha" value={50} onChange={() => {}} suffix="m²" box="present" />
        <TextBox label="Poloha" box="absent" defaultValue="" />
      </>,
    );
    const group = screen.getByRole('group', { name: 'Novostavba' });
    expect(group).toHaveClass('[&[aria-invalid=true]>button]:border-[var(--color-brick)]');
    expect(screen.getByRole('button', { name: 'A' })).toHaveClass('h-[2.125rem]', 'border-dashed');
    const area = screen.getByRole('textbox', { name: 'Plocha' });
    expect(area).toHaveClass('h-[2.125rem]', 'pl-2', 'pr-8', 'py-1.5', 'aria-[invalid=true]:border-[var(--color-brick)]');
    expect(area).not.toHaveClass('px-3', 'py-2', 'border-dashed');
    expect(area.parentElement).toHaveClass('relative');
    expect(screen.getByText('m²')).toHaveClass('absolute', 'right-2', 'top-1/2', '-translate-y-1/2', 'pointer-events-none');
    expect(screen.getByRole('textbox', { name: 'Poloha' })).toHaveAttribute('class', `${INSET} h-[2.125rem] aria-[invalid=true]:border-[var(--color-brick)] border-dashed`);
  });
});
