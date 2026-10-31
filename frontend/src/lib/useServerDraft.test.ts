/* useServerDraft — the one draft rule of every curation editor (D3,
 * docs/design/curation-sync.md § 3.5 Decision 10): a field follows the server
 * while untouched, keeps what the user typed, and never writes a server change
 * back. */

import { describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import { trimDraft as trim, useServerDraft } from './useServerDraft';

function field(initial: string, normalize?: (v: string) => string) {
  return renderHook(({ server }: { server: string }) => useServerDraft(server, normalize), {
    initialProps: { server: initial },
  });
}

describe('useServerDraft', () => {
  it('follows the server while untouched, focused or not', () => {
    const { result, rerender } = field('Zájem');
    expect(result.current.value).toBe('Zájem');
    rerender({ server: 'Zájem 2' });
    expect(result.current.value).toBe('Zájem 2');
    act(() => result.current.onFocus());
    rerender({ server: 'Zájem 3' });
    expect(result.current.value).toBe('Zájem 3');
    expect(result.current.edited()).toBeNull();
  });

  it('never writes back a server change made while the field was focused', () => {
    const { result, rerender } = field('Zájem');
    act(() => result.current.onFocus());
    rerender({ server: 'Přejmenováno jinde' });
    act(() => result.current.onBlur());
    expect(result.current.edited()).toBeNull();
    expect(result.current.value).toBe('Přejmenováno jinde');
  });

  it('answers null from edited() unless the user typed, then their value', () => {
    const { result } = field('Zájem');
    expect(result.current.edited()).toBeNull();
    act(() => result.current.onFocus());
    act(() => result.current.onChange('Zájem!'));
    expect(result.current.value).toBe('Zájem!');
    expect(result.current.edited()).toBe('Zájem!');
  });

  it('keeps the draft over a server change once the user typed', () => {
    const { result, rerender } = field('Zájem');
    act(() => result.current.onFocus());
    act(() => result.current.onChange('Můj název'));
    rerender({ server: 'Jejich název' });
    expect(result.current.value).toBe('Můj název');
    act(() => result.current.onBlur());
    expect(result.current.value).toBe('Můj název');
    expect(result.current.edited()).toBe('Můj název');
  });

  it('treats a typed-and-undone edit as no edit: the blur resumes following', () => {
    const { result, rerender } = field('Zájem');
    act(() => result.current.onFocus());
    act(() => result.current.onChange('Zájem x'));
    rerender({ server: 'Jinde' });
    act(() => result.current.onChange('Zájem'));
    /* Still focused: the value the user is typing over does not jump. */
    expect(result.current.value).toBe('Zájem');
    expect(result.current.edited()).toBeNull();
    act(() => result.current.onBlur());
    expect(result.current.value).toBe('Jinde');
  });

  it('starts the draft from the value the user saw when they began', () => {
    const { result, rerender } = field('A');
    act(() => result.current.onFocus());
    rerender({ server: 'B' });
    act(() => result.current.onChange('B!'));
    act(() => result.current.onChange('B'));
    act(() => result.current.onBlur());
    /* B was the base, so typing back to it is no edit: nothing to save. */
    expect(result.current.edited()).toBeNull();
  });

  it('hands a saved draft back to the server once the re-read brings it, with no flash', () => {
    const { result, rerender } = field('Zájem', trim);
    act(() => result.current.onFocus());
    act(() => result.current.onChange('Prohlídka '));
    act(() => result.current.onBlur());
    expect(result.current.edited()).toBe('Prohlídka');
    /* The save is in flight: the field keeps the user's text, not the old value. */
    rerender({ server: 'Zájem' });
    expect(result.current.value).toBe('Prohlídka ');
    rerender({ server: 'Prohlídka' });
    expect(result.current.value).toBe('Prohlídka');
    /* Untouched again, so a later change elsewhere is followed. */
    rerender({ server: 'Prohlídka 2' });
    expect(result.current.value).toBe('Prohlídka 2');
  });

  /* A field that stays open after a save (the stage row): a change made elsewhere
     can land between the save and its re-read, so the server never reaches the
     draft. Marked saved, the draft ends at the next server value, whatever it is. */
  it('ends a saved draft at the next server value, even a change made elsewhere', () => {
    const { result, rerender } = field('A', trim);
    act(() => result.current.onFocus());
    act(() => result.current.onChange('X'));
    act(() => result.current.onBlur());
    expect(result.current.edited()).toBe('X');
    act(() => result.current.saved());
    /* The re-read has not come yet: the field keeps the saved text. */
    rerender({ server: 'A' });
    expect(result.current.value).toBe('X');
    rerender({ server: 'Y' });
    expect(result.current.value).toBe('Y');
    expect(result.current.edited()).toBeNull();
    act(() => result.current.onFocus());
    act(() => result.current.onBlur());
    expect(result.current.edited()).toBeNull();
    rerender({ server: 'Z' });
    expect(result.current.value).toBe('Z');
  });

  it('keeps a saved draft the user typed into again, over a server change', () => {
    const { result, rerender } = field('A');
    act(() => result.current.onChange('X'));
    act(() => result.current.saved());
    act(() => result.current.onFocus());
    act(() => result.current.onChange('X2'));
    act(() => result.current.onBlur());
    rerender({ server: 'Y' });
    expect(result.current.value).toBe('X2');
    expect(result.current.edited()).toBe('X2');
  });

  it('compares through normalize: whitespace alone is no edit', () => {
    const { result } = field('Zájem', trim);
    act(() => result.current.onFocus());
    act(() => result.current.onChange('Zájem  '));
    expect(result.current.edited()).toBeNull();
    act(() => result.current.onBlur());
    expect(result.current.value).toBe('Zájem');
  });

  it('answers null when the server already holds what the user typed', () => {
    const { result, rerender } = field('A');
    act(() => result.current.onFocus());
    act(() => result.current.onChange('B'));
    rerender({ server: 'B' });
    expect(result.current.edited()).toBeNull();
  });

  it('drops a no-net-change draft at once on a control with no focus (a swatch)', () => {
    const { result, rerender } = field('copper');
    act(() => result.current.onChange('sage'));
    rerender({ server: 'teal' });
    expect(result.current.value).toBe('sage');
    act(() => result.current.onChange('copper'));
    expect(result.current.value).toBe('teal');
    expect(result.current.edited()).toBeNull();
  });

  it('reset() drops the draft and follows the server again', () => {
    const { result, rerender } = field('A');
    act(() => result.current.onFocus());
    act(() => result.current.onChange('mine'));
    act(() => result.current.reset());
    expect(result.current.value).toBe('A');
    rerender({ server: 'B' });
    expect(result.current.value).toBe('B');
    expect(result.current.edited()).toBeNull();
  });
});
