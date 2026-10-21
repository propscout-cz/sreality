/* Notifications — the feed row's accessible NAME must be the row's own text.
 *
 * The unread dot used to carry aria-label="unread", which is injected as the
 * first token of the enclosing <Link> / <button> name — so a row renamed itself
 * the moment it was marked seen. The dot is now decorative and the state rides
 * on the control's DESCRIPTION, which is what these tests pin.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { notificationKeys } from '@/lib/queries';
import type {
  NotificationUnreadCount,
  WatchdogDispatch,
  WatchdogDispatchesResponse,
} from '@/lib/types';

const baseDispatch: WatchdogDispatch = {
  id: 'd1',
  source_kind: 'watchdog',
  subscription_id: 's1',
  subscription_name: 'Praha 2+kk',
  collection_id: null,
  collection_name: null,
  sreality_id: 12345,
  property_id: 999,
  change_kind: 'new',
  message: null,
  check_key: null,
  share_kind: null,
  share_ref: null,
  dispatched_at: '2026-05-12T10:00:00+00:00',
  seen_at: null,
  trigger_price_czk: null,
  prev_price_czk: null,
  trigger_snapshot_id: null,
  estimation_run_id: null,
  estimation_status: null,
  estimation_kind: null,
  estimated_monthly_rent_czk: null,
  estimated_sale_price_czk: null,
  gross_yield_pct: null,
  confidence: null,
  category_main: 'byty',
  category_type: 'prodej',
  price_czk: 8_900_000,
  price_unit: null,
  area_m2: 55,
  disposition: '2+kk',
  subtype: null,
  display_label: 'Praha 5',
  is_active: true,
  first_seen_at: null,
  last_seen_at: null,
  mf_gross_yield_pct: null,
  source: 'sreality',
  source_url: null,
};

const feed: WatchdogDispatchesResponse = {
  data: [
    baseDispatch,
    {
      ...baseDispatch,
      id: 'd2',
      sreality_id: 22222,
      display_label: 'Brno-střed',
      seen_at: '2026-05-12T11:00:00+00:00',
    },
    {
      ...baseDispatch,
      id: 'd3',
      source_kind: 'system_health',
      change_kind: 'system_alert',
      message: 'Ingest stalled',
      check_key: 'scraping',
      subscription_id: null,
      subscription_name: null,
    },
    {
      ...baseDispatch,
      id: 'd4',
      source_kind: 'share',
      change_kind: 'shared',
      message: 'owner@example.com shared the collection "Vinohrady" with you',
      subscription_id: null,
      subscription_name: null,
      collection_id: 42,
      collection_name: 'Vinohrady',
      property_id: null,
      display_label: null,
      price_czk: null,
    },
  ],
  total: 4,
  limit: 100,
  offset: 0,
  next_cursor: null,
};

const counts: NotificationUnreadCount = {
  watchdog: 2,
  collection_monitor: 0,
  share: 0,
  system_health: 0,
  total: 2,
  unread_count: 2,
};

// The cache is seeded below, so nothing here fires during the assertions; the
// mocks just guard against an accidental network call. Factory is hoisted —
// keep it free of outer-scope references.
vi.mock('@/lib/api', async (orig) => ({
  ...(await orig<typeof import('@/lib/api')>()),
  listNotifications: vi.fn(),
  getNotificationUnreadCount: vi.fn(),
  markWatchdogDispatchSeen: vi.fn(),
  // The transport Health's Acknowledge posts through: the bell must never reach it.
  apiPost: vi.fn(),
}));

import { apiPost, listNotifications, markWatchdogDispatchSeen } from '@/lib/api';
import { expectNoNestedInteractive } from '@/test/a11y';
import Notifications from './Notifications';

function renderPage(page: WatchdogDispatchesResponse = feed) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(notificationKeys.feed({ source: 'all', seen: 'all' }), page);
  qc.setQueryData(notificationKeys.unreadCount, counts);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Notifications />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('<Notifications> row names', () => {
  it('names an unread listing row by its own text and describes it as unread', () => {
    renderPage();
    const row = screen.getByRole('link', { name: /^New match Praha 5/ });
    expect(row).toHaveAccessibleDescription('unread');
  });

  it('names a read listing row identically — the name never flips on mark-seen', () => {
    renderPage();
    const read = screen.getByRole('link', { name: /^New match Brno-střed/ });
    expect(read).toHaveAccessibleDescription('');
  });

  it('names the system-alert row by its message, unread state in the description', () => {
    renderPage();
    const alert = screen.getByRole('button', { name: /^System Ingest stalled/ });
    expect(alert).toHaveAccessibleDescription('unread');
  });
});

describe('<Notifications> collection share row', () => {
  it('shows the message and links to the shared collection', () => {
    renderPage();
    const text = screen.getByRole('button', { name: /^Shared owner@example\.com shared the collection "Vinohrady"/ });
    expect(text).toHaveAccessibleDescription('unread');
    expect(screen.getByRole('link', { name: 'Open collection' })).toHaveAttribute(
      'href',
      '/collection/42',
    );
  });

  it('marks the share read when the collection is opened', async () => {
    vi.mocked(markWatchdogDispatchSeen).mockResolvedValue({ ...baseDispatch, id: 'd4', seen_at: '2026-05-12T12:00:00+00:00' });
    renderPage();
    fireEvent.click(screen.getByRole('link', { name: 'Open collection' }));
    await waitFor(() => expect(markWatchdogDispatchSeen).toHaveBeenCalledWith('d4'));
  });

  /* The same kind and the same renderer for a pipeline share (migration 641):
   * no collection_id, so the link opens the Pipeline tab instead. */
  it('renders a pipeline share with the one renderer, linking to the pipeline', () => {
    const pipelineShare = {
      ...feed.data[3],
      id: 'd5',
      message: 'owner@example.com shared their pipeline with you (edit)',
      collection_id: null,
      collection_name: null,
    };
    renderPage({ ...feed, data: [pipelineShare], total: 1 });
    expect(
      screen.getByRole('button', { name: /^Shared owner@example\.com shared their pipeline with you \(edit\)/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open pipeline' })).toHaveAttribute('href', '/pipeline');
    expect(screen.queryByRole('link', { name: 'Open collection' })).toBeNull();
  });

  /* A dashboard share (migration 647) names its kind and the dashboard's uuid;
   * the link opens Browse › Stats, which loads the dashboard from the URL. */
  it('renders a dashboard share with the one renderer, linking to the Stats tab', async () => {
    vi.mocked(markWatchdogDispatchSeen).mockResolvedValue({ ...baseDispatch, id: 'd6', seen_at: '2026-05-12T12:00:00+00:00' });
    const dashboardShare = {
      ...feed.data[3],
      id: 'd6',
      message: 'owner@example.com shared the dashboard "Ceny Praha" with you',
      collection_id: null,
      collection_name: null,
      share_kind: 'dashboard' as const,
      share_ref: '6f1c2d3e-0000-4000-8000-000000000001',
    };
    renderPage({ ...feed, data: [dashboardShare], total: 1 });
    expect(
      screen.getByRole('button', { name: /^Shared owner@example\.com shared the dashboard "Ceny Praha" with you/ }),
    ).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Open dashboard' });
    expect(link).toHaveAttribute('href', '/browse?tab=stats&dashboard=6f1c2d3e-0000-4000-8000-000000000001');
    expect(screen.queryByRole('link', { name: 'Open pipeline' })).toBeNull();
    fireEvent.click(link);
    await waitFor(() => expect(markWatchdogDispatchSeen).toHaveBeenCalledWith('d6'));
  });

  it('keeps a collection share on its collection when the API names the kind', () => {
    renderPage({
      ...feed,
      data: [{ ...feed.data[3], share_kind: 'collection', share_ref: '42' }],
      total: 1,
    });
    expect(screen.getByRole('link', { name: 'Open collection' })).toHaveAttribute('href', '/collection/42');
  });

  it('offers a Shared source tab that asks for the share kind', async () => {
    vi.mocked(listNotifications).mockResolvedValue({ data: [], total: 0 } as never);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Shared' }));
    await waitFor(() =>
      expect(listNotifications).toHaveBeenLastCalledWith(
        expect.objectContaining({ source_kind: 'share' }),
      ));
  });
});

/* A system alert opens Health at the check it is about (the API reads the key off
 * the dedupe key, as the outbox's email and Telegram links do). The link is a
 * SIBLING of the mark-seen button, never nested in it, and reading the alert —
 * by either control — marks it read without ever acknowledging the incident:
 * only Acknowledge on Health stops the reminders. */
describe('<Notifications> system alert row', () => {
  beforeEach(() => {
    vi.mocked(markWatchdogDispatchSeen).mockClear();
    vi.mocked(apiPost).mockClear();
  });

  it('opens Health at the check the alert is about', () => {
    renderPage();
    expect(screen.getByRole('link', { name: 'Open Health' })).toHaveAttribute(
      'href',
      '/health#scraping',
    );
  });

  it('opens plain Health when the API names no check', () => {
    renderPage({
      ...feed,
      data: feed.data.map((d) => (d.id === 'd3' ? { ...d, check_key: null } : d)),
    });
    expect(screen.getByRole('link', { name: 'Open Health' })).toHaveAttribute('href', '/health');
  });

  it('marks the alert read from either control and never acknowledges it', async () => {
    vi.mocked(markWatchdogDispatchSeen).mockResolvedValue({
      ...feed.data[2],
      seen_at: '2026-05-12T12:00:00+00:00',
    });
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /^System Ingest stalled/ }));
    fireEvent.click(screen.getByRole('link', { name: 'Open Health' }));
    await waitFor(() => expect(markWatchdogDispatchSeen).toHaveBeenCalledTimes(2));
    expect(vi.mocked(markWatchdogDispatchSeen).mock.calls).toEqual([['d3'], ['d3']]);
    expect(apiPost).not.toHaveBeenCalled();
  });

  it('nests no control inside another', () => {
    const { container } = renderPage();
    expectNoNestedInteractive(container);
  });
});
