/* SavedSetBar — THE saved-set bar, instance-free: own chips vs a shared one,
 * load, the dirty mark and the update button, the two-step entries, the three
 * dialog flows and the leading slot. The instances' own pins live beside them
 * (FilterPresetBar.test, stats/DashboardBar.test). */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ApiError } from '@/lib/api';
import { PRESET_COPY } from './FilterPresetBar';
import SavedSetBar, { type SavedSetBarProps, type SavedSetItem } from './SavedSetBar';

/* The preset's words, plus the two a shared chip needs (presets have none). */
const COPY = { ...PRESET_COPY, shared: { by: 'shared by', editedHint: 'Edited — save a copy of your own' } };

interface Item extends SavedSetItem {
  owner: string | null;
}

const mine = (id: string, name: string): Item => ({ id, name, color: null, owner: null });
const ITEMS: Item[] = [
  mine('a', 'Alfa'),
  { id: 's', name: 'Cizí', color: 'sage', owner: 'owner@example.com' },
  mine('b', 'Beta'),
];

function renderBar(props: Partial<SavedSetBarProps<Item>> = {}) {
  const all: SavedSetBarProps<Item> = {
    ...renderBarProps(),
    isOwn: (i) => i.owner == null,
    suffix: (i) => i.owner,
    ...props,
  };
  render(<SavedSetBar {...all} />);
  return all;
}

const openMenu = (name: string) =>
  fireEvent.click(screen.getByRole('button', { name: `Preset options: ${name}` }));

describe('<SavedSetBar> chips', () => {
  it('lists own chips first with a grip each, then a shared chip with its owner and no grip', () => {
    renderBar();
    const loads = screen.getAllByRole('button', { name: /^(Alfa|Beta|Cizí)/ });
    expect(loads.map((b) => b.textContent)).toEqual(['Alfa', 'Beta', 'Cizí — shared by owner@example.com']);
    expect(loads[2]).toHaveAccessibleName('Cizí — shared by owner@example.com');
    expect(screen.getByRole('button', { name: 'Preset options: Cizí — shared by owner@example.com' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reorder preset: Alfa' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reorder preset: Beta' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reorder preset: Cizí' })).toBeNull();
  });

  it('draws no grip while there is only one own chip to order', () => {
    renderBar({ items: [mine('a', 'Alfa')] });
    expect(screen.queryByRole('button', { name: /^Reorder preset/ })).toBeNull();
  });

  it('loads on a chip click', () => {
    const { onLoad } = renderBar();
    fireEvent.click(screen.getByRole('button', { name: 'Beta' }));
    expect(onLoad).toHaveBeenCalledWith(ITEMS[2]);
  });

  it('says so when there is nothing saved, but not while the list is read', () => {
    const { rerender } = render(
      <SavedSetBar {...renderBarProps()} items={[]} loading />,
    );
    expect(screen.queryByText('No saved presets yet.')).toBeNull();
    rerender(<SavedSetBar {...renderBarProps()} items={[]} />);
    expect(screen.getByText('No saved presets yet.')).toBeInTheDocument();
  });

  it('renders the leading slot first', () => {
    renderBar({ leading: <button type="button">Pipeline</button> });
    expect(screen.getAllByRole('button')[0]).toHaveTextContent('Pipeline');
  });
});

describe('<SavedSetBar> dirty', () => {
  it('marks the active chip and offers the update for an own one', () => {
    renderBar({ activeId: 'a', dirty: true });
    expect(screen.getByRole('button', { name: 'Alfa' })).toHaveAccessibleDescription('Edited since loaded');
    expect(screen.getByRole('button', { name: 'Beta' })).not.toHaveAccessibleDescription('Edited since loaded');
    expect(screen.getByRole('button', { name: 'Update preset' })).toBeInTheDocument();
  });

  it('never offers the update for a shared chip', () => {
    renderBar({ activeId: 's', dirty: true });
    const load = screen.getByRole('button', { name: /^Cizí/ });
    expect(load).toHaveAccessibleDescription('Edited since loaded');
    expect(within(load).getByTitle('Edited — save a copy of your own')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Update preset' })).toBeNull();
  });

  it('updates through the dialog, the checkbox starting from the item', async () => {
    const checkboxInitial = vi.fn((i: Item | null) => i?.id === 'a');
    const { onUpdate } = renderBar({
      activeId: 'a',
      dirty: true,
      checkbox: { label: 'Include the current map area in this preset', initial: checkboxInitial },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Update preset' }));
    const dialog = screen.getByRole('dialog', { name: 'Update preset' });
    expect(within(dialog).getByRole('button', { name: /map area/ })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update preset' }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(ITEMS[0], 'Alfa', true, null));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

describe('<SavedSetBar> dialogs', () => {
  it('saves a new set with the checkbox off by default', async () => {
    const { onSaveNew } = renderBar({
      checkbox: { label: 'Uložit i nastavení filtru', initial: () => false },
    });
    fireEvent.click(screen.getByRole('button', { name: '+ Save preset' }));
    const dialog = screen.getByRole('dialog', { name: 'Save filters as a preset' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Preset name' }), { target: { value: '  Nová ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save preset' }));
    await waitFor(() => expect(onSaveNew).toHaveBeenCalledWith('Nová', false, null));
  });

  it('keeps the dialog open and says why when the write is refused', async () => {
    renderBar({ onSaveNew: vi.fn(async () => Promise.reject(new ApiError('name taken', 422, null))) });
    fireEvent.click(screen.getByRole('button', { name: '+ Save preset' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Preset name' }), { target: { value: 'X' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save preset' }));
    expect(await within(dialog).findByText('name taken')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('renames from the menu without the checkbox', async () => {
    const { onRename } = renderBar({ checkbox: { label: 'Extra', initial: () => true } });
    openMenu('Beta');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit…' }));
    const dialog = screen.getByRole('dialog', { name: 'Edit preset' });
    expect(within(dialog).queryByRole('button', { name: 'Extra' })).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onRename).toHaveBeenCalledWith(ITEMS[2], 'Beta', null));
  });
});

describe('<SavedSetBar> menus', () => {
  it('gives an own chip Edit, the instance entries, then a two-step Delete', async () => {
    const share = vi.fn();
    const { onDelete } = renderBar({
      chipMenu: (i) => (i.owner == null ? [{ label: 'Sdílet…', onSelect: share }] : []),
    });
    openMenu('Alfa');
    const menu = screen.getByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['Edit…', 'Sdílet…', 'Delete…']);
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Delete…' }));
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Confirm delete' }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(ITEMS[0]));
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
  });

  it('gives a shared chip only the instance entries, two-step ones armed first', async () => {
    const leave = vi.fn(async () => undefined);
    renderBar({
      chipMenu: (i) =>
        i.owner == null
          ? []
          : [{ label: 'Přestat sledovat', danger: true, confirm: { label: 'Opravdu přestat sledovat', busy: 'Odcházím…' }, onSelect: leave }],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Preset options: Cizí — shared by owner@example.com' }));
    const menu = screen.getByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['Přestat sledovat']);
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Přestat sledovat' }));
    expect(leave).not.toHaveBeenCalled();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Opravdu přestat sledovat' }));
    await waitFor(() => expect(leave).toHaveBeenCalledTimes(1));
  });

  it('keeps a failed two-step entry armed for a retry', async () => {
    renderBar({ onDelete: vi.fn(async () => Promise.reject(new Error('nope'))) });
    openMenu('Alfa');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete…' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Confirm delete' }));
    await waitFor(() => expect(screen.getByRole('menuitem', { name: 'Confirm delete' })).toBeEnabled());
  });
});

function renderBarProps(): SavedSetBarProps<Item> {
  return {
    items: ITEMS,
    activeId: null,
    dirty: false,
    copy: COPY,
    onLoad: vi.fn(),
    onSaveNew: vi.fn(async () => undefined),
    onUpdate: vi.fn(async () => undefined),
    onRename: vi.fn(async () => undefined),
    onDelete: vi.fn(async () => undefined),
    onReorder: vi.fn(),
  };
}
