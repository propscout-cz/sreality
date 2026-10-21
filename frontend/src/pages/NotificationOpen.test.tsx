/* The push landing (notifications push § 3.5): `/n/{id}?d={delivery}` marks the
 * dispatch read, reports the open when the tap named its delivery, and REPLACES
 * itself with the row's target as the mark-seen answer names it (a merge has
 * re-pointed its property by then); a 404 or any failure lands on the feed.
 * The coalesced push's `/notifications?d=` reports its open once and drops the
 * parameter. */
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation, useNavigationType } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { PHONE_SHELL_QUERY } from '@/lib/phoneShell';
import { notificationKeys } from '@/lib/queries';
import type { WatchdogDispatch } from '@/lib/types';

import NotificationOpen from './NotificationOpen';
import Notifications from './Notifications';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  markWatchdogDispatchSeen: vi.fn(),
  postPushOpened: vi.fn(),
  listNotifications: vi.fn(),
}));

const row = {
  id: '7f1c',
  source_kind: 'collection_monitor',
  property_id: 555,
  collection_id: 7,
  share_kind: null,
  share_ref: null,
  seen_at: '2026-10-21T10:00:00+00:00',
} as unknown as WatchdogDispatch;

function Where() {
  const { pathname, search } = useLocation();
  return <p data-testid="where">{`${useNavigationType()} ${pathname}${search}`}</p>;
}

function renderAt(path: string, qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/n/:dispatchId" element={<NotificationOpen />} />
          <Route path="/property/:propertyId" element={<Where />} />
          <Route path="/collection/:id" element={<Where />} />
          <Route
            path="/notifications"
            element={
              <>
                <Where />
                <Notifications />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return qc;
}

beforeEach(() => {
  vi.mocked(api.markWatchdogDispatchSeen).mockReset();
  vi.mocked(api.postPushOpened).mockReset().mockResolvedValue(undefined);
  vi.mocked(api.listNotifications).mockResolvedValue({
    data: [], total: 0, limit: 50, offset: 0, next_cursor: null,
  });
});

describe('/n/:dispatchId', () => {
  it('marks the dispatch read, reports the open and replaces itself with the target', async () => {
    vi.mocked(api.markWatchdogDispatchSeen).mockResolvedValue(row);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(notificationKeys.unreadCount, { unread_count: 3 });
    renderAt('/n/7f1c?d=42', qc);

    expect(screen.getByRole('status')).toHaveTextContent('Opening…');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('REPLACE /property/555'));
    expect(api.markWatchdogDispatchSeen).toHaveBeenCalledTimes(1);
    expect(api.markWatchdogDispatchSeen).toHaveBeenCalledWith('7f1c');
    expect(api.postPushOpened).toHaveBeenCalledTimes(1);
    expect(api.postPushOpened).toHaveBeenCalledWith(42);
    /* The bell's number refetches, as the feed's own mark-seen does. */
    expect(qc.getQueryState(notificationKeys.unreadCount)?.isInvalidated).toBe(true);
  });

  it('says so in Czech in the phone frame', async () => {
    vi.mocked(api.markWatchdogDispatchSeen).mockReturnValue(new Promise(() => {}));
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query === PHONE_SHELL_QUERY,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
    try {
      renderAt('/n/7f1c?d=42');
      expect(screen.getByRole('status')).toHaveTextContent('Otevírám…');
    } finally {
      window.matchMedia = original;
    }
  });

  it('opens a share on what was shared', async () => {
    vi.mocked(api.markWatchdogDispatchSeen).mockResolvedValue({
      ...row, source_kind: 'share', share_kind: 'collection', collection_id: 42, property_id: null,
    } as WatchdogDispatch);
    renderAt('/n/7f1c?d=9');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('REPLACE /collection/42'));
  });

  it('lands on the feed when the dispatch is not found', async () => {
    vi.mocked(api.markWatchdogDispatchSeen).mockRejectedValue(
      new ApiError('dispatch not found', 404, { detail: 'dispatch not found' }),
    );
    renderAt('/n/7f1c?d=42');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('REPLACE /notifications'));
    /* The tap still happened. */
    expect(api.postPushOpened).toHaveBeenCalledWith(42);
  });

  it('lands on the feed on any other failure', async () => {
    vi.mocked(api.markWatchdogDispatchSeen).mockRejectedValue(new ApiError('down', 0, null, 'network'));
    renderAt('/n/7f1c');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('REPLACE /notifications'));
  });

  it('reports nothing without a delivery id, or with one that is not a number', async () => {
    vi.mocked(api.markWatchdogDispatchSeen).mockResolvedValue(row);
    renderAt('/n/7f1c');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/property/555'));
    renderAt('/n/7f1c?d=1;drop');
    await waitFor(() => expect(api.markWatchdogDispatchSeen).toHaveBeenCalledTimes(2));
    expect(api.postPushOpened).not.toHaveBeenCalled();
  });

  it('never waits for the open report', async () => {
    vi.mocked(api.markWatchdogDispatchSeen).mockResolvedValue(row);
    vi.mocked(api.postPushOpened).mockReturnValue(new Promise(() => {}));
    renderAt('/n/7f1c?d=42');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('REPLACE /property/555'));
  });

  it('survives a failed open report', async () => {
    vi.mocked(api.markWatchdogDispatchSeen).mockResolvedValue(row);
    vi.mocked(api.postPushOpened).mockRejectedValue(new ApiError('boom', 500, null));
    renderAt('/n/7f1c?d=42');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('REPLACE /property/555'));
  });
});

describe('/notifications?d=', () => {
  it('reports the coalesced push once and drops the parameter', async () => {
    renderAt('/notifications?d=17&x=1');
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('REPLACE /notifications?x=1'));
    expect(api.postPushOpened).toHaveBeenCalledTimes(1);
    expect(api.postPushOpened).toHaveBeenCalledWith(17);
  });

  it('reports nothing for the plain feed', async () => {
    renderAt('/notifications');
    await screen.findByText(/No notifications yet/);
    expect(api.postPushOpened).not.toHaveBeenCalled();
  });
});
