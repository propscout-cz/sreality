/* Background-worker-side fetch wrapper. Only this file talks to the
 * FastAPI service directly; the content script proxies via runtime
 * messages, so the call leaves from the extension's own origin, which the
 * API's CORS allowlist names (the manifest grants no host permission). */

import { getAccessToken } from './auth';
import { API_BASE_URL, API_NOT_CONFIGURED_DETAIL, NOT_SIGNED_IN_DETAIL } from './config';
import type {
  ApiResult,
  BillingMe,
  CollectionWriteResult,
  DismissalWriteResult,
  EstimationRun,
  IngestState,
  NotePatch,
  PipelineCardResult,
  PortalListing,
  PortalLookupItem,
  PriceHistory,
  TagWriteResult,
  YieldScenarioUpdate,
} from './types';
import type { Collection, Note, PipelineStage, Tag } from '../../frontend/src/lib/types';

if (!API_BASE_URL) {
  console.warn(
    '[sreality-ext] VITE_API_BASE_URL is empty — extension cannot ' +
    'reach the FastAPI service. Set it in .env and rebuild.',
  );
}

/* Same transport deadline the SPA uses (frontend/src/lib/api.ts,
 * REQUEST_DEADLINE_MS) — duplicated by value because the territories share no
 * code. A hung request fails here with an honest detail instead of holding the
 * panel open forever. index_overlay's LOOKUP_TIMEOUT_MS stays on top of this:
 * it guards the chrome.runtime.sendMessage round trip, which can also be lost
 * to MV3 service-worker teardown — a failure this fetch deadline cannot see. */
const REQUEST_DEADLINE_MS = 130_000;

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiResult<T>> {
  if (!API_BASE_URL) {
    return { ok: false, status: 0, detail: API_NOT_CONFIGURED_DETAIL };
  }
  const access = await getAccessToken();
  if (access.kind === 'signed_out') {
    return { ok: false, status: 401, detail: NOT_SIGNED_IN_DETAIL };
  }
  // The refresh got no answer: still signed in, so a plain retryable failure, never "sign in".
  if (access.kind === 'retry') return { ok: false, status: 0, detail: access.detail };
  const token = access.token;
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...((init.headers as Record<string, string> | undefined) ?? {}),
  };
  if (init.body !== undefined) headers['Content-Type'] = 'application/json';
  headers.Authorization = `Bearer ${token}`;

  let res: Response;
  let text: string;
  try {
    res = await fetch(API_BASE_URL + path, {
      ...init,
      headers,
      signal: AbortSignal.timeout(REQUEST_DEADLINE_MS),
    });
    text = await res.text();
  } catch (err) {
    const timedOut = err instanceof DOMException && err.name === 'TimeoutError';
    return {
      ok: false,
      status: 0,
      detail: timedOut
        ? `Server neodpověděl do ${Math.round(REQUEST_DEADLINE_MS / 1000)} s`
        : err instanceof Error ? err.message : 'network error',
    };
  }

  let body: unknown = null;
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }

  if (!res.ok) {
    const raw =
      body && typeof body === 'object' && body !== null && 'detail' in body
        ? (body as { detail: unknown }).detail
        : null;
    /* The API's structured details ({code, message} — e.g. db_busy) render
     * their message; String() on the object would show "[object Object]". */
    const message =
      raw && typeof raw === 'object' && !Array.isArray(raw)
        ? (raw as { message?: unknown }).message
        : null;
    const detail =
      typeof message === 'string'
        ? message
        : raw != null
          ? String(raw)
          : res.statusText || `HTTP ${res.status}`;
    return { ok: false, status: res.status, detail };
  }

  return { ok: true, data: body as T };
}

/* The read routes answer `{data: [...]}`: unwrap to the array, once for all. */
async function requestData<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  const res = await request<{ data: T }>(path, init);
  return res.ok ? { ok: true, data: res.data.data } : res;
}

/* POST /listings/lookup — batch (source, native id) → our scraped facts +
 * precomputed MF reference rent / "Výnos MF" yield + latest estimate. One
 * request resolves every visible card on an index page; the detail panel
 * sends a single item. Returns one entry per requested item, in order. */
export async function lookupListings(
  items: PortalLookupItem[],
): Promise<ApiResult<PortalListing[]>> {
  if (items.length === 0) return { ok: true, data: [] };
  return requestData<PortalListing[]>('/listings/lookup', {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
}

/* POST /listings/ingest — ask for an ad we do not hold: the API resolves the
 * URL, enqueues its page for the worker's detail drain and answers one state
 * (admins only in v1). Idempotent server-side. */
export async function ingestListing(url: string): Promise<ApiResult<IngestState>> {
  return request<IngestState>('/listings/ingest', {
    method: 'POST',
    body: JSON.stringify({ url }),
  });
}

/* GET /listings/ingest/:source/:native_id — the same state, polled until the
 * ad is held with a property. */
export async function ingestState(
  source: string,
  nativeId: string,
): Promise<ApiResult<IngestState>> {
  return request<IngestState>(
    `/listings/ingest/${encodeURIComponent(source)}/${encodeURIComponent(nativeId)}`,
  );
}

/* PATCH /estimations/:id/scenario — write the operator's yield
 * overrides. A body with all fields null clears the column. */
export async function patchScenario(
  run_id: number,
  body: YieldScenarioUpdate,
): Promise<ApiResult<EstimationRun>> {
  return request<EstimationRun>(`/estimations/${run_id}/scenario`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

/* POST /estimations — kicks off a new estimation of the ad the operator is
 * viewing, by its `listing_id` from the lookup: an estimation is always of an ad
 * we hold (an ad we lack is added first, ingestListing). We surface the
 * resulting row id and the panel polls until it lands.
 *
 * The subject's CATEGORY decides what may be estimated. This used to send a
 * flat `estimate_kind:'rent'` for everything and nothing else, which — because
 * the server's `category_main` defaults to 'byt' and `category_type` follows
 * the kind — fired an APARTMENTS-FOR-RENT comparable cohort at houses,
 * commercial units and plots alike, i.e. a monthly per-m² table read against a
 * subject whose per-m² measure is capital. Both knobs are now passed through
 * from the caller; omitting either keeps the server's historical default. */
export async function createEstimation(
  listing_id: number,
  opts: { estimate_kind?: 'rent' | 'sale'; category_main?: string | null } = {},
): Promise<ApiResult<EstimationRun>> {
  const body: Record<string, unknown> = {
    listing_id,
    source: 'extension',
    estimate_kind: opts.estimate_kind ?? 'rent',
    // mode:'agent' is the metered path (Wave 1) — this is what the "(zbývá X)"
    // quota counts. The server enforces the monthly quota atomically and 429s
    // when it's exhausted; the panel surfaces that as an upgrade prompt.
    mode: 'agent',
  };
  // Only when the ad says: a null would PIN "every category" (a cohort with no
  // single per-m² basis). Absent = the server's 'byt' default.
  if (opts.category_main != null) body.category_main = opts.category_main;
  return request<EstimationRun>('/estimations', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/* GET /billing/me — the caller's plan + agent-estimation allowance. The panel
 * reads `agent_estimations` for its "(zbývá X)" counter and upgrade prompt. */
export async function getBillingMe(): Promise<ApiResult<BillingMe>> {
  return request<BillingMe>('/billing/me');
}

/* GET /estimations/:id — used by the polling loop after we trigger a
 * new estimation. Returns when the row reaches a terminal status. */
export async function getEstimation(
  run_id: number,
): Promise<ApiResult<EstimationRun>> {
  return request<EstimationRun>(`/estimations/${run_id}`);
}

/* GET /properties/:id/price-history — every ad of the property and its price
 * snapshots, for the panel's "Vývoj ceny" chart. */
export function getPriceHistory(property_id: number): Promise<ApiResult<PriceHistory>> {
  return requestData(`/properties/${property_id}/price-history`);
}

/* POST /dismissals / DELETE /dismissals/:property_id — hide the property from
 * discovery and undo it (migration 536). The SAME bearer-gated endpoints the
 * SPA's DismissButton writes through. */
export async function dismissProperty(
  property_id: number,
): Promise<ApiResult<DismissalWriteResult>> {
  return request<DismissalWriteResult>('/dismissals', {
    method: 'POST',
    body: JSON.stringify({ property_id }),
  });
}

export async function undismissProperty(
  property_id: number,
): Promise<ApiResult<DismissalWriteResult>> {
  return request<DismissalWriteResult>(`/dismissals/${property_id}`, {
    method: 'DELETE',
  });
}

/* POST /pipeline/cards — bookmark a property into the deal pipeline (rule #22),
 * landing it at the entry stage. The SAME bearer-gated endpoint the SPA's
 * BookmarkButton / PipelineToggle use — one write path, idempotent server-side
 * (ON CONFLICT DO NOTHING). Returns the card incl. its entry-stage label. */
export async function addPipelineCard(
  property_id: number,
): Promise<ApiResult<PipelineCardResult>> {
  return request<PipelineCardResult>('/pipeline/cards', {
    method: 'POST',
    body: JSON.stringify({ property_id }),
  });
}

/* DELETE /pipeline/cards/:property_id — un-bookmark (drop the card, ledger-logged). */
export async function removePipelineCard(
  property_id: number,
): Promise<ApiResult<PipelineCardResult>> {
  return request<PipelineCardResult>(`/pipeline/cards/${property_id}`, {
    method: 'DELETE',
  });
}

/* PATCH /pipeline/cards/:property_id — change the deal stage. The SAME audited
 * write the SPA's PipelineToggle + the kanban use: it stamps `entered_stage_at`
 * and logs a `moved` event to property_pipeline_events. Returns the moved card. */
export async function movePipelineCard(
  property_id: number,
  stage_id: number,
): Promise<ApiResult<PipelineCardResult>> {
  return request<PipelineCardResult>(`/pipeline/cards/${property_id}`, {
    method: 'PATCH',
    body: JSON.stringify({ stage_id }),
  });
}

/* GET /pipeline/stages — the operator-curated stage list, to populate the stage
 * `<select>`. */
export function listPipelineStages(): Promise<ApiResult<PipelineStage[]>> {
  return requestData('/pipeline/stages');
}

/* GET /collections — the caller's collections (rule #18), for the panel's
 * save-to-collection checklist. */
export function listCollections(): Promise<ApiResult<Collection[]>> {
  return requestData('/collections');
}

/* GET /tags — the caller's tags (rule #18), names + colours for the search-page
 * card chips, ordered by name. */
export function listTags(): Promise<ApiResult<Tag[]>> {
  return requestData('/tags');
}

/* POST /properties/:id/tags — tag the property (rule #18). The SAME route the
 * SPA's TagAssignList uses; idempotent server-side, resolves a merged-away
 * property to its survivor. */
export async function attachTag(
  tag_id: number,
  property_id: number,
): Promise<ApiResult<TagWriteResult>> {
  return request<TagWriteResult>(`/properties/${property_id}/tags`, {
    method: 'POST',
    body: JSON.stringify({ tag_id }),
  });
}

/* DELETE /properties/:id/tags/:tag_id — take the tag off the property. */
export async function detachTag(
  tag_id: number,
  property_id: number,
): Promise<ApiResult<TagWriteResult>> {
  return request<TagWriteResult>(`/properties/${property_id}/tags/${tag_id}`, {
    method: 'DELETE',
  });
}

/* POST /collections/:id/properties — add the property to a collection (rule #18).
 * The SAME bearer-gated route the SPA's CollectionSaveMenu uses; idempotent
 * server-side (ON CONFLICT DO NOTHING). Returns `{added, skipped}`. */
export async function addToCollection(
  collection_id: number,
  property_id: number,
): Promise<ApiResult<CollectionWriteResult>> {
  return request<CollectionWriteResult>(`/collections/${collection_id}/properties`, {
    method: 'POST',
    body: JSON.stringify({ property_ids: [property_id] }),
  });
}

/* DELETE /collections/:id/properties/:property_id — remove the property from a
 * collection. Returns `{removed}`. */
export async function removeFromCollection(
  collection_id: number,
  property_id: number,
): Promise<ApiResult<CollectionWriteResult>> {
  return request<CollectionWriteResult>(
    `/collections/${collection_id}/properties/${property_id}`,
    { method: 'DELETE' },
  );
}

/* GET /properties/:id/notes — the property's operator notes (rule #18),
 * most-recent-first. */
export function listNotes(property_id: number): Promise<ApiResult<Note[]>> {
  return requestData(`/properties/${property_id}/notes`);
}

/* POST /properties/:id/notes — add an operator note to the property. The SAME
 * bearer-gated route the SPA's CurationBlock uses; `shared` is the author's
 * consent for it to cross a share (migration 610); `origin_listing_ref_id`
 * records the advert being viewed (display provenance only). Returns the
 * created note. */
export async function addNote(
  property_id: number,
  body: string,
  shared: boolean,
  origin_listing_ref_id: number | null,
): Promise<ApiResult<Note>> {
  return request<Note>(`/properties/${property_id}/notes`, {
    method: 'POST',
    body: JSON.stringify({ body, shared, origin_listing_ref_id }),
  });
}

/* PATCH /properties/:id/notes/:note_id — edit a note's body or its shared mark
 * in place (author only). Same bearer-gated route the SPA's NoteRow uses.
 * Returns the updated note. */
export async function updateNote(
  property_id: number,
  note_id: number,
  patch: NotePatch,
): Promise<ApiResult<Note>> {
  return request<Note>(`/properties/${property_id}/notes/${note_id}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

/* DELETE /properties/:id/notes/:note_id — remove a note. */
export async function deleteNote(
  property_id: number,
  note_id: number,
): Promise<ApiResult<{ deleted: true }>> {
  return request<{ deleted: true }>(`/properties/${property_id}/notes/${note_id}`, {
    method: 'DELETE',
  });
}
