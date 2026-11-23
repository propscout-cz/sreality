/* The one ⋯ menu behind the redesign's chart card (P14), watchdog row (P24) and
 * collection header (P31): the APG menu-button contract, and the order that lets
 * a caller's own confirm step run on a closed menu. */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import OverflowMenu, { type OverflowMenuItem } from './OverflowMenu';

const trigger = () => screen.getByRole('button', { name: 'Další akce' });
const items = () => screen.getAllByRole('menuitem');

function renderMenu(entries?: OverflowMenuItem[]) {
  const list: OverflowMenuItem[] = entries ?? [
    { label: 'Posunout výš', onSelect: vi.fn() },
    { label: 'Posunout níž', onSelect: vi.fn() },
    { label: 'Odebrat', onSelect: vi.fn(), danger: true },
  ];
  render(<OverflowMenu label="Další akce" items={list} />);
  return list;
}

describe('<OverflowMenu> trigger', () => {
  it('is a ⋯ button named by its label that announces a closed menu', () => {
    renderMenu();
    expect(trigger()).toHaveTextContent('⋯');
    expect(trigger()).toHaveAttribute('aria-haspopup', 'menu');
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('opens on click into a menu of its items, focus on the first', async () => {
    renderMenu();
    await userEvent.setup().click(trigger());
    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    const menu = screen.getByRole('menu');
    expect(trigger()).toHaveAttribute('aria-controls', menu.id);
    expect(items().map((i) => i.textContent)).toEqual(['Posunout výš', 'Posunout níž', 'Odebrat']);
    expect(document.activeElement).toBe(items()[0]);
  });

  it.each([['Enter', '{Enter}'], ['Space', ' '], ['ArrowDown', '{ArrowDown}']])(
    '%s on the trigger opens with focus on the first item',
    async (_, key) => {
      renderMenu();
      const user = userEvent.setup();
      trigger().focus();
      await user.keyboard(key);
      expect(screen.getByRole('menu')).toBeInTheDocument();
      expect(document.activeElement).toBe(items()[0]);
    },
  );

  it('a second click on the trigger closes it', async () => {
    renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());
    await user.click(trigger());
    expect(screen.queryByRole('menu')).toBeNull();
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('<OverflowMenu> keyboard inside', () => {
  it('ArrowDown and ArrowUp move between the items, wrapping at the ends', async () => {
    renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(items()[1]);
    await user.keyboard('{ArrowDown}{ArrowDown}');
    expect(document.activeElement).toBe(items()[0]);
    await user.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(items()[2]);
  });

  it('Escape closes it and hands focus back to the trigger', async () => {
    renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it('Tab from any item closes it and continues from the trigger', async () => {
    renderMenu();
    await userEvent.setup().click(trigger());
    fireEvent.keyDown(items()[1], { key: 'Tab' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });
});

describe('<OverflowMenu> choosing', () => {
  it('closes the menu first, then calls the item, so a caller confirm opens on a closed menu', async () => {
    const seen: { menuOpen: boolean; focus: Element | null }[] = [];
    const onSelect = vi.fn(() => {
      seen.push({ menuOpen: screen.queryByRole('menu') != null, focus: document.activeElement });
    });
    renderMenu([
      { label: 'Upravit', onSelect: vi.fn() },
      { label: 'Smazat…', onSelect, danger: true },
    ]);
    const user = userEvent.setup();
    await user.click(trigger());
    await user.click(screen.getByRole('menuitem', { name: 'Smazat…' }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(seen[0]).toEqual({ menuOpen: false, focus: trigger() });
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('Enter on a focused item chooses it', async () => {
    const list = renderMenu();
    const user = userEvent.setup();
    await user.click(trigger());
    await user.keyboard('{ArrowDown}{Enter}');
    expect(list[1].onSelect).toHaveBeenCalledTimes(1);
    expect(list[0].onSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('a disabled item stays reachable but does nothing and keeps the menu open', async () => {
    const list = renderMenu([
      { label: 'Posunout výš', onSelect: vi.fn(), disabled: true },
      { label: 'Odebrat', onSelect: vi.fn(), danger: true },
    ]);
    const user = userEvent.setup();
    await user.click(trigger());
    const first = items()[0];
    expect(first).toHaveAttribute('aria-disabled', 'true');
    expect(document.activeElement).toBe(first);
    await user.click(first);
    expect(list[0].onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('a danger item reads in brick, the others do not', async () => {
    renderMenu();
    await userEvent.setup().click(trigger());
    const [up, , remove] = items();
    expect(remove.className).toContain('text-[var(--color-brick)]');
    expect(up.className).not.toContain('text-[var(--color-brick)]');
  });
});
