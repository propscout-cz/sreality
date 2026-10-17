/* Sbírky on the phone (#1769): /collections under the phone shell is the
 * hairline list plus "Nová sbírka" — a name in a sheet, created through the
 * desktop's own `createCollection` with monitoring off — and no rename, delete
 * or monitoring control anywhere. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { ROUTES } from '@/lib/routes';
import type { Collection } from '@/lib/types';

import Collections from '../Collections';

vi.mock('@/lib/phoneShell', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/phoneShell')>()),
  usePhoneShell: () => true,
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  listCollections: vi.fn(),
  createCollection: vi.fn(),
  deleteCollection: vi.fn(),
}));

const coll = (c: Partial<Collection>): Collection => ({
  id: 1,
  name: 'Shortlist',
  description: null,
  created_at: '2026-10-03T09:00:00Z',
  updated_at: '2026-10-11T09:00:00Z',
  listing_count: 12,
  monitoring_enabled: false,
  notify_channels: [],
  is_system: false,
  owner_email: null,
  shared_with: [],
  ...c,
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-12T12:00:00Z'));
  vi.mocked(api.listCollections).mockResolvedValue({
    data: [
      coll({}),
      coll({ id: 2, name: 'Vysočany sledované', monitoring_enabled: true, listing_count: 1, created_at: '2026-10-12T07:00:00Z' }),
    ],
    total: 2,
  });
  vi.mocked(api.createCollection).mockImplementation(async (input) => coll({ id: 3, name: input.name, listing_count: 0 }));
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/collections']}>
        <Collections />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('<Collections> on the phone', () => {
  it('lists each collection as a row: name, count, founding day, the monitored mark', async () => {
    renderPage();
    const list = await screen.findByRole('list', { name: 'Sbírky' });
    const rows = within(list).getAllByRole('link');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute('href', ROUTES.collectionDetail.build({ id: 1 }));
    expect(rows[0].textContent?.replace(/ /g, ' ')).toContain('12 nemovitostí · založena 3. 10.');
    expect(within(rows[0]).queryByText('sledováno')).toBeNull();
    expect(rows[1].textContent).toContain('1 nemovitost · založena dnes');
    expect(within(rows[1]).getByText('sledováno')).toBeInTheDocument();
  });

  it('offers no rename, delete, monitoring or sharing control — the footer says where they live', async () => {
    renderPage();
    await screen.findByRole('list', { name: 'Sbírky' });
    expect(screen.queryByRole('button', { name: /delete|smazat/i })).toBeNull();
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.getByText('Přejmenování, smazání, sledování a sdílení nastavíte na počítači.')).toBeInTheDocument();
  });

  it('creates a collection from the sheet with monitoring off', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Sbírky' });
    await user.click(screen.getByRole('button', { name: 'Nová sbírka' }));
    const sheet = screen.getByRole('dialog', { name: 'Nová sbírka' });
    const submit = within(sheet).getByRole('button', { name: 'Založit sbírku' });
    expect(submit).toBeDisabled();

    await user.type(within(sheet).getByLabelText('Název'), '  K prohlídce  ');
    await user.click(submit);
    await waitFor(() =>
      expect(api.createCollection).toHaveBeenCalledWith({
        name: 'K prohlídce',
        description: null,
        monitoring_enabled: false,
      }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Nová sbírka' })).toBeNull());
    expect(api.listCollections).toHaveBeenCalledTimes(2);
  });

  it('refuses a name already taken, as the desktop form does', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Sbírky' });
    await user.click(screen.getByRole('button', { name: 'Nová sbírka' }));
    const sheet = screen.getByRole('dialog', { name: 'Nová sbírka' });
    await user.type(within(sheet).getByLabelText('Název'), 'shortlist{Enter}');
    expect(within(sheet).getByText('Sbírka „shortlist“ už existuje.')).toBeInTheDocument();
    expect(within(sheet).getByRole('button', { name: 'Založit sbírku' })).toBeDisabled();
    expect(api.createCollection).not.toHaveBeenCalled();
  });

  it('says a failed read failed, never "no collections"', async () => {
    vi.mocked(api.listCollections).mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText('Sbírky se nepodařilo načíst.')).toBeInTheDocument();
    expect(screen.queryByText('Zatím žádná sbírka.')).toBeNull();
  });

  it('lists collections shared with me in their own group, read-only, by owner', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [
        coll({}),
        coll({
          id: 5,
          name: 'Od Jany',
          monitoring_enabled: false,
          listing_count: 4,
          owner_email: 'jana@example.com',
          shared_with: [{ account_id: 'me', email: null }],
        }),
      ],
      total: 2,
    });
    renderPage();
    const mine = await screen.findByRole('list', { name: 'Sbírky' });
    expect(within(mine).getAllByRole('link')).toHaveLength(1);
    expect(screen.getByRole('heading', { name: 'Sdílené se mnou' })).toBeInTheDocument();
    const shared = screen.getByRole('list', { name: 'Sdílené se mnou' });
    const [row] = within(shared).getAllByRole('link');
    expect(row).toHaveAttribute('href', ROUTES.collectionDetail.build({ id: 5 }));
    expect(row.textContent?.replace(/\u00a0/g, ' ')).toContain('4 nemovitosti · sdílí jana@example.com');
    expect(within(row).queryByText('sledováno')).toBeNull();
  });

  it('checks a new name against my own collections only', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [coll({}), coll({ id: 5, name: 'Od Jany', owner_email: 'jana@example.com' })],
      total: 2,
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole('list', { name: 'Sbírky' });
    await user.click(screen.getByRole('button', { name: 'Nová sbírka' }));
    const sheet = screen.getByRole('dialog', { name: 'Nová sbírka' });
    await user.type(within(sheet).getByLabelText('Název'), 'Od Jany');
    expect(within(sheet).queryByText(/už existuje/)).toBeNull();
    expect(within(sheet).getByRole('button', { name: 'Založit sbírku' })).toBeEnabled();
  });

  it('shows the shared group even when I have no collection of my own', async () => {
    vi.mocked(api.listCollections).mockResolvedValue({
      data: [coll({ id: 5, name: 'Od Jany', owner_email: 'jana@example.com' })],
      total: 1,
    });
    renderPage();
    expect(await screen.findByText('Zatím žádná vlastní sbírka.')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Sdílené se mnou' })).toBeInTheDocument();
  });
});
