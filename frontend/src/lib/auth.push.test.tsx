/* The identity binding end to end (AUDIT F18, program § 3.6): AuthProvider over
 * the REAL lib/push and a fake push platform. A subscription belongs to the
 * device's service-worker registration, not to a user, so the app ends it on
 * every change of user; the explicit sign-out tells the server first, while
 * the JWT still works. The F18 scenario: the operator lends his iPhone, signs
 * out, a colleague signs in, and none of his alerts reach her lock screen. */
import { act, render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthRetryableFetchError } from '@supabase/supabase-js';

import { installPushFakes, type PushFakes } from '@/test/pushFakes';

const h = vi.hoisted(() => ({
  listeners: [] as Array<(event: string, session: unknown) => void>,
  session: null as unknown,
  error: null as unknown,
  log: null as string[] | null,
}));

vi.mock('./supabase', () => {
  const rows = (relation: string) =>
    relation === 'plans' ? [{ key: 'free', agendas: { browse: true }, is_default: true }] : [];
  const table = (relation: string) => {
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
        resolve({ data: single ? null : rows(relation), error: null, status: 200 }),
    };
    return b;
  };
  const emit = (event: string, session: unknown) => {
    h.session = session;
    for (const cb of h.listeners) cb(event, session);
  };
  return {
    isSupabaseConfigured: () => true,
    supabase: {
      from: table,
      auth: {
        getSession: () =>
          Promise.resolve({ data: { session: h.session }, error: h.session ? null : h.error }),
        onAuthStateChange: (cb: (event: string, session: unknown) => void) => {
          h.listeners.push(cb);
          return { data: { subscription: { unsubscribe: () => {} } } };
        },
        signOut: vi.fn(async () => {
          h.log?.push('supabase.signOut');
          emit('SIGNED_OUT', null);
          return { error: null };
        }),
      },
    },
  };
});

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  putPushSubscription: vi.fn(async () => {
    h.log?.push('PUT');
  }),
  deletePushSubscription: vi.fn(async (body: { reason: string }) => {
    h.log?.push(`DELETE ${body.reason}`);
  }),
}));

import * as api from './api';
import { AuthProvider, useAuth } from './auth';
import { readPushOwner, writePushOwner } from './push';

const sessionFor = (id: string) => ({ access_token: 't', user: { id, app_metadata: {} } });

let fakes: PushFakes;
let auth: ReturnType<typeof useAuth>;

function Probe() {
  auth = useAuth();
  return null;
}

function renderSignedIn(id: string | null) {
  h.session = id ? sessionFor(id) : null;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

const emit = (event: string, session: unknown) =>
  act(() => {
    h.session = session;
    for (const cb of h.listeners) cb(event, session);
  });

beforeEach(() => {
  localStorage.clear();
  h.listeners = [];
  h.error = null;
  fakes = installPushFakes({ subscribed: true, permission: 'granted' });
  h.log = fakes.log;
  writePushOwner('operator');
});

afterEach(() => {
  fakes.uninstall();
  vi.clearAllMocks();
});

describe('push binding follows identity', () => {
  it("re-sends the owner's subscription when the app launches signed in", async () => {
    renderSignedIn('operator');
    await waitFor(() => expect(fakes.log).toEqual(['PUT']));
    expect(fakes.subscription).not.toBeNull();
  });

  it('signing out tells the server, ends the subscription here, then signs out', async () => {
    renderSignedIn('operator');
    await waitFor(() => expect(fakes.log).toEqual(['PUT']));
    fakes.log.length = 0;

    await act(() => auth.signOut());
    expect(fakes.log).toEqual(['DELETE signed_out', 'unsubscribe', 'supabase.signOut']);
    expect(fakes.subscription).toBeNull();
    expect(readPushOwner()).toBeNull();
  });

  it("a session that ends on its own still ends the subscription, with no JWT", async () => {
    renderSignedIn('operator');
    await waitFor(() => expect(fakes.log).toEqual(['PUT']));
    fakes.log.length = 0;

    emit('SIGNED_OUT', null);
    await waitFor(() => expect(fakes.subscription).toBeNull());
    expect(fakes.log).toEqual(['unsubscribe']);
    expect(api.deletePushSubscription).not.toHaveBeenCalled();
  });

  it("an account switch ends the previous user's subscription and binds nothing to the next", async () => {
    renderSignedIn('operator');
    await waitFor(() => expect(fakes.log).toEqual(['PUT']));
    fakes.log.length = 0;

    emit('SIGNED_IN', sessionFor('colleague'));
    await waitFor(() => expect(fakes.subscription).toBeNull());
    expect(fakes.log).toEqual(['unsubscribe']);
    expect(api.putPushSubscription).toHaveBeenCalledTimes(1);
  });

  it("an app that launches signed out ends the subscription its last user left", async () => {
    /* The operator's session ended while the app was closed (a revoked refresh). */
    renderSignedIn(null);
    await waitFor(() => expect(fakes.subscription).toBeNull());
    expect(fakes.log).toEqual(['unsubscribe']);
    expect(api.putPushSubscription).not.toHaveBeenCalled();
  });

  it('a launch that could not refresh its session keeps the subscription for the refresh that works', async () => {
    h.error = new AuthRetryableFetchError('Failed to fetch', 0);
    renderSignedIn(null);
    await new Promise((r) => setTimeout(r, 30));
    expect(fakes.subscription).not.toBeNull();
    expect(fakes.log).toEqual([]);

    emit('TOKEN_REFRESHED', sessionFor('operator'));
    await waitFor(() => expect(fakes.log).toEqual(['PUT']));
    expect(fakes.subscription).not.toBeNull();
  });

  it("a colleague's session found at launch never inherits the operator's subscription", async () => {
    renderSignedIn('colleague');
    await waitFor(() => expect(fakes.subscription).toBeNull());
    expect(fakes.log).toEqual(['unsubscribe']);
    expect(api.putPushSubscription).not.toHaveBeenCalled();
  });
});
