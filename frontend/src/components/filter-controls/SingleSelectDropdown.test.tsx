/* SingleSelectDropdown — <select> with an explicit `any` placeholder.
 *
 * Behaviour pinned:
 *   - the placeholder is the first option and represents `null`
 *   - selecting the placeholder emits null
 *   - selecting a real option emits the matched value (typed back via
 *     options.find — the wire value passes through the DOM as a
 *     string, the component maps it back to its original number /
 *     string type)
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { SingleSelectDropdown } from './SingleSelectDropdown';

describe('<SingleSelectDropdown>', () => {
  it('renders the placeholder when value is null', () => {
    render(
      <SingleSelectDropdown label="Ownership"
        value={null}
        options={[
          { value: 'osobni', label: 'Osobní' },
          { value: 'druzstevni', label: 'Družstevní' },
        ]}
        onChange={vi.fn()}
        placeholder="any"
      />,
    );
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('emits a string value when a string option is picked', () => {
    const onChange = vi.fn();
    render(
      <SingleSelectDropdown label="Ownership"
        value={null}
        options={[
          { value: 'osobni', label: 'Osobní' },
          { value: 'druzstevni', label: 'Družstevní' },
        ]}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'druzstevni' },
    });
    expect(onChange).toHaveBeenCalledWith('druzstevni');
  });

  it('preserves numeric option values across the DOM round-trip', () => {
    const onChange = vi.fn();
    render(
      <SingleSelectDropdown label="Ownership"
        value={null}
        options={[
          { value: 6, label: '3+kk' },
          { value: 7, label: '4+1' },
        ]}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: '6' },
    });
    // The wire value comes back from the <select> as a string; the
    // component looks it up in options and emits the original number.
    expect(onChange).toHaveBeenCalledWith(6);
  });

  it('emits null when the placeholder is selected', () => {
    const onChange = vi.fn();
    render(
      <SingleSelectDropdown label="Ownership"
        value={6}
        options={[
          { value: 6, label: '3+kk' },
        ]}
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: '' },
    });
    expect(onChange).toHaveBeenCalledWith(null);
  });
});

/* The select used to have an accessible name of "": `placeholder` renders only
 * as the blank option's text, and an option's text is a VALUE, never the
 * control's name. The caption now arrives as words. */
describe('<SingleSelectDropdown> accessible name', () => {
  it('is named by the caption it is handed', () => {
    render(
      <SingleSelectDropdown
        label="Ownership"
        value={null}
        options={[{ value: 'osobni', label: 'Osobní' }]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole('combobox', { name: 'Ownership' })).toBeInTheDocument();
  });

  /* Every filter's select keeps its classes; the form box (the Valuo editor) adds one height and its states. */
  it('keeps its classes without a box, and takes the form box with one', () => {
    const base = 'w-full px-2 py-1.5 text-sm rounded-[var(--radius-sm)] bg-[var(--color-inset)] border border-[var(--color-rule)] text-[var(--color-ink)]';
    const { rerender } = render(<SingleSelectDropdown label="Stav" value={null} options={[]} onChange={vi.fn()} />);
    expect(screen.getByRole('combobox')).toHaveAttribute('class', base);
    rerender(<SingleSelectDropdown label="Stav" value={null} options={[]} onChange={vi.fn()} box="absent" />);
    expect(screen.getByRole('combobox')).toHaveAttribute('class',
      `${base} h-[2.125rem] aria-[invalid=true]:border-[var(--color-brick)] border-dashed`);
  });
});
