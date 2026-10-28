/* The extension's ONE data layer, shared by the listing panel (panel.ts) and the
 * search-page cards (index_cards.ts): the transport and its session generation,
 * each property's curation, the lists that name it, and the one write path.
 * Surfaces subscribe and re-render from it. No DOM, no `chrome`, no runtime import
 * (node --test loads this file as it is): deps are injected (like ingest_flow.ts's),
 * so store.test.ts runs it under node.
 *
 * Invariants (store.test.ts proves each; the focus one, dom.test.ts):
 *  1. One session generation: a reply to a call sent before an account change is
 *     never delivered; get_auth_state / sign_in / sign_out always are.
 *  2. An account change clears every cache: curation, lists, writes in flight.
 *  3. Curation is keyed by property: a reply for listing A never paints listing B,
 *     and a lookup sent before a write of a property never undoes that write.
 *  4. One write per property and kind at a time; a second is refused, not queued.
 *  5. A failed optimistic write reverts exactly once — a toggle by undoing its one
 *     step, so a change that landed meanwhile survives.
 *  6. One status per list: failed is never drawn as empty, a failed refresh keeps
 *     the list, an unchanged reload notifies nobody (a rebuild under the pointer
 *     swallows the click being made).
 *  7. One live-deal rule for every surface (isLiveDeal).
 *  8. Focus is never stolen from the portal page (dom.ts keepFocus). */

import type {
  ApiMessage, ApiResult, PipelineCardResult, PipelineMembership, PortalListing, PortalLookupItem,
} from './types';
import type { Collection, PipelineStage, Tag } from '../../frontend/src/lib/types';

export type Curation = Pick<PortalListing, 'pipeline' | 'collection_ids' | 'tag_ids' | 'dismissed'>;
export interface Lists {
  stages: PipelineStage[];
  collections: Collection[];
  tags: Tag[];
}
export type ListKind = keyof Lists;
/* `data` is set exactly when ok; "loading" also covers "not read yet" (both draw "Načítám…"). */
export interface ListView<T> {
  data: T | null;
  status: 'loading' | 'ok' | 'failed';
}
export type WriteKind = 'pipeline' | 'dismissed' | 'collections' | 'tags';
/* `on`: what the write set — in the pipeline, dismissed, a member. */
export type WriteResult = { ok: true; on: boolean } | { ok: false; on: boolean; detail: string };
/* null: refused at once (one of its kind is in flight, or nothing to change). */
export type Pending = Promise<WriteResult> | null;
export type Store = ReturnType<typeof createStore>;

export interface StoreDeps {
  /* The raw transport; always resolves (content.ts wraps chrome.runtime.sendMessage). */
  send: (message: ApiMessage) => Promise<ApiResult<unknown>>;
  sleep: (ms: number) => Promise<void>;
  /* The stored session's account changes (session.ts onAccountChange). */
  onAccountChange: (fn: (user: string | null) => void) => unknown;
}

export const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
/* The back-off between a read's three tries. */
const RETRY_MS = 1500;

/* A LIVE deal and a dismissal never coexist: the API refuses the dismissal, and a
 * write that leaves a card live lifts it. Liveness is read off the stage list, the
 * one place `is_terminal` lives; until it has loaded an in-pipeline card counts as
 * live — the conservative answer, and the server's own for a live deal. */
export function isLiveDeal(
  pipeline: PipelineMembership | null | undefined, stages: readonly PipelineStage[] | null,
): boolean {
  if (!pipeline?.in_pipeline) return false;
  const stage = stages?.find((s) => s.id === pipeline.stage_id);
  return stage == null || !stage.is_terminal;
}

const OUT: PipelineMembership = {
  in_pipeline: false, stage_id: null, stage_key: null, stage_label: null, stage_code: null, stage_color: null,
  entered_stage_at: null,
};
const ALWAYS_ANSWERED = new Set<ApiMessage['type']>(['get_auth_state', 'sign_in', 'sign_out']);
const READ: Record<ListKind, ApiMessage> = {
  stages: { type: 'list_pipeline_stages' },
  collections: { type: 'list_collections' },
  tags: { type: 'list_tags' },
};
/* What surfaces draw from a list: a reload that keeps this keeps the screen. Rows
 * in id order — the server sorts collections by updated_at, which every add/remove
 * bumps; owner and recipients count (what is saved, who a shared note reaches). */
const DRAWN: Record<ListKind, (data: never) => string> = {
  stages: (data: PipelineStage[]) => JSON.stringify(data),
  collections: (data: Collection[]) => JSON.stringify([...data].sort((a, b) => a.id - b.id).map((c) => [
    c.id, c.name, c.monitoring_enabled, c.owner_email, (c.shared_with ?? []).map((r) => r.email),
  ])),
  tags: (data: Tag[]) => JSON.stringify(data.map((t) => [t.id, t.name, t.color])),
};

const fromCard = (card: PipelineCardResult, base: PipelineMembership): PipelineMembership => ({
  in_pipeline: true,
  stage_id: card.stage_id ?? base.stage_id,
  stage_key: card.stage_key ?? base.stage_key,
  stage_label: card.stage_label ?? base.stage_label,
  stage_code: card.stage_code ?? base.stage_code,
  stage_color: card.stage_color ?? base.stage_color,
  entered_stage_at: card.entered_stage_at ?? base.entered_stage_at,
});
// A live deal lifts the caller's dismissal server-side; mirror it.
const lift = (c: Curation): Curation => ({ ...c, dismissed: c.dismissed == null ? c.dismissed : false });

/* A toggle is ONE step applied to whatever is held then — never a whole list
 * captured at click time, which would drop what a fresh lookup brought since. */
function step(field: 'collection_ids' | 'tag_ids', id: number, member: boolean) {
  return (c: Curation): Curation => {
    const held = c[field] ?? [];
    if (held.includes(id) === member) return { ...c, [field]: held };
    return { ...c, [field]: member ? [...held, id] : held.filter((x) => x !== id) };
  };
}

export function createStore(deps: StoreDeps) {
  let session = 0;
  const curations = new Map<number, Curation>();
  /* Bumped by every step of a write; `written`: each property's last bump. */
  let clock = 0;
  const written = new Map<number, number>();
  const blank = (): Record<ListKind, ListView<unknown>> => ({
    stages: { data: null, status: 'loading' },
    collections: { data: null, status: 'loading' },
    tags: { data: null, status: 'loading' },
  });
  const lists = blank();
  const reading = new Set<ListKind>();
  const writing = new Set<string>();
  /* Whose curation changed; null when a list did. */
  const subscribers = new Set<(propertyIds: readonly number[] | null) => void>();
  const accountListeners = new Set<(user: string | null) => void>();
  const notify = (propertyIds: readonly number[] | null): void => {
    for (const fn of [...subscribers]) fn(propertyIds);
  };
  const listen = <F>(set: Set<F>, fn: F): (() => void) => {
    set.add(fn);
    return () => { set.delete(fn); };
  };

  deps.onAccountChange((user) => {
    session++;
    curations.clear();
    written.clear();
    Object.assign(lists, blank());
    reading.clear();
    writing.clear();
    for (const fn of [...accountListeners]) fn(user);
  });

  function call<T>(message: ApiMessage): Promise<ApiResult<T>> {
    const asked = session;
    return new Promise((resolve) => {
      void deps.send(message).then((res) => {
        if (asked === session || ALWAYS_ANSWERED.has(message.type)) resolve(res as ApiResult<T>);
      });
    });
  }

  /* A read tried three times, RETRY_MS apart: one network blip must not cost the
   * page view a list. Null: the account changed between tries. */
  async function retry<T>(read: () => Promise<ApiResult<T>>): Promise<ApiResult<T> | null> {
    const asked = session;
    let res = await read();
    for (let attempt = 1; attempt < 3 && !res.ok; attempt++) {
      await deps.sleep(RETRY_MS);
      if (asked !== session) return null;
      res = await read();
    }
    return res;
  }

  /* Read a list — a held one only on `refresh`, a failed one again. False: nothing
   * was sent (a read of it is out, or it is held). */
  function load(kind: ListKind, refresh = false): boolean {
    if (reading.has(kind) || (lists[kind].data != null && !refresh)) return false;
    reading.add(kind);
    if (lists[kind].status === 'failed') {
      lists[kind] = { data: null, status: 'loading' };
      notify(null);
    }
    void retry(() => call<never>(READ[kind])).then((res) => {
      if (res == null) return;
      reading.delete(kind);
      if (res.ok) {
        const held = lists[kind].data as never;
        lists[kind] = { data: res.data, status: 'ok' };
        if (held == null || DRAWN[kind](held) !== DRAWN[kind](res.data)) notify(null);
      } else if (lists[kind].data == null) {
        lists[kind] = { data: null, status: 'failed' };
        notify(null);
      }
    });
    return true;
  }

  /* Lookup rows → curation, one notification for the batch. A property written
   * since `sentAt` keeps what the write left: the row predates it. */
  function seed(rows: readonly PortalListing[], sentAt: number): void {
    const changed: number[] = [];
    for (const row of rows) {
      if (!row.found || row.property_id == null || (written.get(row.property_id) ?? -1) > sentAt) continue;
      const held = curations.get(row.property_id);
      const next: Curation = {
        pipeline: row.pipeline, collection_ids: row.collection_ids, tag_ids: row.tag_ids, dismissed: row.dismissed,
      };
      if (held != null && JSON.stringify(held) === JSON.stringify(next)) continue;
      curations.set(row.property_id, next);
      changed.push(row.property_id);
    }
    if (changed.length > 0) notify(changed);
  }

  /* The one write path: optimistic, then the answer settles it or `revert` puts
   * the prior state back, once. A reply across an account change never lands
   * (call), and the clear took the optimistic state with everything else. */
  function write(
    kind: WriteKind, propertyId: number, on: boolean, message: ApiMessage,
    optimistic: (c: Curation) => Curation, settle: (c: Curation, data: unknown) => Curation,
    revert: (c: Curation) => Curation, onFailure?: () => void,
  ): Pending {
    const key = `${kind}:${propertyId}`;
    if (writing.has(key) || !curations.has(propertyId)) return null;
    const apply = (fn: (c: Curation) => Curation): void => {
      const c = curations.get(propertyId);
      if (c != null) curations.set(propertyId, fn(c));
      written.set(propertyId, ++clock);
      notify([propertyId]);
    };
    writing.add(key);
    apply(optimistic);
    return call<unknown>(message).then((res) => {
      writing.delete(key);
      if (res.ok) {
        apply((c) => settle(c, res.data));
        return { ok: true, on };
      }
      apply(revert);
      onFailure?.();
      return { ok: false, on, detail: res.detail };
    });
  }

  function pipelineWrite(
    propertyId: number, on: boolean, message: ApiMessage, optimistic: PipelineMembership,
    settle: (c: Curation, card: PipelineCardResult) => Curation,
  ): Pending {
    const prior = curations.get(propertyId)?.pipeline ?? null;
    return write('pipeline', propertyId, on, message, (c) => ({ ...c, pipeline: optimistic }),
      (c, card) => settle(c, card as PipelineCardResult), (c) => ({ ...c, pipeline: prior }));
  }

  function toggle(kind: 'collections' | 'tags', propertyId: number, id: number): Pending {
    const field = kind === 'collections' ? 'collection_ids' : 'tag_ids';
    const add = !(curations.get(propertyId)?.[field] ?? []).includes(id);
    const message: ApiMessage = kind === 'collections'
      ? { type: add ? 'add_to_collection' : 'remove_from_collection', collection_id: id, property_id: propertyId }
      : { type: add ? 'attach_tag' : 'detach_tag', tag_id: id, property_id: propertyId };
    // The likeliest failure is one deleted in the app since the list loaded: re-read it.
    return write(kind, propertyId, add, message, step(field, id, add), step(field, id, add),
      step(field, id, !add), () => { load(kind, true); });
  }

  return {
    call,
    retry,
    /* After the store dropped what the old account read. */
    onAccountChange: (fn: (user: string | null) => void) => listen(accountListeners, fn),
    subscribe: (fn: (propertyIds: readonly number[] | null) => void) => listen(subscribers, fn),
    /* The one lookup: its rows become curation when it answers. */
    lookup(items: PortalLookupItem[]): Promise<ApiResult<PortalListing[]>> {
      const sentAt = clock;
      return call<PortalListing[]>({ type: 'lookup_listings', items }).then((res) => {
        if (res.ok) seed(res.data, sentAt);
        return res;
      });
    },
    curation: (propertyId: number): Curation | null => curations.get(propertyId) ?? null,
    list: <K extends ListKind>(kind: K) => lists[kind] as ListView<Lists[K]>,
    load,
    /* Read a list never read: neither a held one nor one that failed. */
    ensure(kind: ListKind): void {
      if (lists[kind].status === 'loading' && !reading.has(kind)) load(kind);
    },
    busy: (kind: WriteKind, propertyId: number): boolean => writing.has(`${kind}:${propertyId}`),
    addToPipeline: (propertyId: number): Pending => pipelineWrite(
      propertyId, true, { type: 'add_pipeline_card', property_id: propertyId }, { ...OUT, in_pipeline: true },
      // A new card sits at the entry stage, which can't be terminal: it is live.
      (c, card) => lift({ ...c, pipeline: fromCard(card, OUT) }),
    ),
    removeFromPipeline(propertyId: number): Pending {
      if (!curations.get(propertyId)?.pipeline?.in_pipeline) return null;
      return pipelineWrite(propertyId, false, { type: 'remove_pipeline_card', property_id: propertyId }, OUT,
        (c) => ({ ...c, pipeline: OUT }));
    },
    moveStage(propertyId: number, stageId: number): Pending {
      const prior = curations.get(propertyId)?.pipeline;
      if (prior == null || prior.stage_id === stageId) return null;
      const target = (lists.stages.data as PipelineStage[] | null)?.find((s) => s.id === stageId) ?? null;
      const moved: PipelineMembership = {
        in_pipeline: true, stage_id: stageId,
        stage_key: target?.key ?? prior.stage_key,
        stage_label: target?.label ?? prior.stage_label,
        stage_code: target?.code ?? null,
        stage_color: target?.color ?? prior.stage_color,
        // A move restamps it: unknown until the card comes back.
        entered_stage_at: null,
      };
      // Re-opening a closed deal makes it live, which lifts its dismissal.
      const reopens = target != null && !target.is_terminal;
      return pipelineWrite(propertyId, true, { type: 'move_pipeline_card', property_id: propertyId, stage_id: stageId },
        moved, (c, card) => {
          const next = { ...c, pipeline: fromCard(card, moved) };
          return reopens ? lift(next) : next;
        });
    },
    toggleDismiss(propertyId: number): Pending {
      const wasOn = curations.get(propertyId)?.dismissed;
      if (wasOn == null) return null;
      const set = (dismissed: boolean) => (c: Curation): Curation => ({ ...c, dismissed });
      return write('dismissed', propertyId, !wasOn,
        { type: wasOn ? 'undismiss_property' : 'dismiss_property', property_id: propertyId },
        set(!wasOn), set(!wasOn), set(wasOn));
    },
    toggleCollection: (propertyId: number, collectionId: number): Pending =>
      toggle('collections', propertyId, collectionId),
    toggleTag: (propertyId: number, tagId: number): Pending => toggle('tags', propertyId, tagId),
  };
}
