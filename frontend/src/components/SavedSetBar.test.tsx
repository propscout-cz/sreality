/* SavedSetBar — THE saved-set bar, instance-free: own chips vs a shared one,
 * load, the dirty mark and the update button, the two-step entries, the three
 * dialog flows and the leading slot. The instances' own pins live beside them
 * (FilterPresetBar.test, stats/DashboardBar.test). */

import { describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { ApiError } from '@/lib/api';
import { PRESET_COPY, PRESET_GROUP_COPY } from './FilterPresetBar';
import SavedSetBar, { type SavedSetBarProps, type SavedSetGroups, type SavedSetItem } from './SavedSetBar';

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
  const entries = () => screen.getAllByRole('menuitem').map((b) => b.textContent);

  it('marks the active chip, and its menu leads with Save changes and Revert to saved', () => {
    renderBar({ activeId: 'a', dirty: true });
    expect(screen.getByRole('button', { name: 'Alfa' })).toHaveAccessibleDescription('Edited since loaded');
    expect(screen.getByRole('button', { name: 'Beta' })).not.toHaveAccessibleDescription('Edited since loaded');
    openMenu('Alfa');
    expect(entries().slice(0, 3)).toEqual(['Save changes', 'Revert to saved', 'Edit…']);
  });

  it('draws no save button in the bar (operator 2026-10-27)', () => {
    renderBar({ activeId: 'a', dirty: true });
    expect(screen.queryByRole('button', { name: /Update|Save changes/ })).toBeNull();
  });

  it('offers neither on a clean chip, nor on another one', () => {
    renderBar({ activeId: 'a', dirty: false });
    openMenu('Alfa');
    expect(entries()).not.toContain('Save changes');
    expect(entries()).not.toContain('Revert to saved');
    cleanup();
    renderBar({ activeId: 'a', dirty: true });
    openMenu('Beta');
    expect(entries()).not.toContain('Save changes');
  });

  it('offers a shared chip the revert, never the save', () => {
    renderBar({ activeId: 's', dirty: true });
    const load = screen.getByRole('button', { name: /^Cizí/ });
    expect(load).toHaveAccessibleDescription('Edited since loaded');
    expect(within(load).getByTitle('Edited — save a copy of your own')).toBeInTheDocument();
    openMenu('Cizí — shared by owner@example.com');
    expect(entries()).toEqual(['Revert to saved']);
  });

  it('saves in one click, with no dialog: the name, colour and checkbox answer stay the item\'s own', async () => {
    const checkboxInitial = vi.fn((i: Item | null) => i?.id === 'a');
    const { onUpdate } = renderBar({
      activeId: 'a',
      dirty: true,
      checkbox: { label: 'Include the current map area in this preset', initial: checkboxInitial },
    });
    openMenu('Alfa');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Save changes' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(ITEMS[0], 'Alfa', true, null));
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
  });

  it('saves a greyed-out checkbox unticked', async () => {
    const { onUpdate } = renderBar({
      activeId: 'a',
      dirty: true,
      checkbox: { label: 'Uložit i nastavení filtru', initial: () => true, disabled: () => 'Nenese filtr.' },
    });
    openMenu('Alfa');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Save changes' }));
    await waitFor(() => expect(onUpdate).toHaveBeenCalledWith(ITEMS[0], 'Alfa', false, null));
  });

  it('reverts by loading the stored set again', () => {
    const { onLoad, onUpdate } = renderBar({ activeId: 'a', dirty: true });
    openMenu('Alfa');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Revert to saved' }));
    expect(onLoad).toHaveBeenCalledWith(ITEMS[0]);
    expect(onUpdate).not.toHaveBeenCalled();
    expect(screen.queryByRole('menu')).toBeNull();
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

describe('<SavedSetBar> groups', () => {
  const GROUPS = [
    { id: 'g1', name: 'Praha', color: 'plum' as const },
    { id: 'g2', name: 'Brno', color: null },
  ];
  const grouped = (id: string, name: string, group_id: string | null): Item => ({ ...mine(id, name), group_id });
  const GROUPED: Item[] = [grouped('a', 'Alfa', null), grouped('b', 'Beta', 'g1'), grouped('c', 'Gama', 'g1')];

  function renderGroups(over: Partial<SavedSetGroups> = {}, props: Partial<SavedSetBarProps<Item>> = {}) {
    const groups: SavedSetGroups = {
      items: GROUPS,
      copy: PRESET_GROUP_COPY,
      onLayout: vi.fn(),
      onCreate: vi.fn(async () => undefined),
      onEdit: vi.fn(async () => undefined),
      onDelete: vi.fn(async () => undefined),
      ...over,
    };
    renderBar({ items: GROUPED, groups, ...props });
    return groups;
  }

  it('draws each group as a box holding its chips, an empty one inviting a drag', () => {
    renderGroups();
    const praha = screen.getByRole('group', { name: 'Praha' });
    expect(within(praha).getAllByRole('button', { name: /^(Alfa|Beta|Gama)$/ }).map((b) => b.textContent)).toEqual([
      'Beta',
      'Gama',
    ]);
    expect(within(screen.getByRole('group', { name: 'Brno' })).getByText('Drag presets here')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Alfa' }).closest('[role="group"]')).toBeNull();
    expect(screen.getByRole('button', { name: 'Move group: Praha' })).toBeInTheDocument();
    // A lone loose chip still drags: it can go into a box.
    expect(screen.getByRole('button', { name: 'Reorder preset: Alfa' })).toBeInTheDocument();
  });

  it('moves a chip into a box and back out from its menu, writing the whole layout', () => {
    const { onLayout } = renderGroups();
    openMenu('Alfa');
    const entries = within(screen.getByRole('menu')).getAllByRole('menuitem').map((m) => m.textContent);
    expect(entries).toEqual(['Edit…', 'Move to Praha', 'Move to Brno', 'Delete…']);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Move to Brno' }));
    expect(onLayout).toHaveBeenLastCalledWith({
      groups: ['g1', 'g2'],
      items: [
        { id: 'b', group_id: 'g1' },
        { id: 'c', group_id: 'g1' },
        { id: 'a', group_id: 'g2' },
      ],
    });
    openMenu('Beta');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Take out of group' }));
    expect(onLayout).toHaveBeenLastCalledWith({
      groups: ['g1', 'g2'],
      items: [
        { id: 'a', group_id: null },
        { id: 'b', group_id: null },
        { id: 'c', group_id: 'g1' },
      ],
    });
  });

  it('creates and edits a group through the name and colour dialog', async () => {
    const { onCreate, onEdit } = renderGroups();
    fireEvent.click(screen.getByRole('button', { name: '+ New group' }));
    const dialog = screen.getByRole('dialog', { name: 'New preset group' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Group name' }), { target: { value: 'Ostrava' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create group' }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('Ostrava', null));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: 'Group options: Praha' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit…' }));
    const edit = screen.getByRole('dialog', { name: 'Edit group' });
    expect(within(edit).getByRole('textbox', { name: 'Group name' })).toHaveValue('Praha');
    fireEvent.click(within(edit).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onEdit).toHaveBeenCalledWith(GROUPS[0], 'Praha', 'plum'));
  });

  it('deletes a group in two steps, saying its presets stay', async () => {
    const { onDelete } = renderGroups();
    fireEvent.click(screen.getByRole('button', { name: 'Group options: Brno' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete group…' }));
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete group, keep presets' }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(GROUPS[1]));
  });

  it('folded, shows the active chip alone with no boxes and no new-group button', () => {
    localStorage.removeItem('test.groups.expanded');
    renderGroups({}, { activeId: 'b', collapse: { label: 'Presety', storageKey: 'test.groups.expanded', show: 's', fold: 'f' } });
    expect(screen.queryByRole('group')).toBeNull();
    expect(screen.queryByRole('button', { name: '+ New group' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Beta' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Alfa' })).toBeNull();
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
