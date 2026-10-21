/* Session bootstrap: one read per user, and no cross-account cache bleed.
 *
 * Both properties here were live defects found by the hydration audit, and both
 * are invisible in normal use — the first only shows up in a network trace, the
 * second only when two accounts are used in one browser. So they get tests.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { ACTIVE_BOARD_KEY, EDIT_AS_OWNER_KEY, getActiveBoard, setActiveBoard, setEditAsOwner } from './acting';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthRetryableFetchError } from '@supabase/supabase-js';

const h = vi.hoisted(() => ({
  /* Emitted auth-state callbacks, so a test can fire events itself. */
  listeners: [] as Array<(event: string, session: unknown) => void>,
  initialSession: null as unknown,
  initialError: null as unknown,
  reads: [] as string[],
  /* Per relation, envelopes served (in order) before the real rows. */
  failNext: {} as Record<string, unknown[]>,
}));

vi.mock('./supabase', () => {
  const table = (relation: string) => {
    h.reads.push(relation);
    const rows =
      relation === 'plans'
        ? [{ key: 'free', agendas: { browse: true }, is_default: true }]
        : [];
    let single = false;
    const b: Record<string, unknown> = {
      select: () => b,
      retry: () => b,
      abortSignal: () => b,
      maybeSingle: () => {
        single = true;
        return b;
      },
      then: (resolve: (r: unknown) => unknown) =>
        resolve(
          h.failNext[relation]?.shift() ?? { data: single ? null : rows, error: null, status: 200 },
        ),
    };
    return b;
  };
  return {
    isSupabaseConfigured: () => true,
    supabase: {
      from: table,
      auth: {
        getSession: () =>
          Promise.resolve({ data: { session: h.initialSession }, error: h.initialError }),
        onAuthStateChange: (cb: (event: string, session: unknown) => void) => {
          h.listeners.push(cb);
          return { data: { subscription: { unsubscribe: () => {} } } };
        },
      },
    },
  };
});

vi.mock('./push', () => ({
  resyncPush: vi.fn(async () => {}),
  endPushHere: vi.fn(async () => {}),
  signOutPush: vi.fn(async () => {}),
}));
vi.mock('./appBadge', () => ({ syncAppBadge: vi.fn() }));

import { isTransientApiError } from './api';
import { syncAppBadge } from './appBadge';
import { AuthProvider, useAuth } from './auth';
import { endPushHere, resyncPush } from './push';

/* A fresh object each call — exactly what Supabase hands out per auth event,
 * and the reason keying on the session object refetched three times. */
const sessionFor = (userId: string) => ({
  access_token: 't', user: { id: userId, app_metadata: {} },
});

function Probe() {
  const { agendas } = useAuth();
  return <span data-testid="agendas">{agendas ? Object.keys(agendas).join(',') : 'null'}</span>;
}

function renderAuth(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider><Probe /></AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  h.listeners = [];
  h.reads = [];
  h.initialSession = null;
  h.initialError = null;
  h.failNext = {};
  vi.mocked(resyncPush).mockClear();
  vi.mocked(endPushHere).mockClear();
  vi.mocked(syncAppBadge).mockClear();
});

/* postgrest-js's resolved envelopes for a dropped connection and a refused grant. */
const NETWORK_DOWN = { data: null, error: { message: 'TypeError: Failed to fetch', code: '' }, status: 0 };
const REFUSED = {
  data: null,
  error: { code: '42501', message: 'permission denied for table plans' },
  status: 403,
};
/* main.tsx's rule, without its 1 s delay. */
const productionClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: (n, e) => n < 1 && isTransientApiError(e), retryDelay: 0 },
    },
  });

describe('AuthProvider session bootstrap', () => {
  it('reads entitlements + plans ONCE across repeated auth events for one user', async () => {
    h.initialSession = sessionFor('user-1');
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { getByTestId } = renderAuth(qc);

    await waitFor(() => expect(getByTestId('agendas').textContent).toBe('browse'));
    const afterFirst = h.reads.length;

    /* Supabase fires INITIAL_SESSION, SIGNED_IN and TOKEN_REFRESHED on a normal
       start, each with a NEW session object for the SAME user. Before this was
       keyed on user.id, each one re-ran the pair — six requests per app start,
       on every route, measured at 231-433 ms each. */
    for (const event of ['SIGNED_IN', 'TOKEN_REFRESHED', 'INITIAL_SESSION']) {
      for (const cb of h.listeners) cb(event, sessionFor('user-1'));
    }
    await new Promise((r) => setTimeout(r, 20));

    expect(h.reads.length).toBe(afterFirst);
    expect(h.reads.filter((r) => r === 'entitlements')).toHaveLength(1);
    expect(h.reads.filter((r) => r === 'plans')).toHaveLength(1);
  });

  it('clears the query cache when the signed-in identity changes', async () => {
    h.initialSession = sessionFor('user-1');
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { getByTestId } = renderAuth(qc);
    await waitFor(() => expect(getByTestId('agendas').textContent).toBe('browse'));

    /* Stand in for any RLS-scoped read the previous session left behind. */
    qc.setQueryData(['collections'], [{ id: 1, name: "user-1's collection" }]);
    expect(qc.getQueryData(['collections'])).toBeDefined();

    for (const cb of h.listeners) cb('SIGNED_IN', sessionFor('user-2'));

    await waitFor(() => expect(qc.getQueryData(['collections'])).toBeUndefined());
  });

  /* The read is cached for the whole session, so a blip must never resolve to
   * the "no constraint" fallback: it rejects, gets the one retry, and lands. */
  it('retries a transient agenda read instead of caching the fallback', async () => {
    h.initialSession = sessionFor('user-1');
    h.failNext = { plans: [NETWORK_DOWN] };
    const qc = productionClient();
    const { getByTestId } = renderAuth(qc);

    await waitFor(() => expect(getByTestId('agendas').textContent).toBe('browse'));
    expect(h.reads.filter((r) => r === 'plans')).toHaveLength(2);
  });

  it('caches nothing when the transient failure outlasts the retry', async () => {
    h.initialSession = sessionFor('user-1');
    h.failNext = { entitlements: [NETWORK_DOWN, NETWORK_DOWN] };
    const qc = productionClient();
    const { getByTestId } = renderAuth(qc);

    await waitFor(() =>
      expect(qc.getQueryState(['session', 'agendas', 'user-1'])?.status).toBe('error'),
    );
    expect(qc.getQueryData(['session', 'agendas', 'user-1'])).toBeUndefined();
    expect(getByTestId('agendas').textContent).toBe('null');
  });

  it('keeps the no-constraint fallback for a refused read, without retrying it', async () => {
    h.initialSession = sessionFor('user-1');
    h.failNext = { plans: [REFUSED] };
    const qc = productionClient();
    const { getByTestId } = renderAuth(qc);

    await waitFor(() =>
      expect(qc.getQueryState(['session', 'agendas', 'user-1'])?.status).toBe('success'),
    );
    expect(qc.getQueryData(['session', 'agendas', 'user-1'])).toBeNull();
    expect(getByTestId('agendas').textContent).toBe('null');
    expect(h.reads.filter((r) => r === 'plans')).toHaveLength(1);
  });

  it('forgets the previous account\'s pipeline board and "edit as owner" on an identity change', async () => {
    h.initialSession = sessionFor('user-1');
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { getByTestId } = renderAuth(qc);
    await waitFor(() => expect(getByTestId('agendas').textContent).toBe('browse'));
    act(() => {
      setActiveBoard({ account_id: 'owner-1', email: 'jana@example.cz', mode: 'edit' });
      setEditAsOwner(true);
    });
    expect(getActiveBoard()).not.toBeNull();

    for (const cb of h.listeners) cb('SIGNED_OUT', null);

    await waitFor(() => expect(getActiveBoard()).toBeNull());
    expect(localStorage.getItem(ACTIVE_BOARD_KEY)).toBeNull();
    expect(localStorage.getItem(EDIT_AS_OWNER_KEY)).toBeNull();
  });

  it('does not clear the cache on the first sign-in of a session', async () => {
    h.initialSession = null;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderAuth(qc);
    qc.setQueryData(['market-data'], ['anon-readable rows']);

    for (const cb of h.listeners) cb('SIGNED_IN', sessionFor('user-1'));
    await new Promise((r) => setTimeout(r, 20));

    // null -> user is a login, not a switch: nothing cached belonged to anyone else.
    expect(qc.getQueryData(['market-data'])).toBeDefined();
  });
});

/* iOS never fires `pushsubscriptionchange`: the launch and each sign-in re-send
 * this device's push subscription (lib/push), once per identity, never on the
 * auth events a session emits for the same user. */
describe('AuthProvider push re-sync', () => {
  it('re-sends once when the app launches signed in', async () => {
    h.initialSession = sessionFor('user-1');
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { getByTestId } = renderAuth(qc);
    await waitFor(() => expect(getByTestId('agendas').textContent).toBe('browse'));
    for (const event of ['SIGNED_IN', 'TOKEN_REFRESHED', 'INITIAL_SESSION']) {
      for (const cb of h.listeners) cb(event, sessionFor('user-1'));
    }
    await new Promise((r) => setTimeout(r, 20));
    expect(resyncPush).toHaveBeenCalledTimes(1);
  });

  it('re-sends on a sign-in, and not while signed out', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderAuth(qc);
    await new Promise((r) => setTimeout(r, 20));
    expect(resyncPush).not.toHaveBeenCalled();
    act(() => {
      for (const cb of h.listeners) cb('SIGNED_IN', sessionFor('user-1'));
    });
    await waitFor(() => expect(resyncPush).toHaveBeenCalledTimes(1));
  });
});

/* No subscription outlives its user (AUDIT F18): any change of user, sign-out
 * included, ends this device's subscription locally and clears the badge. The
 * explicit sign-out (its order: tell the server, end it here, then sign out)
 * is pinned against the real lib/push in auth.push.test.tsx. */
describe('AuthProvider push binding', () => {
  it('ends the subscription and clears the badge on sign-out', async () => {
    h.initialSession = sessionFor('user-1');
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { getByTestId } = renderAuth(qc);
    await waitFor(() => expect(getByTestId('agendas').textContent).toBe('browse'));
    expect(endPushHere).not.toHaveBeenCalled();

    act(() => {
      for (const cb of h.listeners) cb('SIGNED_OUT', null);
    });
    await waitFor(() => expect(endPushHere).toHaveBeenCalledTimes(1));
    expect(syncAppBadge).toHaveBeenCalledWith(0);
  });

  it('ends it on an account switch, and re-sends nothing for the new user', async () => {
    h.initialSession = sessionFor('user-1');
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { getByTestId } = renderAuth(qc);
    await waitFor(() => expect(getByTestId('agendas').textContent).toBe('browse'));
    vi.mocked(resyncPush).mockClear();

    act(() => {
      for (const cb of h.listeners) cb('SIGNED_IN', sessionFor('user-2'));
    });
    await waitFor(() => expect(endPushHere).toHaveBeenCalledTimes(1));
    expect(resyncPush).not.toHaveBeenCalled();
  });

  it('ends a subscription left behind, and clears the badge, when the app launches signed out', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderAuth(qc);
    await waitFor(() => expect(endPushHere).toHaveBeenCalledTimes(1));
    expect(syncAppBadge).toHaveBeenCalledWith(0);
    expect(resyncPush).not.toHaveBeenCalled();
  });

  /* A launch whose refresh failed in transit answers "no session" with an
   * error while supabase-js keeps the stored one: not certainly nobody. */
  it('ends nothing on a launch that could not refresh its session, until it is sure', async () => {
    h.initialError = new AuthRetryableFetchError('Failed to fetch', 0);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderAuth(qc);
    await new Promise((r) => setTimeout(r, 20));
    expect(endPushHere).not.toHaveBeenCalled();
    expect(syncAppBadge).not.toHaveBeenCalled();

    act(() => {
      for (const cb of h.listeners) cb('SIGNED_OUT', null);
    });
    await waitFor(() => expect(endPushHere).toHaveBeenCalledTimes(1));
    expect(syncAppBadge).toHaveBeenCalledWith(0);
  });

  it('leaves it alone on the events a session emits for the same user', async () => {
    h.initialSession = sessionFor('user-1');
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { getByTestId } = renderAuth(qc);
    await waitFor(() => expect(getByTestId('agendas').textContent).toBe('browse'));
    act(() => {
      for (const cb of h.listeners) cb('TOKEN_REFRESHED', sessionFor('user-1'));
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(endPushHere).not.toHaveBeenCalled();
    expect(syncAppBadge).not.toHaveBeenCalled();
  });
});
