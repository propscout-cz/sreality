/* WatchdogManage — the redesign's watchdog rulings: Delete sits in a ⋯ menu beside
 * edit and keeps its confirm (P24), the created date is a grey line under the name
 * instead of a column (P25), and the page title has no small-caps label (P26). */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { fmtAbsolute, fmtRelative } from '@/lib/format';
import type { WatchdogFilterSpec, WatchdogSubscription } from '@/lib/types';

// Factory is hoisted — keep it free of outer-scope references.
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  listWatchdogSubscriptions: vi.fn(),
  deleteWatchdogSubscription: vi.fn(),
  updateWatchdogSubscription: vi.fn(),
}));

import { deleteWatchdogSubscription, listWatchdogSubscriptions } from '@/lib/api';
import { expectNoNestedInteractive } from '@/test/a11y';
import WatchdogManage from './WatchdogManage';

const DAY = 24 * 60 * 60 * 1000;
const CONFIRM_TEXT = 'Delete this watchdog? Its notifications leave your feed.';

let created: string;

function subscriptions(): WatchdogSubscription[] {
  const spec = { category_main_in: ['byty'], category_type: 'prodej', dispositions: ['2+kk'] } as WatchdogFilterSpec;
  return [
    {
      id: 'w1',
      name: 'Praha 2+kk',
      filter_spec: spec,
      is_active: true,
      created_at: created,
      updated_at: created,
      dispatch_count: 12,
    },
    {
      id: 'w2',
      name: 'Brno 3+1',
      filter_spec: { ...spec, dispositions: ['3+1'] },
      is_active: false,
      created_at: created,
      updated_at: created,
      dispatch_count: 0,
    },
  ];
}

async function renderPage() {
  const rows = subscriptions();
  vi.mocked(listWatchdogSubscriptions).mockResolvedValue({ data: rows, total: rows.length });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <WatchdogManage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole('link', { name: 'Praha 2+kk' });
  return view;
}

const rowOf = (name: string) => screen.getByRole('link', { name }).closest('tr') as HTMLElement;

beforeEach(() => {
  created = new Date(Date.now() - 3 * DAY).toISOString();
  vi.restoreAllMocks();
  vi.mocked(listWatchdogSubscriptions).mockReset();
  vi.mocked(deleteWatchdogSubscription).mockReset();
  vi.mocked(deleteWatchdogSubscription).mockResolvedValue({ deleted: true });
});

describe('<WatchdogManage> header (P26)', () => {
  it('shows the h1 alone, with no small-caps label above it', async () => {
    await renderPage();
    expect(screen.getByRole('heading', { level: 1, name: 'Manage watchdogs' })).toBeInTheDocument();
    expect(screen.queryByText('Watchdog', { exact: true })).toBeNull();
  });
});

describe('<WatchdogManage> created date (P25)', () => {
  it('has no Created column', async () => {
    await renderPage();
    const headers = screen.getAllByRole('columnheader').map((th) => th.textContent?.trim());
    expect(headers).toEqual(['Name', 'Filter summary', 'Fired', 'Status', '']);
  });

  it('reads as a grey line under the name, outside the name link, with the full time as its tooltip', async () => {
    await renderPage();
    const link = screen.getByRole('link', { name: 'Praha 2+kk' });
    expect(link).toHaveAccessibleName('Praha 2+kk');
    const cell = link.closest('td') as HTMLElement;
    const line = Array.from(cell.querySelectorAll('[title]')).find(
      (el) => el.getAttribute('title') === fmtAbsolute(created),
    );
    expect(line).toBeDefined();
    // fmtRelative joins the number and its unit with a narrow no-break space, so
    // compare the raw text: the whitespace-normalising matchers would not.
    expect(line!.textContent).toBe(`Created ${fmtRelative(created)}`);
    expect(link.contains(line!)).toBe(false);
    expect(line).toHaveClass('text-[var(--color-ink-3)]');
    expect(line!.querySelector('.font-mono.tabular-nums')?.textContent).toBe(fmtRelative(created));
    expect(rowOf('Praha 2+kk').querySelectorAll('td')).toHaveLength(5);
  });

  it('gives the freed width to the filter summary, still truncated with the full text as its tooltip', async () => {
    await renderPage();
    const summary = within(rowOf('Praha 2+kk')).getByTitle(/byty \/ prodej/);
    expect(summary).toHaveClass('block', 'truncate');
    expect(summary.closest('td')).toHaveClass('max-w-[540px]');
  });
});

describe('<WatchdogManage> delete in the ⋯ menu (P24)', () => {
  it('keeps edit in the row and shows no delete control until the menu opens', async () => {
    await renderPage();
    const row = within(rowOf('Praha 2+kk'));
    expect(row.queryByRole('button', { name: /delete/i })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: /smazat/i })).toBeNull();
    expect(row.getByRole('link', { name: 'edit' })).toHaveAttribute('href', '/watchdog/w1/edit');
  });

  it('asks the same confirm, and deletes nothing when it is declined', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await renderPage();
    const user = userEvent.setup();
    const trigger = within(rowOf('Praha 2+kk')).getByRole('button', { name: 'Actions: Praha 2+kk' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    await user.click(trigger);
    await user.click(screen.getByRole('menuitem', { name: 'Smazat…' }));
    expect(confirm).toHaveBeenCalledWith(CONFIRM_TEXT);
    expect(deleteWatchdogSubscription).not.toHaveBeenCalled();
  });

  it('deletes that row’s watchdog, and only it, once the confirm is accepted', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await renderPage();
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Actions: Praha 2+kk' }));
    await user.click(screen.getByRole('menuitem', { name: 'Smazat…' }));
    await waitFor(() => expect(deleteWatchdogSubscription).toHaveBeenCalledTimes(1));
    expect(deleteWatchdogSubscription).toHaveBeenCalledWith('w1');
    expect(deleteWatchdogSubscription).not.toHaveBeenCalledWith('w2');
  });

  it('nests no control inside another', async () => {
    const { container } = await renderPage();
    expectNoNestedInteractive(container);
  });
});
