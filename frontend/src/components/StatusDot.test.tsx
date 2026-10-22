/* The status mark (wave 8, contract D3). Pins:
 * - the four statuses on the existing tokens: sage / ochre / brick filled, Not measured a hollow
 *   1.5 px ink-3 ring (never ink-4, never a filled grey dot);
 * - the dot is decorative; the WORD always travels with it, visible in ink-2 or sr-only (then
 *   also the dot's hover title);
 * - anything off the scale reads Not measured, never OK;
 * - the three sizes. */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { Status } from '@/lib/healthChecks';
import StatusDot, { STATUS_WORD } from './StatusDot';

const dotOf = (container: HTMLElement) => container.querySelector('[aria-hidden="true"]');

describe('<StatusDot>', () => {
  it('speaks the four GLOSSARY words', () => {
    expect(STATUS_WORD).toEqual({ ok: 'OK', warn: 'Watch', fail: 'Problem', unknown: 'Not measured' });
  });

  it.each([
    ['ok', 'OK', 'bg-[var(--color-sage)]'],
    ['warn', 'Watch', 'bg-[var(--color-ochre)]'],
    ['fail', 'Problem', 'bg-[var(--color-brick)]'],
    ['unknown', 'Not measured', 'border-[1.5px] border-[var(--color-ink-3)] bg-transparent'],
  ] as const)('%s: the dot (%s) and its word, the word in ink', (status, word, mark) => {
    const { container } = render(<StatusDot status={status} />);
    expect(dotOf(container)?.getAttribute('class')).toBe(`inline-block shrink-0 rounded-full h-2 w-2 ${mark}`);
    expect(screen.getByText(word).getAttribute('class')).toBe('whitespace-nowrap text-[var(--color-ink-2)]');
    expect(container.textContent).toBe(word);
    expect(container.firstElementChild?.hasAttribute('title')).toBe(false);
    expect(container.innerHTML).not.toContain('ink-4');
  });

  it('reads the word out where the layout shows it elsewhere, and names the dot on hover', () => {
    const { container } = render(<StatusDot status="fail" word="sr-only" size="lg" />);
    expect(screen.getByText('Problem').getAttribute('class')).toBe('sr-only');
    expect(container.firstElementChild?.getAttribute('title')).toBe('Problem');
    expect(dotOf(container)?.getAttribute('class')).toBe(
      'inline-block shrink-0 rounded-full h-2.5 w-2.5 bg-[var(--color-brick)]',
    );
  });

  it('draws chip-sized dots', () => {
    const { container } = render(<StatusDot status="ok" size="sm" />);
    expect(dotOf(container)?.getAttribute('class')).toBe('inline-block shrink-0 rounded-full h-1.5 w-1.5 bg-[var(--color-sage)]');
  });

  it('reads a word off the scale as Not measured, never OK', () => {
    const { container } = render(<StatusDot status={'skipped' as Status} />);
    expect(container.textContent).toBe('Not measured');
    expect(dotOf(container)?.getAttribute('class')).toContain('bg-transparent');
  });
});
