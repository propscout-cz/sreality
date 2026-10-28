/* `npm test` (node --test). One test per invariant in store.ts's header, against
 * a fake transport whose replies the test releases one by one. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createStore, isLiveDeal } from './store.ts';
import type { ApiMessage, ApiResult, PortalListing } from './types';
import type { PipelineStage } from '../../frontend/src/lib/types';

interface Out {
  message: ApiMessage;
  resolve: (res: ApiResult<unknown>) => void;
}

function harness() {
  const out: Out[] = [];
  const sent: ApiMessage['type'][] = [];
  let switchAccount: (user: string | null) => void = () => {};
  const store = createStore({
    send: (message) => new Promise((resolve) => { sent.push(message.type); out.push({ message, resolve }); }),
    sleep: async () => {},
    onAccountChange: (fn) => { switchAccount = fn; },
  });
  const notified: (readonly number[] | null)[] = [];
  store.subscribe((propertyIds) => { notified.push(propertyIds); });
  /* Release the oldest outstanding request of this type (for this property). */
  const reply = (type: ApiMessage['type'], res: ApiResult<unknown>, propertyId?: number): void => {
    const at = out.findIndex((o) => o.message.type === type
      && (propertyId == null || ('property_id' in o.message && o.message.property_id === propertyId)));
    assert.ok(at >= 0, `no ${type} outstanding (sent: ${sent.join(',')})`);
    out.splice(at, 1)[0].resolve(res);
  };
  const flush = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));
  /* A lookup answered at once with these rows. */
  const seed = async (rows: PortalListing[]): Promise<void> => {
    const answer = store.lookup([]);
    reply('lookup_listings', ok(rows));
    await answer;
  };
  return { store, out, sent, notified, reply, flush, seed, switchAccount: (u: string | null) => switchAccount(u) };
}

const ok = (data: unknown): ApiResult<unknown> => ({ ok: true, data });
const fail = (detail = 'boom'): ApiResult<unknown> => ({ ok: false, status: 500, detail });

const OUT = {
  in_pipeline: false, stage_id: null, stage_key: null, stage_label: null, stage_code: null, stage_color: null,
  entered_stage_at: null,
};
const IN = { ...OUT, in_pipeline: true, stage_id: 2, stage_key: 'v', stage_label: 'Prohlídka', stage_code: 'P' };

function row(propertyId: number, over: Partial<PortalListing> = {}): PortalListing {
  return {
    source: 'sreality', source_id: String(propertyId), found: true, property_id: propertyId,
    pipeline: OUT, collection_ids: [], tag_ids: [], dismissed: false, ...over,
  } as unknown as PortalListing;
}

const STAGES = [
  { id: 1, is_terminal: false }, { id: 2, is_terminal: false }, { id: 3, is_terminal: true },
] as PipelineStage[];

async function settled<T>(p: Promise<T>, flush: () => Promise<void>): Promise<T | 'never'> {
  return Promise.race([p, flush().then(() => flush()).then(() => 'never' as const)]);
}

test('1. one session generation: a reply across an account change is never delivered, auth replies are', async () => {
  const h = harness();
  const lookup = h.store.lookup([]);
  const auth = h.store.call({ type: 'get_auth_state' });
  // A sign-in's own session is the account change: its reply lands after it.
  const signIn = h.store.call({ type: 'sign_in' });
  const signOut = h.store.call({ type: 'sign_out' });
  h.switchAccount('user-b');
  h.reply('lookup_listings', ok([row(1)]));
  h.reply('get_auth_state', ok({ signedIn: true, email: null }));
  h.reply('sign_in', ok(undefined));
  h.reply('sign_out', ok(undefined));
  assert.equal(await settled(lookup, h.flush), 'never');
  assert.equal(h.store.curation(1), null);
  assert.deepEqual(await settled(auth, h.flush), ok({ signedIn: true, email: null }));
  assert.deepEqual(await settled(signIn, h.flush), ok(undefined));
  assert.deepEqual(await settled(signOut, h.flush), ok(undefined));
  // Asked after the change: delivered.
  const fresh = h.store.call({ type: 'lookup_listings', items: [] });
  h.reply('lookup_listings', ok([]));
  assert.deepEqual(await fresh, ok([]));
});

test('2. an account change clears every cache: curation, lists, writes in flight', async () => {
  const h = harness();
  await h.seed([row(1, { collection_ids: [11] })]);
  h.store.load('tags');
  h.reply('list_tags', ok([{ id: 21, name: 'Balkon', color: 'teal' }]));
  await h.flush();
  assert.equal(h.store.list('tags').status, 'ok');
  const write = h.store.toggleDismiss(1);
  assert.ok(write);
  h.store.load('collections');

  h.switchAccount(null);
  assert.equal(h.store.curation(1), null);
  assert.deepEqual(h.store.list('tags'), { data: null, status: 'loading' });
  assert.equal(h.store.busy('dismissed', 1), false);

  // The old account's answers land on nothing.
  h.reply('dismiss_property', ok({ property_id: 1, added: true }));
  h.reply('list_collections', ok([{ id: 11, name: 'A', monitoring_enabled: false, owner_email: null }]));
  assert.equal(await settled(write, h.flush), 'never');
  assert.equal(h.store.curation(1), null);
  assert.deepEqual(h.store.list('collections'), { data: null, status: 'loading' });
});

test('3. curation is keyed by property: a reply for A never paints B, a lookup never undoes a write', async () => {
  const h = harness();
  await h.seed([row(1), row(2, { collection_ids: [12] })]);
  const before = h.store.curation(2);
  h.notified.length = 0;
  const write = h.store.toggleCollection(1, 11);
  h.reply('add_to_collection', ok({ added: 1 }));
  assert.deepEqual(await write, { ok: true, on: true });
  assert.deepEqual(h.store.curation(1)?.collection_ids, [11]);
  assert.equal(h.store.curation(2), before);
  assert.deepEqual(h.notified, [[1], [1]]);

  // Sent before the add, answered after it settled: the row predates the add.
  const stale = h.store.lookup([]);
  const add = h.store.addToPipeline(2);
  h.reply('add_pipeline_card', ok({ stage_id: 1, stage_label: 'Nové' }));
  await add;
  h.reply('lookup_listings', ok([row(2, { collection_ids: [12] })]));
  await stale;
  assert.equal(h.store.curation(2)?.pipeline?.in_pipeline, true);
  // Sent after it: the server's answer wins.
  await h.seed([row(2, { collection_ids: [12, 14] })]);
  assert.deepEqual(h.store.curation(2)?.collection_ids, [12, 14]);
  assert.equal(h.store.curation(2)?.pipeline?.in_pipeline, false);
});

test('4. one write per property and kind at a time; another kind or property goes ahead', async () => {
  const h = harness();
  await h.seed([row(1), row(2)]);
  h.sent.length = 0;
  const first = h.store.addToPipeline(1);
  assert.ok(first);
  assert.equal(h.store.busy('pipeline', 1), true);
  assert.equal(h.store.addToPipeline(1), null);
  assert.equal(h.store.moveStage(1, 3), null);
  assert.ok(h.store.toggleDismiss(1));
  assert.ok(h.store.addToPipeline(2));
  assert.deepEqual(h.sent, ['add_pipeline_card', 'dismiss_property', 'add_pipeline_card']);
  h.reply('add_pipeline_card', ok({ stage_id: 1, stage_label: 'Nové' }));
  await first;
  assert.equal(h.store.busy('pipeline', 1), false);
  assert.equal(h.store.curation(1)?.pipeline?.stage_label, 'Nové');

  // A move restamps the stage date: unknown while it is out, the card's once it lands.
  const move = h.store.moveStage(1, 2);
  assert.equal(h.store.curation(1)?.pipeline?.entered_stage_at, null);
  h.reply('move_pipeline_card', ok({ stage_id: 2, entered_stage_at: '2026-10-28T09:00:00Z' }));
  await move;
  assert.equal(h.store.curation(1)?.pipeline?.entered_stage_at, '2026-10-28T09:00:00Z');

  // A new card is live, and a live deal lifts the dismissal.
  await h.seed([row(3, { dismissed: true })]);
  const third = h.store.addToPipeline(3);
  h.reply('add_pipeline_card', ok({ stage_id: 1 }), 3);
  await third;
  assert.equal(h.store.curation(3)?.dismissed, false);
});

test('5. a failed optimistic write reverts exactly once; a toggle undoes only its step', async () => {
  const h = harness();
  await h.seed([row(1, { pipeline: IN, collection_ids: [11], dismissed: false })]);
  const prior = h.store.curation(1)?.pipeline;

  const remove = h.store.removeFromPipeline(1);
  assert.equal(h.store.curation(1)?.pipeline?.in_pipeline, false);
  h.reply('remove_pipeline_card', fail());
  assert.deepEqual(await remove, { ok: false, on: false, detail: 'boom' });
  assert.equal(h.store.curation(1)?.pipeline, prior);

  h.notified.length = 0;
  const add = h.store.toggleCollection(1, 13);
  assert.deepEqual(h.store.curation(1)?.collection_ids, [11, 13]);
  // A fresh lookup lands meanwhile, carrying a collection added elsewhere.
  await h.seed([row(1, { pipeline: IN, collection_ids: [11, 12, 13], dismissed: false })]);
  h.reply('add_to_collection', fail());
  await add;
  assert.deepEqual(h.store.curation(1)?.collection_ids, [11, 12]);
  assert.deepEqual(h.notified, [[1], [1], [1]]);  // optimistic, the lookup, ONE revert
  // The failure re-reads the list (a collection deleted in the app is the likeliest cause).
  assert.equal(h.sent.at(-1), 'list_collections');
});

test('6. one status per list: failed is not empty, a failed refresh keeps the list, an unchanged reload is silent', async () => {
  const h = harness();
  assert.equal(h.store.load('collections'), true);
  for (let i = 0; i < 3; i++) {
    h.reply('list_collections', fail());
    await h.flush();
  }
  assert.deepEqual(h.store.list('collections'), { data: null, status: 'failed' });
  // A list read that failed is not retried on its own...
  h.store.ensure('collections');
  assert.equal(h.out.length, 0);
  // ...only when asked, and it reads "loading" again meanwhile.
  assert.equal(h.store.load('collections'), true);
  assert.equal(h.store.list('collections').status, 'loading');
  const list = [{ id: 11, name: 'A', monitoring_enabled: false, owner_email: null, updated_at: '1' }];
  h.reply('list_collections', ok(list));
  await h.flush();
  assert.equal(h.store.list('collections').status, 'ok');

  assert.equal(h.store.load('collections'), false);  // held: read only on refresh
  h.notified.length = 0;
  assert.equal(h.store.load('collections', true), true);
  assert.equal(h.store.load('collections', true), false);  // one read in flight per list
  assert.equal(h.out.length, 1);
  h.reply('list_collections', ok([{ ...list[0], updated_at: '2' }]));  // nothing drawn changed
  await h.flush();
  assert.deepEqual(h.notified, []);

  h.store.load('collections', true);
  for (let i = 0; i < 3; i++) {
    h.reply('list_collections', fail());
    await h.flush();
  }
  assert.equal(h.store.list('collections').status, 'ok');
  assert.equal(h.store.list('collections').data?.length, 1);
});

test('7. one live-deal rule', () => {
  assert.equal(isLiveDeal(null, STAGES), false);
  assert.equal(isLiveDeal(OUT, STAGES), false);
  assert.equal(isLiveDeal({ ...IN, stage_id: 2 }, STAGES), true);
  assert.equal(isLiveDeal({ ...IN, stage_id: 3 }, STAGES), false);
  // Until the stage list has loaded, an in-pipeline card counts as live.
  assert.equal(isLiveDeal({ ...IN, stage_id: 3 }, null), true);
});

test('a lookup batch notifies once; an unchanged row nobody', async () => {
  const h = harness();
  await h.seed([row(1), row(2, { dismissed: true })]);
  assert.deepEqual(h.notified, [[1, 2]]);
  h.notified.length = 0;
  await h.seed([row(1), row(2, { dismissed: true })]);
  assert.deepEqual(h.notified, []);
  // Rows we do not hold, or with no property yet, carry no curation.
  await h.seed([row(3, { found: false }), row(4, { property_id: null })]);
  assert.equal(h.store.curation(3), null);
  assert.equal(h.store.curation(4), null);
  assert.deepEqual(h.notified, []);
});
