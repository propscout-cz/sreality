/* `npm test` (node --test; Node >= 22.18 strips the types). */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createTokenSource, REFRESH_RETRY_DETAIL, type RefreshReply, type TokenDeps } from './token_source.ts';
import type { StoredSession } from './session.ts';

const T0 = 1_800_000_000;

/* A GoTrue stand-in: every refresh rotates the refresh token, and presenting a used one revokes the
 * whole session (Supabase's reuse detection), so two racing refreshes would end signed out. */
function world(opts: { expiresIn?: number } = {}) {
  let now = T0;
  let stored: StoredSession | null = {
    access_token: 'a1', refresh_token: 'r1', expires_at: T0 + (opts.expiresIn ?? 3600),
  };
  let valid: string | null = 'r1';
  const used = new Set<string>();
  let generation = 1;
  let outage: 'network' | 503 | 429 | null = null;

  const deps: TokenDeps = {
    load: async () => (stored == null ? null : { ...stored }),
    save: async (s) => { stored = { ...s }; },
    clear: async () => { stored = null; },
    now: () => now,
    refresh: async (token): Promise<RefreshReply | null> => {
      await new Promise((r) => setTimeout(r, 1));  // a real round trip, so callers can interleave
      if (outage === 'network') return null;
      if (outage != null) return { status: outage, body: { msg: 'unavailable' } };
      if (token !== valid) {
        if (used.has(token)) valid = null;
        return { status: 400, body: { code: 400, error_code: 'refresh_token_already_used', msg: 'Invalid Refresh Token' } };
      }
      used.add(token);
      generation += 1;
      valid = `r${generation}`;
      return {
        status: 200,
        body: { access_token: `a${generation}`, refresh_token: valid, expires_in: 3600, expires_at: now + 3600, token_type: 'bearer' },
      };
    },
  };
  return {
    deps,
    stored: () => stored,
    advance: (seconds: number) => { now += seconds; },
    outage: (kind: 'network' | 503 | 429 | null) => { outage = kind; },
    revoke: () => { valid = null; },
  };
}

test('a token far from expiry is used as it is', async () => {
  const w = world();
  assert.deepEqual(await createTokenSource(w.deps).accessToken(), { kind: 'token', token: 'a1' });
  assert.equal(w.stored()?.refresh_token, 'r1');
});

test('near expiry: refreshed first, then the call proceeds with the new token', async () => {
  const w = world({ expiresIn: 30 });
  assert.deepEqual(await createTokenSource(w.deps).accessToken(), { kind: 'token', token: 'a2' });
  assert.deepEqual(w.stored(), { access_token: 'a2', refresh_token: 'r2', expires_at: T0 + 3600 });
});

test('past expiry after a long idle: refreshed on the next call (no timer needed)', async () => {
  const w = world();
  w.advance(5 * 24 * 3600);
  assert.deepEqual(await createTokenSource(w.deps).accessToken(), { kind: 'token', token: 'a2' });
});

test('network error: still signed in, the call is retryable', async () => {
  const w = world({ expiresIn: 30 });
  w.outage('network');
  assert.deepEqual(await createTokenSource(w.deps).accessToken(), { kind: 'retry', detail: REFRESH_RETRY_DETAIL });
  assert.equal(w.stored()?.refresh_token, 'r1');
});

test('5xx: still signed in, the call is retryable', async () => {
  const w = world({ expiresIn: 30 });
  w.outage(503);
  assert.equal((await createTokenSource(w.deps).accessToken()).kind, 'retry');
  assert.equal(w.stored()?.refresh_token, 'r1');
});

test('429: still signed in, the call is retryable', async () => {
  const w = world({ expiresIn: 30 });
  w.outage(429);
  assert.equal((await createTokenSource(w.deps).accessToken()).kind, 'retry');
  assert.notEqual(w.stored(), null);
});

test('after the outage, the next call refreshes and proceeds', async () => {
  const w = world({ expiresIn: 30 });
  const source = createTokenSource(w.deps);
  w.outage('network');
  assert.equal((await source.accessToken()).kind, 'retry');
  w.outage(null);
  assert.deepEqual(await source.accessToken(), { kind: 'token', token: 'a2' });
});

test('refresh refused: signed out', async () => {
  const w = world({ expiresIn: 30 });
  w.revoke();
  assert.deepEqual(await createTokenSource(w.deps).accessToken(), { kind: 'signed_out' });
  assert.equal(w.stored(), null);
});

test('no stored session: signed out', async () => {
  const w = world();
  await w.deps.clear();
  assert.deepEqual(await createTokenSource(w.deps).accessToken(), { kind: 'signed_out' });
});

test('two concurrent callers share one refresh and stay signed in', async () => {
  const w = world({ expiresIn: 30 });
  const source = createTokenSource(w.deps);
  const [a, b] = await Promise.all([source.accessToken(), source.accessToken()]);
  assert.deepEqual(a, { kind: 'token', token: 'a2' });
  assert.deepEqual(b, { kind: 'token', token: 'a2' });
  assert.equal(w.stored()?.refresh_token, 'r2');
});

test('a caller that read the session before another refresh finished does not refresh it again', async () => {
  const w = world({ expiresIn: 30 });
  const source = createTokenSource(w.deps);
  const first = source.accessToken();
  await new Promise((r) => setTimeout(r, 5));  // the first refresh has landed
  const [a, b] = await Promise.all([first, source.accessToken()]);
  assert.deepEqual([a, b], [{ kind: 'token', token: 'a2' }, { kind: 'token', token: 'a2' }]);
  assert.equal(w.stored()?.refresh_token, 'r2');
});
