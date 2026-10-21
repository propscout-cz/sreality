/* `npm test` (node --test; Node >= 22.18 strips the types). */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isAdminGate, outcomeMessage, runIngest, type IngestDeps, type IngestStep } from './ingest_flow.ts';
import type { ApiMessage, ApiResult, IngestState } from './types';

const URL_ = 'https://reality.idnes.cz/detail/prodej/byt/praha/0123456789abcdef01234567/#foto';
const SOURCE = 'idnes';
const NATIVE = '0123456789abcdef01234567';

function reply(patch: Partial<IngestState>): ApiResult<IngestState> {
  return {
    ok: true,
    data: {
      state: 'queued', message_cs: null, source: SOURCE, source_id_native: NATIVE,
      listing_id: null, is_active: null, property_id: null, queue: null, ...patch,
    },
  };
}

function harness(replies: ApiResult<IngestState>[], opts: { maxPolls?: number; aliveFor?: number } = {}) {
  const sent: ApiMessage[] = [];
  const steps: IngestStep[] = [];
  const sleeps: number[] = [];
  let aliveChecks = 0;
  const deps: IngestDeps = {
    call: async (message) => {
      sent.push(message);
      const next = replies.shift();
      assert.ok(next, `unexpected request ${JSON.stringify(message)}`);
      return next;
    },
    sleep: async (ms) => { sleeps.push(ms); },
    alive: () => opts.aliveFor == null || aliveChecks++ < opts.aliveFor,
    onStep: (step) => { steps.push(step); },
    intervalMs: 2000,
    maxPolls: opts.maxPolls ?? 180,
  };
  return { deps, sent, steps, sleeps };
}

test('a held ad with a property ends at once on the POST', async () => {
  const h = harness([reply({ state: 'held', listing_id: 7, is_active: true, property_id: 42 })]);
  const outcome = await runIngest(URL_, h.deps);
  assert.deepEqual(outcome, { kind: 'held', source: SOURCE, nativeId: NATIVE, propertyId: 42 });
  assert.deepEqual(h.sent, [{ type: 'ingest_listing', url: URL_ }]);
  assert.deepEqual(h.steps, ['resolving']);
  assert.deepEqual(h.sleeps, []);
});

test('an inactive held ad with a property is held too (the drain re-checks it behind)', async () => {
  const h = harness([reply({ state: 'held', listing_id: 7, is_active: false, property_id: 42 })]);
  assert.equal((await runIngest(URL_, h.deps)).kind, 'held');
});

test('queued → fetched → born: polls the GET by the identity the server resolved', async () => {
  const h = harness([
    reply({ state: 'queued', queue: { attempts: 0, claimed_at: null, given_up: false } }),
    reply({ state: 'queued', queue: { attempts: 0, claimed_at: '2026-10-20T12:00:00Z', given_up: false } }),
    reply({ state: 'held', listing_id: 7, is_active: true, property_id: null }),
    reply({ state: 'held', listing_id: 7, is_active: true, property_id: 42 }),
  ]);
  const outcome = await runIngest(URL_, h.deps);
  assert.deepEqual(outcome, { kind: 'held', source: SOURCE, nativeId: NATIVE, propertyId: 42 });
  const poll = { type: 'ingest_state', source: SOURCE, native_id: NATIVE };
  assert.deepEqual(h.sent, [{ type: 'ingest_listing', url: URL_ }, poll, poll, poll]);
  assert.deepEqual(h.steps, ['resolving', 'fetching', 'fetching', 'attaching']);
  assert.deepEqual(h.sleeps, [2000, 2000, 2000]);
});

for (const state of ['refused', 'gone', 'failed'] as const) {
  test(`${state} ends on its one sentence`, async () => {
    const message = `věta pro ${state}`;
    const h = harness([
      reply({ state: 'queued' }),
      reply({ state, message_cs: message, source: state === 'refused' ? null : SOURCE }),
    ]);
    assert.deepEqual(await runIngest(URL_, h.deps), { kind: 'refused', message });
    assert.equal(h.sent.length, 2);
  });
}

test('a refusal on the POST sends nothing more', async () => {
  const h = harness([reply({ state: 'refused', message_cs: 'Tuto stránku zatím nečteme.', source: null, source_id_native: null })]);
  assert.deepEqual(await runIngest(URL_, h.deps), { kind: 'refused', message: 'Tuto stránku zatím nečteme.' });
  assert.equal(h.sent.length, 1);
});

test('an API error on the POST or mid-poll stops the flow with its status', async () => {
  const onPost = harness([{ ok: false, status: 403, detail: 'Admin only' }]);
  assert.deepEqual(await runIngest(URL_, onPost.deps), { kind: 'error', status: 403, detail: 'Admin only' });
  const midPoll = harness([reply({ state: 'queued' }), { ok: false, status: 0, detail: 'network error' }]);
  assert.deepEqual(await runIngest(URL_, midPoll.deps), { kind: 'error', status: 0, detail: 'network error' });
  assert.equal(midPoll.sent.length, 2);
});

test('a waiting state without the ad identity is an error, not a poll of nothing', async () => {
  const h = harness([reply({ state: 'queued', source: null })]);
  assert.equal((await runIngest(URL_, h.deps)).kind, 'error');
  assert.equal(h.sent.length, 1);
});

test('the poll budget bounds the GETs, then the flow times out', async () => {
  const h = harness([reply({}), reply({}), reply({})], { maxPolls: 2 });
  assert.deepEqual(await runIngest(URL_, h.deps), { kind: 'timeout' });
  assert.equal(h.sent.filter((m) => m.type === 'ingest_state').length, 2);
});

test('leaving the panel stops the poll before the next GET', async () => {
  const afterPost = harness([reply({})], { aliveFor: 0 });
  assert.deepEqual(await runIngest(URL_, afterPost.deps), { kind: 'cancelled' });
  assert.equal(afterPost.sent.length, 1);
  const afterSleep = harness([reply({})], { aliveFor: 1 });
  assert.deepEqual(await runIngest(URL_, afterSleep.deps), { kind: 'cancelled' });
  assert.deepEqual(afterSleep.sent.map((m) => m.type), ['ingest_listing']);
});

test('outcomeMessage: the server sentence, the admin gate in Czech, a friendly detail, the timeout', () => {
  const friendly = (d: string): string => (d === 'not_signed_in' ? 'nejste přihlášeni' : d);
  assert.equal(outcomeMessage({ kind: 'refused', message: 'Inzerát na portálu už není.' }, friendly),
    'Inzerát na portálu už není.');
  assert.equal(outcomeMessage({ kind: 'error', status: 403, detail: 'Admin only' }, friendly),
    'Přidávat inzeráty mohou zatím jen správci.');
  assert.equal(outcomeMessage({ kind: 'error', status: 401, detail: 'not_signed_in' }, friendly),
    'Přidání se nepodařilo: nejste přihlášeni');
  assert.match(outcomeMessage({ kind: 'timeout' }, friendly), /zkuste to za chvíli znovu/);
});

test('isAdminGate: only a 403 hides the button; a refusal, another error or a timeout keep it', () => {
  assert.equal(isAdminGate({ kind: 'error', status: 403, detail: 'Admin only' }), true);
  assert.equal(isAdminGate({ kind: 'error', status: 401, detail: 'not_signed_in' }), false);
  assert.equal(isAdminGate({ kind: 'refused', message: 'Tuto stránku zatím nečteme.' }), false);
  assert.equal(isAdminGate({ kind: 'timeout' }), false);
});
