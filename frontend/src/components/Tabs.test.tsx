/* The one page tab strip (operator ruling 2026-10-27, option A of the tab-strip options
 * page; .interface-design/system.md § Tab strip): a full-width rule, the first label
 * on the title's edge, a label-wide 2px copper line on the rule, colour never
 * weight, one count pill, and the WAI-ARIA tabs keyboard. jsdom draws no layout,
 * so the geometry is pinned through the classes that make it. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';

import Tabs, { type Tab } from './Tabs';

type K = 'a' | 'b' | 'c';
const TABS: ReadonlyArray<Tab<K>> = [
  { key: 'a', label: 'A', count: 0 },
  { key: 'b', label: 'B', count: '155 189' },
  { key: 'c', label: 'C' },
];

const classes = (el: Element) => el.className.split(/\s+/).filter(Boolean);
const COLOUR = /^(hover:)?text-\[var\(--color-ink(-\d)?\)\]$/;

function Harness({
  initial = 'a',
  onChange = () => {},
}: {
  initial?: K;
  onChange?: (k: K) => void;
}) {
  const [active, setActive] = useState<K>(initial);
  return (
    <Tabs
      tabs={TABS}
      active={active}
      onChange={(k) => {
        onChange(k);
        setActive(k);
      }}
      ariaLabel="Části"
    />
  );
}

describe('<Tabs>', () => {
  it('puts the first label on the content edge', () => {
    render(<Tabs tabs={TABS} active="a" onChange={() => {}} ariaLabel="Části" />);
    for (const tab of screen.getAllByRole('tab')) {
      expect(classes(tab).filter((c) => /^(px|pl|pr|ps|pe)-/.test(c))).toEqual([]);
    }
    const list = classes(screen.getByRole('tablist'));
    expect(classes(screen.getAllByRole('tab')[0].parentElement!)).toContain('gap-6');
    // The scroller's 4px of ring room is cancelled by an equal negative margin.
    expect(list).toContain('-mx-1');
    expect(list).toContain('p-1');
  });

  /* The ring room hangs 4px past the strip, below its rule too; pinned, that band
     sits over the panels scrolling underneath and swallowed their clicks. */
  it('takes clicks on the tabs and their gaps only, never on the ring room', () => {
    const onChange = vi.fn();
    render(<Tabs tabs={TABS} active="a" onChange={onChange} ariaLabel="Části" sticky />);
    const list = screen.getByRole('tablist');
    const tabs = screen.getAllByRole('tab');
    const row = tabs[0].parentElement!;
    expect(classes(list)).toContain('pointer-events-none');
    expect(classes(list)).toEqual(expect.arrayContaining(['-mt-1', '-mb-1.25', 'p-1']));
    // One row, inside the scroller's padding, holds every tab: tabs and gaps take the
    // clicks and the sideways swipes, and it is exactly the strip's height.
    expect(row.parentElement).toBe(list);
    expect(list.children).toHaveLength(1);
    for (const tab of tabs) expect(tab.parentElement).toBe(row);
    expect(classes(row)).toEqual(expect.arrayContaining(['pointer-events-auto', 'flex', 'shrink-0']));
    expect(classes(row).filter((c) => /^-?[mp][trblxy]?-/.test(c))).toEqual([]);
    fireEvent.click(tabs[1]);
    expect(onChange).toHaveBeenCalledWith('b');
  });

  it('marks the open tab with a 2px copper line exactly its width, on the rule', () => {
    render(<Tabs tabs={TABS} active="b" onChange={() => {}} ariaLabel="Části" />);
    const [a, b, c] = screen.getAllByRole('tab');
    const lines = b.querySelectorAll('[data-tab-indicator]');
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveAttribute('aria-hidden', 'true');
    expect(classes(lines[0])).toEqual(
      expect.arrayContaining(['absolute', 'inset-x-0', 'bottom-0', 'h-0.5', 'bg-[var(--color-copper)]']),
    );
    expect(classes(b)).toContain('relative');
    expect(a.querySelector('[data-tab-indicator]')).toBeNull();
    expect(c.querySelector('[data-tab-indicator]')).toBeNull();
    // 4px of ring room + 1px: the buttons' bottom row lies ON the strip's rule.
    expect(classes(screen.getByRole('tablist'))).toContain('-mb-1.25');
  });

  it('changes colour, never weight or size', () => {
    render(<Tabs tabs={TABS} active="a" onChange={() => {}} ariaLabel="Části" />);
    const [on, idle] = screen.getAllByRole('tab');
    expect(classes(on)).toContain('text-[var(--color-ink)]');
    expect(classes(on).some((c) => c.startsWith('hover:text-'))).toBe(false);
    expect(classes(idle)).toEqual(
      expect.arrayContaining(['text-[var(--color-ink-3)]', 'hover:text-[var(--color-ink-2)]']),
    );
    expect(classes(on).filter((c) => !COLOUR.test(c))).toEqual(classes(idle).filter((c) => !COLOUR.test(c)));
    for (const tab of [on, idle]) {
      expect(classes(tab).filter((c) => /^(font-(medium|semibold|bold)|tracking-)/.test(c))).toEqual([]);
      expect(classes(tab)).toEqual(expect.arrayContaining(['h-11', 'text-sm', 'shrink-0', 'whitespace-nowrap']));
      // The global :focus-visible ring is the one indicator (eslint.config.js).
      expect(tab.className).not.toMatch(/outline-none/);
    }
    // Too many tabs for the width scroll sideways, with no scrollbar between them and the rule.
    expect(classes(screen.getByRole('tablist'))).toEqual(
      expect.arrayContaining(['overflow-x-auto', '[scrollbar-width:none]', '[&::-webkit-scrollbar]:hidden']),
    );
  });

  it('draws every count as the inset pill, zero included', () => {
    render(<Tabs tabs={TABS} active="a" onChange={() => {}} ariaLabel="Části" />);
    const PILL = [
      'rounded-full', 'bg-[var(--color-inset)]', 'px-1.5', 'py-0.5', 'font-mono',
      'text-[0.68rem]', 'tabular-nums', 'text-[var(--color-ink-2)]',
    ];
    const a = screen.getByRole('tab', { name: 'A 0' });
    const b = screen.getByRole('tab', { name: 'B 155 189' });
    expect(classes(within(a).getByText('0'))).toEqual(expect.arrayContaining(PILL));
    expect(classes(within(b).getByText('155 189'))).toEqual(expect.arrayContaining(PILL));
    const c = screen.getByRole('tab', { name: 'C' });
    expect(c.querySelector('.rounded-full')).toBeNull();
  });

  it('is a labelled ARIA tablist', () => {
    const { rerender } = render(<Tabs tabs={TABS} active="b" onChange={() => {}} ariaLabel="Části" idPrefix="x" />);
    expect(screen.getByRole('tablist', { name: 'Části' })).toBeInTheDocument();
    const [a, b] = screen.getAllByRole('tab');
    for (const tab of screen.getAllByRole('tab')) expect(tab).toHaveAttribute('type', 'button');
    expect(b).toHaveAttribute('aria-selected', 'true');
    expect(a).toHaveAttribute('aria-selected', 'false');
    expect(b).toHaveAttribute('tabindex', '0');
    expect(a).toHaveAttribute('tabindex', '-1');
    expect(a).toHaveAttribute('id', 'xtab-a');
    expect(a).toHaveAttribute('aria-controls', 'xpanel-a');

    rerender(<Tabs tabs={TABS} active="b" onChange={() => {}} ariaLabel="Části" />);
    expect(screen.getAllByRole('tab')[0]).not.toHaveAttribute('id');
    expect(screen.getAllByRole('tab')[0]).not.toHaveAttribute('aria-controls');

    // An active key the strip does not hold still leaves one tab to Tab into.
    rerender(<Tabs tabs={TABS.slice(1)} active="a" onChange={() => {}} ariaLabel="Části" />);
    expect(screen.getAllByRole('tab').map((t) => t.getAttribute('tabindex'))).toEqual(['0', '-1']);
  });

  it('moves with the arrow keys and focus follows', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const tab = (name: string) => screen.getByRole('tab', { name: new RegExp(`^${name}`) });

    fireEvent.keyDown(tab('A'), { key: 'ArrowLeft' });
    expect(tab('C')).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(tab('C'));

    fireEvent.keyDown(tab('C'), { key: 'ArrowRight' });
    expect(tab('A')).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(tab('A'));

    fireEvent.keyDown(tab('A'), { key: 'End' });
    expect(tab('C')).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(tab('C'));

    fireEvent.keyDown(tab('C'), { key: 'Home' });
    expect(tab('A')).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(tab('A'));

    onChange.mockClear();
    fireEvent.keyDown(tab('A'), { key: 'Home' });
    expect(onChange).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(tab('A'));

    // A browser shortcut (Alt+← is Back) is not the strip's.
    fireEvent.keyDown(tab('A'), { key: 'ArrowLeft', altKey: true });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('keeps the trailing slot on the strip, above its rule', () => {
    render(
      <Tabs tabs={TABS} active="a" onChange={() => {}} ariaLabel="Části" trailing={<button type="button">Split</button>} />,
    );
    const list = screen.getByRole('tablist');
    const strip = list.parentElement!;
    const split = screen.getByRole('button', { name: 'Split' });
    expect(classes(strip)).toEqual(expect.arrayContaining(['border-b', 'border-[var(--color-rule)]']));
    expect(strip).toContainElement(split);
    expect(list).not.toContainElement(split);
    // Too narrow for one line: the slot wraps to a line ABOVE the tabs, at the right end,
    // so the tabs never leave the rule.
    expect(classes(strip)).toContain('flex-wrap-reverse');
    expect(classes(split.parentElement!)).toContain('ml-auto');
  });

  it('draws the rule on the whole strip, not on the tab group', () => {
    render(<Tabs tabs={TABS} active="a" onChange={() => {}} ariaLabel="Části" />);
    const list = screen.getByRole('tablist');
    expect(classes(list.parentElement!)).toContain('border-b');
    expect(classes(list)).toEqual(expect.arrayContaining(['flex-auto', 'min-w-0']));
    expect(classes(list).filter((c) => c.startsWith('border'))).toEqual([]);
  });

  it('pins under the header only when sticky', () => {
    const { rerender } = render(<Tabs tabs={TABS} active="a" onChange={() => {}} ariaLabel="Části" sticky />);
    expect(classes(screen.getByRole('tablist').parentElement!)).toEqual(
      expect.arrayContaining(['sticky', 'top-14', 'z-20', 'bg-[var(--color-paper)]']),
    );
    rerender(<Tabs tabs={TABS} active="a" onChange={() => {}} ariaLabel="Části" />);
    expect(classes(screen.getByRole('tablist').parentElement!)).not.toContain('sticky');
  });
});
