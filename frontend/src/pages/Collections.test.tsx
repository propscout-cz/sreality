/* Collections — the curated-lists page.
 *
 * Pins the new-collection form's two text fields, which used to be named by a
 * placeholder only (a placeholder is not an accessible name, and it disappears
 * the moment the operator types). The monitoring checkbox was already wrapped
 * in its own <label>, so it is asserted here as the control that must NOT have
 * changed.
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

describe('<Collections>', () => {
  beforeEach(() => {
    vi.mocked(api.listCollections).mockResolvedValue({ data: [], total: 0 });
  });

  it('names both new-collection fields and leaves the monitoring checkbox named', async () => {
    renderPage();
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

  it('submits what was typed into the named fields', async () => {
    vi.mocked(api.createCollection).mockResolvedValue({} as never);
    renderPage();
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
  });

  /* Sharing (migration 610): what is shared with me is its own section, named by
   * its owner, with none of the owner's controls. */
  it('lists collections shared with me apart, and counts my recipients', async () => {
    const base = {
      description: null, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
      listing_count: 1, monitoring_enabled: false, notify_channels: [], is_system: false,
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
    const section = screen.getByText('Shared with me').closest('section') as HTMLElement;
    expect(within(section).getByRole('link', { name: 'Od Petra' })).toBeInTheDocument();
    expect(within(section).getAllByText(/Shared by petr@example\.cz/)).toHaveLength(2);
    expect(within(section).queryByRole('button', { name: 'Delete' })).toBeNull();
    expect(within(section).queryByText('Default')).toBeNull();
    // My own row keeps its Delete.
    expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(1);

    // A shared collection's name is not mine to collide with.
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
      target: { value: 'Od Petra' },
    });
    expect(screen.queryByText(/already exists/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Create' })).toBeEnabled();
  });
});
