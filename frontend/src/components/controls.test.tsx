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

import { CollapsibleGroup, DecimalField, NumberCell, Segmented, SegmentedBar, TextBox } from './controls';

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

/* THE pick-one control (operator ruling 2026-10-27): one joined track, the chosen
 * segment filled copper. Every pick-one group in the app draws it, so its modes
 * are pinned here once. */
describe('<SegmentedBar>', () => {
  const STATUS = [
    { value: 'any', label: 'Vše' },
    { value: 'active', label: 'Aktivní' },
    { value: 'inactive', label: 'Neaktivní' },
  ];
  const CHOSEN = ['bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]'];
  const seg = (name: string) => screen.getByRole('button', { name });

  /* The Browse sidebar's Status bar and the amenity rows, byte for byte. */
  it('keeps the fill and compact bars exactly as their callers drew them', () => {
    const { rerender } = render(<SegmentedBar fill label="Stav" options={STATUS} value="any" onChange={() => {}} />);
    const track = screen.getByRole('group', { name: 'Stav' });
    expect(track).toHaveAttribute('class',
      'grid w-full gap-0.5 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] p-0.5');
    expect(track).toHaveStyle({ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' });
    expect(seg('Vše')).toHaveAttribute('class',
      'px-2 py-1 text-xs whitespace-nowrap rounded-[var(--radius-xs)] transition-colors bg-[var(--color-copper)] text-[var(--color-on-copper)]');
    expect(seg('Aktivní')).toHaveAttribute('class',
      'px-2 py-1 text-xs whitespace-nowrap rounded-[var(--radius-xs)] transition-colors text-[var(--color-ink-3)] hover:text-[var(--color-ink-2)]');
    expect(seg('Vše')).toHaveAttribute('aria-pressed', 'true');
    expect(seg('Aktivní')).toHaveAttribute('aria-pressed', 'false');

    rerender(<SegmentedBar label="Stav" options={STATUS} value="any" onChange={() => {}} />);
    expect(screen.getByRole('group', { name: 'Stav' })).toHaveClass('inline-grid');
    expect(seg('Vše')).toHaveClass('px-2.5', 'py-0.5', 'text-[0.7rem]');
  });

  it('sets the value clicked, the lit one included', () => {
    const onChange = vi.fn();
    render(<SegmentedBar options={STATUS} value="any" onChange={onChange} />);
    fireEvent.click(seg('Aktivní'));
    fireEvent.click(seg('Vše'));
    expect(onChange.mock.calls).toEqual([['active'], ['any']]);
  });

  /* Brokers' and Outreach's Vše, the Valuo editor's "neuvedeno". */
  it('takes null as a real option', () => {
    const onChange = vi.fn();
    const opts = [{ value: null, label: 'Vše' }, { value: 'prodej', label: 'Prodej' }];
    const { rerender } = render(<SegmentedBar options={opts} value={'prodej' as string | null} onChange={onChange} />);
    fireEvent.click(seg('Vše'));
    expect(onChange).toHaveBeenLastCalledWith(null);
    rerender(<SegmentedBar options={opts} value={null} onChange={onChange} />);
    expect(seg('Vše')).toHaveAttribute('aria-pressed', 'true');
    expect(seg('Prodej')).toHaveAttribute('aria-pressed', 'false');
  });

  /* "Listing for" has no Vše (ruling 2026-10-20): no deal lights nothing, and the lit deal releases. */
  it('in deselectable mode lights nothing for none and releases the lit segment', () => {
    const onChange = vi.fn();
    const deals = [{ value: 'pronajem', label: 'Pronájem' }, { value: 'prodej', label: 'Prodej' }];
    const { rerender } = render(
      <SegmentedBar deselectable options={deals} value={null as string | null} onChange={onChange} />,
    );
    for (const b of screen.getAllByRole('button')) expect(b).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(seg('Prodej'));
    expect(onChange).toHaveBeenLastCalledWith('prodej');
    rerender(<SegmentedBar deselectable options={deals} value={'prodej' as string | null} onChange={onChange} />);
    expect(seg('Prodej')).toHaveClass(...CHOSEN);
    fireEvent.click(seg('Prodej'));
    expect(onChange).toHaveBeenLastCalledWith(null);
    fireEvent.click(seg('Pronájem'));
    expect(onChange).toHaveBeenLastCalledWith('pronajem');
  });

  it('refuses deselectable where none is not a value', () => {
    // @ts-expect-error — a bar whose values exclude null cannot release to null
    render(<SegmentedBar deselectable options={STATUS} value="any" onChange={() => {}} />);
    expect(seg('Vše')).toBeInTheDocument();
  });

  it('disables one option with its reason, or the whole control', () => {
    const onChange = vi.fn();
    const opts = [
      { value: 'table', label: 'Tabulka' },
      { value: 'box', label: 'Krabice', disabled: true, title: 'Jen pro jednu hodnotu' },
    ];
    const { rerender } = render(<SegmentedBar options={opts} value="table" onChange={onChange} />);
    expect(seg('Krabice')).toBeDisabled();
    expect(seg('Krabice')).toHaveAttribute('title', 'Jen pro jednu hodnotu');
    expect(seg('Krabice')).toHaveClass('opacity-50', 'cursor-not-allowed');
    expect(seg('Krabice')).not.toHaveClass('hover:text-[var(--color-ink-2)]');
    expect(seg('Tabulka')).toBeEnabled();
    fireEvent.click(seg('Krabice'));
    expect(onChange).not.toHaveBeenCalled();

    rerender(<SegmentedBar disabled options={opts} value="table" onChange={onChange} />);
    for (const b of screen.getAllByRole('button')) expect(b).toBeDisabled();
    expect(seg('Tabulka')).toHaveClass(...CHOSEN, 'opacity-50');
  });

  it('draws a glyph before the label, outside the name', () => {
    const opts = [{ value: 'sm', label: 'Malé', glyph: <svg data-testid="glyph" aria-hidden /> }];
    render(<SegmentedBar options={opts} value="sm" onChange={() => {}} />);
    expect(seg('Malé')).toHaveAccessibleName('Malé');
    expect(seg('Malé').firstChild).toBe(screen.getByTestId('glyph'));
    expect(seg('Malé')).toHaveClass('inline-flex', 'items-center', 'gap-1.5');
  });

  /* The Valuo editor: the form box is the TRACK, so the bar reads as one box. */
  it('puts the form box and the aria on the track', () => {
    const { rerender } = render(
      <SegmentedBar label="Novostavba" options={STATUS} value="any" onChange={() => {}} box="absent"
        aria={{ 'aria-invalid': true, 'aria-describedby': 'why' }} />,
    );
    const track = screen.getByRole('group', { name: 'Novostavba' });
    expect(track).toHaveClass('h-[2.125rem]', 'border-dashed', 'aria-[invalid=true]:border-[var(--color-brick)]');
    expect(track).toHaveAttribute('aria-invalid', 'true');
    expect(track).toHaveAttribute('aria-describedby', 'why');
    for (const b of screen.getAllByRole('button')) expect(b).not.toHaveClass('h-[2.125rem]');
    rerender(<SegmentedBar label="Novostavba" options={STATUS} value="any" onChange={() => {}} box="present" />);
    expect(screen.getByRole('group', { name: 'Novostavba' })).not.toHaveClass('border-dashed');
  });

  /* Long labels (emails, counts) each keep their width; the group is a scroller
   * that keeps 4px for the focus ring and gives it back, as the tab strip does,
   * so it stays the root its callers lay out, and the track inside it is drawn. */
  it('in hug mode sizes each segment to its label and scrolls sideways', () => {
    render(<SegmentedBar hug label="Fronta" options={STATUS} value="any" onChange={() => {}} />);
    const group = screen.getByRole('group', { name: 'Fronta' });
    expect(group).toHaveClass('overflow-x-auto', 'p-1', '-m-1', 'min-w-0', 'pointer-events-none');
    const track = group.firstElementChild as HTMLElement;
    expect(track).toHaveClass('inline-flex', 'pointer-events-auto', 'border-[var(--color-rule)]');
    expect(track).not.toHaveClass('grid', 'inline-grid');
    expect(track.style.gridTemplateColumns).toBe('');
    for (const b of screen.getAllByRole('button')) {
      expect(b.parentElement).toBe(track);
      expect(b).toHaveClass('shrink-0', 'px-2', 'py-1', 'text-xs');
    }
    expect(seg('Vše')).toHaveClass(...CHOSEN);
  });

  it('in hug mode puts the form box on the track and the aria on the group', () => {
    render(
      <SegmentedBar hug label="Druh" options={STATUS} value="any" onChange={() => {}} box="absent"
        aria={{ 'aria-invalid': true }} />,
    );
    const group = screen.getByRole('group', { name: 'Druh' });
    expect(group).toHaveAttribute('aria-invalid', 'true');
    expect(group.firstElementChild).toHaveClass('h-[2.125rem]', 'border-dashed', '[[aria-invalid=true]>&]:border-[var(--color-brick)]');
  });

  /* The phone's own screens: colour there only ever means a stage. */
  it('in the phone tone marks the chosen segment ink on the inset well, never copper', () => {
    render(<SegmentedBar tone="phone" label="Řadit" options={STATUS} value="any" onChange={() => {}} />);
    const track = screen.getByRole('group', { name: 'Řadit' });
    expect(seg('Vše')).toHaveClass('bg-[var(--color-inset)]', 'text-[var(--color-ink)]');
    expect(seg('Aktivní')).toHaveClass('text-[var(--color-ink-2)]');
    expect(track).toHaveClass('h-8', 'border-[var(--color-rule-strong)]');
    expect(track.outerHTML).not.toContain('copper');
    for (const b of screen.getAllByRole('button')) expect(b).toHaveClass('text-[13px]');
  });
});

/* Segmented is the joined bar (ruling 2026-10-27): its callers (Pipeline Stav,
 * Brokers, the Valuo editor, …) became one bar at once; `variant` is a no-op. */
describe('<Segmented>', () => {
  const OPTIONS = [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }];

  it('draws the one hugging bar, the chosen segment filled copper whatever the variant', () => {
    const { rerender } = render(<Segmented label="Stav" options={OPTIONS} value="a" onChange={() => {}} />);
    const soft = screen.getByRole('button', { name: 'A' }).className;
    expect(screen.getByRole('group', { name: 'Stav' }).firstElementChild).toHaveClass('inline-flex', 'border-[var(--color-rule)]');
    expect(screen.getByRole('button', { name: 'A' })).toHaveClass('bg-[var(--color-copper)]', 'text-[var(--color-on-copper)]');
    expect(screen.getByRole('button', { name: 'B' })).toHaveAttribute('aria-pressed', 'false');
    rerender(<Segmented label="Stav" options={OPTIONS} value="a" onChange={() => {}} variant="solid" />);
    expect(screen.getByRole('button', { name: 'A' }).className).toBe(soft);
  });

  it('sets the value clicked, null included', () => {
    const onChange = vi.fn();
    render(<Segmented options={[{ value: null, label: 'Vše' }, ...OPTIONS]} value="a" onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Vše' }));
    fireEvent.click(screen.getByRole('button', { name: 'A' }));
    expect(onChange.mock.calls).toEqual([[null], ['a']]);
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
    render(
      <>
        <Segmented label="Druh" options={[{ value: 'a', label: 'A' }]} value="b" onChange={() => {}} />
        <DecimalField label="Nájem" value={1} onChange={() => {}} suffix="Kč" className="mt-1.5" />
        <NumberCell value={null} placeholder="" onChange={() => {}} ariaLabel="Cena" />
      </>,
    );
    expect(screen.getByRole('group', { name: 'Druh' }).firstElementChild).toHaveAttribute('class',
      'inline-flex pointer-events-auto gap-0.5 rounded-[var(--radius-sm)] border border-[var(--color-rule)] bg-[var(--color-paper-2)] p-0.5');
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
    expect(group).toHaveAttribute('aria-invalid', 'true');
    expect(group.firstElementChild).toHaveClass('h-[2.125rem]', 'border-dashed', '[[aria-invalid=true]>&]:border-[var(--color-brick)]');
    const area = screen.getByRole('textbox', { name: 'Plocha' });
    expect(area).toHaveClass('h-[2.125rem]', 'pl-2', 'pr-8', 'py-1.5', 'aria-[invalid=true]:border-[var(--color-brick)]');
    expect(area).not.toHaveClass('px-3', 'py-2', 'border-dashed');
    expect(area.parentElement).toHaveClass('relative');
    expect(screen.getByText('m²')).toHaveClass('absolute', 'right-2', 'top-1/2', '-translate-y-1/2', 'pointer-events-none');
    expect(screen.getByRole('textbox', { name: 'Poloha' })).toHaveAttribute('class', `${INSET} h-[2.125rem] aria-[invalid=true]:border-[var(--color-brick)] border-dashed`);
  });
});
