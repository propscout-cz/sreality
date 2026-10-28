/* Collections — the curated-lists page.
 *
 * Pins the new-collection form's two text fields, which used to be named by a
 * placeholder only (a placeholder is not an accessible name, and it disappears
 * the moment the operator types). The monitoring checkbox was already wrapped
 * in its own <label>, so it is asserted here as the control that must NOT have
 * changed. Since ruling P30 the form opens from a dashed "+ Nová kolekce" card
 * at the end of my list, in place.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import Collections from './Collections';
import * as api from '@/lib/api';

vi.mock('@/lib/api', () => ({
  listCollections: vi.fn(),
  createCollection: vi.fn(),
  deleteCollection: vi.fn(),
}));

/* The add card, closed: the form opens from it in place (ruling P30). */
const addCard = () => screen.findByRole('button', { name: 'Nová kolekce' });
const openForm = async () => fireEvent.click(await addCard());
const follows = (a: Node, b: Node) =>
  Boolean(b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING);

const renderPage = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Collections />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

const BASE = {
  description: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  listing_count: 1, monitoring_enabled: false, is_system: false,
};
const OWN = { ...BASE, id: 7, name: 'Šortlist', owner_email: null, shared_with: [] };
const FROM_PETR = {
  ...BASE, id: 9, name: 'Od Petra', owner_email: 'petr@example.cz',
  shared_with: [{ account_id: 'me', email: null }],
};

describe('<Collections>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listCollections).mockResolvedValue({ data: [], total: 0 });
  });

  it('names both new-collection fields and leaves the monitoring checkbox named', async () => {
    renderPage();
    await openForm();
    const name = await screen.findByRole('textbox', { name: 'Name' });
    expect(name).toHaveAccessibleName('Name');
    expect(screen.getByRole('textbox', { name: 'Description' })).toBeInTheDocument();
    expect(
      screen.getByRole('checkbox', { name: /Monitor changes/ }),
    ).toBeInTheDocument();
  });

  it('says a failed list with a retry that reads again (MS16)', async () => {
    vi.mocked(api.listCollections)
      .mockRejectedValueOnce(new Error('HTTP 500'))
      .mockResolvedValue({ data: [], total: 0 });
    renderPage();
    const retry = await screen.findByRole('button', { name: 'Zkusit znovu' });
    expect(screen.queryByText('No collections yet')).toBeNull();
    fireEvent.click(retry);
    expect(await screen.findByText('No collections yet')).toBeInTheDocument();
  });

  it('submits what was typed into the named fields, then flips back to the card', async () => {
    vi.mocked(api.createCollection).mockResolvedValue({} as never);
    renderPage();
    await openForm();
    fireEvent.change(await screen.findByRole('textbox', { name: 'Name' }), {
      target: { value: 'Shortlist' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), {
      target: { value: 'the good ones' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() =>
      expect(api.createCollection).toHaveBeenCalledWith({
        name: 'Shortlist',
        description: 'the good ones',
        monitoring_enabled: false,
      }),
    );
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull());
    expect(screen.getByRole('button', { name: 'Nová kolekce' })).toHaveFocus();
  });

  /* Sharing (migration 610): what is shared with me is its own section, named by
   * its owner, with none of the owner's controls. */
  it('lists collections shared with me apart, and counts my recipients', async () => {
    const base = {
      description: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
      listing_count: 1, monitoring_enabled: false, is_system: false,
    };
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [
        { ...base, id: 7, name: 'Šortlist', owner_email: null,
          shared_with: [{ account_id: 'b', email: 'petr@example.cz' },
            { account_id: 'c', email: 'jana@example.cz' }] },
        { ...base, id: 9, name: 'Od Petra', owner_email: 'petr@example.cz',
          shared_with: [{ account_id: 'me', email: null }] },
        // Every account has a system "monitoring"; shared, it is not my Default.
        { ...base, id: 10, name: 'monitoring', is_system: true, owner_email: 'petr@example.cz',
          shared_with: [{ account_id: 'me', email: null }] },
      ],
      total: 3,
    });
    renderPage();

    expect(await screen.findByText(/shared with 2/)).toBeInTheDocument();
    // The header counts my own collections, not the API total.
    expect(screen.getByText('1 collection.')).toBeInTheDocument();
    /* A group of the list under an L3 title (section cards ladder), named by it. */
    const section = screen.getByRole('region', { name: 'Shared with me' });
    expect(within(section).getByRole('heading', { level: 2, name: 'Shared with me' }))
      .toHaveClass('text-sm', 'font-semibold', 'text-[var(--color-ink)]');
    expect(within(section).getByRole('heading', { level: 2, name: 'Shared with me' })).not.toHaveClass('uppercase');
    expect(within(section).getByRole('link', { name: 'Od Petra' })).toBeInTheDocument();
    expect(within(section).getAllByText(/Shared by petr@example\.cz/)).toHaveLength(2);
    expect(within(section).queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(within(section).queryByText('Default')).toBeNull();
    // My own row keeps its Delete.
    expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(1);

    // A shared collection's name is not mine to collide with.
    await openForm();
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
      target: { value: 'Od Petra' },
    });
    expect(screen.queryByText(/already exists/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Create' })).toBeEnabled();
  });

  /* Ruling P26: the h1 alone; the nav already says where you are. */
  it('draws no small-caps "Collections" label above the h1', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { level: 1, name: 'Curated lists' })).toBeInTheDocument();
    expect(screen.queryByText('Collections')).toBeNull();
  });

  /* Ruling P30: a dashed card at the end of my list opens into the same form. */
  it('puts a dashed "Nová kolekce" card after my collections that flips into the form in place', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ data: [OWN], total: 1 });
    renderPage();
    const mine = await screen.findByRole('link', { name: 'Šortlist' });
    const card = await addCard();
    expect(card).toHaveClass('border-dashed');
    expect(follows(card, mine)).toBe(true);
    expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull();

    fireEvent.click(card);
    const name = screen.getByRole('textbox', { name: 'Name' });
    expect(name).toHaveFocus();
    expect(screen.getByText('New collection')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nová kolekce' })).toBeNull();
  });

  it('Cancel and Escape flip the form back to the card and return focus to it', async () => {
    renderPage();
    await screen.findByText('No collections yet');
    await openForm();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Nová kolekce' })).toHaveFocus();

    await openForm();
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Name' }), { key: 'Escape' });
    expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Nová kolekce' })).toHaveFocus();
    expect(api.createCollection).not.toHaveBeenCalled();
  });

  it('sits after my list and before "Shared with me"', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({ data: [OWN, FROM_PETR], total: 2 });
    renderPage();
    const mine = await screen.findByRole('link', { name: 'Šortlist' });
    const card = await addCard();
    expect(follows(card, mine)).toBe(true);
    expect(follows(screen.getByRole('heading', { name: 'Shared with me' }), card)).toBe(true);
  });

  it('points the empty state at the card below it', async () => {
    renderPage();
    const sentence = await screen.findByText(/to start one\./);
    expect(sentence).toHaveTextContent(
      'Use "+ Nová kolekce" below to start one. Then add listings from any listing\'s detail page.',
    );
    expect(follows(await addCard(), sentence)).toBe(true);
  });

  it('keeps the card while the list loads and after it failed', async () => {
    vi.mocked(api.listCollections).mockReturnValueOnce(new Promise(() => {}));
    const { unmount } = renderPage();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Nová kolekce' })).toBeInTheDocument();
    unmount();

    vi.mocked(api.listCollections).mockRejectedValue(new Error('HTTP 500'));
    renderPage();
    const banner = await screen.findByRole('alert');
    expect(follows(screen.getByRole('button', { name: 'Nová kolekce' }), banner)).toBe(true);
  });
});
