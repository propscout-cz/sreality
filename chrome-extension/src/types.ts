/* The MF result type and its shape rule are the SPA's own zero-dependency
 * module (like brand.ts), imported rather than copied. */
import type { ReferenceRent } from '../../frontend/src/lib/mfReference';
import type { ListingSnapshotPublic, PropertySource } from '../../frontend/src/lib/types';

/* Subset of the SPA's EstimationRun shape — we only consume the
 * fields needed by the yield panel. Mirroring frontend/src/lib/types.ts
 * but kept local so the extension build doesn't reach across into the
 * SPA's path aliases. */

export interface YieldScenario {
  rent_czk: number | null;
  fond_per_m2_czk: number | null;
  price_czk: number | null;
  /* Migration 213 — flat one-off renovation budget added to price_czk to form
   * the total acquisition cost (the yield denominator). Null/absent = none. */
  renovation_czk: number | null;
  updated_at: string;
}

export interface EstimationRun {
  id: number;
  status: 'pending' | 'running' | 'success' | 'failed';
  estimate_kind: 'rent' | 'sale' | null;
  input_sreality_id: number | null;
  input_purchase_price_czk: number | null;
  estimated_monthly_rent_czk: number | null;
  estimated_sale_price_czk: number | null;
  gross_yield_pct: number | null;
  confidence: 'high' | 'medium' | 'low' | null;
  input_spec: { area_m2?: number | null } | null;
  scenario: YieldScenario | null;
  reference_rent: ReferenceRent | null;
  error_message: string | null;
}

export interface EstimationListResponse {
  data: EstimationRun[];
  total: number;
  limit: number;
  offset: number;
}

export interface YieldScenarioUpdate {
  rent_czk?: number | null;
  fond_per_m2_czk?: number | null;
  price_czk?: number | null;
  renovation_czk?: number | null;
}

/* Deal-pipeline membership for the listing's property (rule #22). Null when the
 * listing has no property yet (not in our DB, or the ~5-min straggler-attach
 * lag). `in_pipeline` is the bookmark state; stage_* describe the current stage
 * when bookmarked. */
export interface PipelineMembership {
  in_pipeline: boolean;
  /* The current stage's id — the `<select>` value when changing stage. */
  stage_id: number | null;
  stage_key: string | null;
  stage_label: string | null;
  /* Short funnel badge + accent (migration 377), served by the lookup so the
   * panel renders the same mark as the SPA without a second round trip.
   * stage_code null = fall back to the stage's ordinal in `stages`. */
  stage_code: string | null;
  stage_color: string | null;
}

/* One operator-curated pipeline stage (GET /pipeline/stages). Mirrors the SPA's
 * stage shape; the extension reads id + label to populate the stage `<select>`. */
export interface PipelineStage {
  id: number;
  key: string;
  label: string;
  position: number;
  color: string | null;
  is_terminal: boolean;
  is_entry: boolean;
  code: string | null;
}

/* Response of the pipeline-card writes: POST /pipeline/cards (add) and
 * PATCH /pipeline/cards/{id} (move) both return the card (incl. its stage);
 * DELETE returns `{removed}`. We only read the few fields the control reflects. */
export interface PipelineCardResult {
  property_id?: number;
  stage_id?: number;
  stage_key?: string | null;
  stage_label?: string | null;
  stage_code?: string | null;
  stage_color?: string | null;
  added?: boolean;
  removed?: boolean;
}

/* One collection the caller can read (GET /collections): its own, or one
 * another account shared with it view-only (migration 610). Property-grain
 * (rule #18); `monitoring_enabled` marks it as a watchlist (always false on a
 * shared one: alerts go to its owner). `owner_email` is null exactly when it is
 * MINE — the one field mine vs shared is told by (glyphs.ts `isMine`). */
export interface ExtCollection {
  id: number;
  name: string;
  monitoring_enabled: boolean;
  owner_email: string | null;
  /* Who else reads it: on mine, every recipient; on a shared one, only my own
   * grant (`email` null = me). Read for a note's share audience; absent from
   * an API that predates sharing (same posture as `tag_ids`). */
  shared_with?: { account_id: string; email: string | null }[];
}

/* A tag (GET /tags, rule #18): a name in one of the stage palette's colours. */
export interface ExtTag {
  id: number;
  name: string;
  color: string;
}

/* Response of POST /dismissals (`added`) and DELETE /dismissals/{id}
 * (`removed`). Read only to confirm the write landed. */
export interface DismissalWriteResult {
  property_id: number;
  added?: boolean;
  removed?: boolean;
}

/* Response of the collection writes: POST /collections/{id}/properties (add)
 * returns `{added, skipped}`; DELETE /collections/{id}/properties/{property_id}
 * returns `{removed}`. We only read these to confirm the write landed. */
export interface CollectionWriteResult {
  added?: number;
  skipped?: number;
  removed?: boolean;
}

/* Response of POST /properties/{id}/tags (`attached`) and DELETE
 * /properties/{id}/tags/{tag_id} (`detached`). Read only to confirm it landed. */
export interface TagWriteResult {
  attached?: boolean;
  detached?: boolean;
}

/* One note about a PROPERTY (rule #18) the caller can read: its own, or
 * another author's SHARED note on a property a share puts in reach (migration
 * 610). Read via GET /properties/{id}/notes (most-recent-first). */
export interface ExtNote {
  id: number;
  property_id: number;
  body: string;
  /* The author's consent for it to cross a share (default false). A foreign
   * note is always shared — else it would not be readable. */
  shared: boolean;
  created_at: string;
  /* Null until the operator edits the note's body (marking it shared is not an edit). */
  updated_at: string | null;
  /* The author's email; null exactly when the note is mine (read-only otherwise). */
  owner_email: string | null;
}

/* PATCH /properties/{id}/notes/{note_id}: at least one field (author only). */
export interface NotePatch {
  body?: string;
  shared?: boolean;
}

/* One entry from POST /listings/lookup — our scraped facts for a portal
 * listing keyed by (source, native id), including its property's MF
 * reference-rent result + "Výnos MF" gross yield (the same figures Browse cards
 * show), a handle on any existing successful estimation, the property's
 * deal-pipeline membership, and its collection memberships. */
export interface PortalListing {
  source: string;
  source_id: string;
  found: boolean;
  /* LEGACY app-wide listing identity (negative for non-sreality portals).
   * NULL for a post-Gate-2 listing — use `found` to test DB membership and
   * `listing_id` to reference the listing in a write. The deep link uses the
   * canonical /listing/{source}/{source_id} natural-key route regardless. */
  sreality_id: number | null;
  /* Surrogate listing identity — present for every listing in our DB. */
  listing_id: number | null;
  /* The grouping property (the pipeline + dedup grain). Null when not in our
   * DB or not yet attached to a property. */
  property_id: number | null;
  category_main: string | null;
  category_type: string | null;
  area_m2: number | null;
  /* WHICH area `area_m2` holds, as stamped by the parser that wrote it
   * (migration 423): usable | floor | total | plot | unknown. `area_m2` is
   * polymorphic — on `pozemek` it IS the parcel — so no surface may render or
   * multiply it without consulting this. Null on a row stamped before the
   * column existed. */
  area_basis: string | null;
  price_czk: number | null;
  /* THE per-m² measure (migration 425) and its label, computed server-side at
   * LISTING grain. Never re-derive the quotient here: the measure carries
   * its own per-basis validity floors, so a row it withheld a number from must
   * render a gap, not a quotient. `price_per_m2_basis` is one of
   * 'sale_capital_czk_m2' | 'rent_monthly_czk_m2' | 'land_capital_czk_m2' |
   * null, and names the unit the number is in. */
  price_per_m2: number | null;
  price_per_m2_basis: string | null;
  disposition: string | null;
  /* Portal-agnostic property sub-type (migration 152): the meaningful "kind"
   * for commercial/houses where disposition is NULL. */
  subtype: string | null;
  /* The display "kind": the Czech subtype label (commercial/houses) else the
   * disposition (apartments). Computed server-side so the extension carries no
   * slug→label dictionary of its own. */
  kind_label: string | null;
  /* The ONE place string the whole product prints, composed server-side from
   * the listing's resolved location (migration 503). It replaced a
   * `district ?? locality` pair that this panel fell back through in one order
   * while the SPA fell back through it in the other, so the same listing could
   * be labelled two different ways on two surfaces. */
  display_label: string | null;
  is_active: boolean | null;
  last_seen_at: string | null;
  /* MF is PROPERTY-grain — every advert of one flat shows its property's
   * figures; null for a non-flat, and for an advert not yet attached to a
   * property. `mf_reference_rent` is the whole result, rendered by its SHAPE
   * (mfShape): a value's breakdown, a range + note, or a note alone. Absent
   * only from an API that predates it — then there is nothing to render. */
  mf_reference_rent_czk: number | null;
  mf_gross_yield_pct: number | null;
  mf_reference_rent?: ReferenceRent | null;
  /* What the PROPERTY is, each field at its MS11 grain
   * (docs/design/merge-sprint/PROGRAM.md), off the same properties_public row as
   * MF and, like it, null before attach. Stored slugs, as the SPA reads them.
   * `canonical_listing_id` is the canonical advert, the price history's first
   * track; `property_subtype` is the canonical advert's (`subtype` above is this
   * advert's); `plot_area_m2` is THE plot measure (migration 534). */
  canonical_listing_id: number | null;
  first_seen_at: string | null;
  tom_days: number | null;
  floor: number | null;
  total_floors: number | null;
  condition: string | null;
  ownership: string | null;
  property_subtype: string | null;
  plot_area_m2: number | null;
  /* The default service charge (fond oprav + SVJ) to seed the yield panel
   * with, in Kč per m² of floor area per MONTH — resolved server-side from
   * THIS subject's denominator. THREE distinct states, and collapsing any two
   * of them blanks the panel's headline yield:
   *   a number — the rate to seed;
   *   `null`   — no fond can apply to this subject at all (its area is a
   *              parcel). This is the server's ANSWER, and the one definition
   *              of that decision — no consumer re-derives it;
   *   absent   — the API predates W7 and knows nothing of the field.
   * Optional on the wire for exactly that last reason: `api.ts` sends no
   * version header, so a bundle built here can meet an older deployment, and
   * the compiler must force that case to be handled (see `defaultFond` /
   * LEGACY_FOND_FALLBACK_CZK_PER_M2 in content.ts). The one definition of the
   * NUMBER lives in api/schemas.DEFAULT_FOND_CZK_PER_M2. */
  fond_per_m2_czk_default?: number | null;
  latest_estimation: {
    estimation_id: number;
    estimate_kind: 'rent' | 'sale' | null;
    gross_yield_pct: number | null;
  } | null;
  pipeline: PipelineMembership | null;
  /* Collection memberships of the listing's property (rule #18): mine AND
   * those shared with me — "saved" is only the mine part (glyphs.ts
   * `savedCollections`). Null when the listing has no property yet (same
   * posture as `pipeline`). */
  collection_ids: number[] | null;
  /* The property's tags (rule #18), same posture; absent from an API that
   * predates the field. Names + colours come from `list_tags`. */
  tag_ids?: number[] | null;
  /* Whether the caller dismissed the listing's property (migration 536). Null
   * when there is no property yet; absent when the API predates the field —
   * either way the panel shows no hide control. */
  dismissed?: boolean | null;
}

export interface PortalLookupResponse {
  data: PortalListing[];
}

/* GET /properties/{id}/price-history: the SPA property page's two reads, row for
 * row (adverts oldest first, snapshots by scrape time). */
export interface PriceHistoryResponse {
  data: { sources: PropertySource[]; snapshots: ListingSnapshotPublic[] };
}

export interface PortalLookupItem {
  source: string;
  source_id: string;
}

/* POST /listings/ingest and GET /listings/ingest/{source}/{native_id} answer
 * this ONE shape (docs/design/new-property-from-link.md §3.2): market data
 * only, no per-account field. `message_cs` is the one Czech sentence of a
 * refused / gone / failed state; null otherwise. */
export interface IngestState {
  state: 'refused' | 'held' | 'queued' | 'gone' | 'failed';
  message_cs: string | null;
  source: string | null;
  source_id_native: string | null;
  listing_id: number | null;
  is_active: boolean | null;
  /* Null while the ad we hold is still unborn (the maintenance lane's birth). */
  property_id: number | null;
  queue: { attempts: number; claimed_at: string | null; given_up: boolean } | null;
}

/* Message protocol between content script and background worker.
 * The background worker is the only context allowed to make API
 * fetches — host_permissions covers it without CORS getting in the
 * way. Content scripts post messages, get the typed result back. */
export type ApiMessage =
  | { type: 'lookup_listings'; items: PortalLookupItem[] }
  | { type: 'ingest_listing'; url: string }
  | { type: 'ingest_state'; source: string; native_id: string }
  | { type: 'patch_scenario'; run_id: number; body: YieldScenarioUpdate }
  /* Sending `estimate_kind` + `category_main` is what stops the panel firing
   * an apartment monthly per-m² comparable table at a house or a plot. */
  | {
      type: 'create_estimation';
      listing_id: number;
      estimate_kind?: 'rent' | 'sale';
      category_main?: string | null;
    }
  | { type: 'get_estimation'; run_id: number }
  | { type: 'add_pipeline_card'; property_id: number }
  | { type: 'remove_pipeline_card'; property_id: number }
  | { type: 'move_pipeline_card'; property_id: number; stage_id: number }
  | { type: 'list_pipeline_stages' }
  | { type: 'dismiss_property'; property_id: number }
  | { type: 'undismiss_property'; property_id: number }
  | { type: 'list_collections' }
  | { type: 'list_tags' }
  | { type: 'attach_tag'; tag_id: number; property_id: number }
  | { type: 'detach_tag'; tag_id: number; property_id: number }
  | { type: 'add_to_collection'; collection_id: number; property_id: number }
  | { type: 'remove_from_collection'; collection_id: number; property_id: number }
  | { type: 'list_notes'; property_id: number }
  | {
      type: 'add_note'; property_id: number; body: string; shared: boolean;
      origin_listing_ref_id: number | null;
    }
  | { type: 'update_note'; property_id: number; note_id: number; patch: NotePatch }
  | { type: 'delete_note'; property_id: number; note_id: number }
  | { type: 'sign_in' }
  | { type: 'sign_out' }
  | { type: 'get_auth_state' }
  | { type: 'get_billing' };

/* Background → content, the other direction: webNavigation.onHistoryStateUpdated
 * fires when a portal's SPA soft-navigates (History API, no new document), which
 * static manifest content_scripts never re-inject for. The background worker
 * relays the new URL so the already-running content script can re-decide what
 * to render without needing a real page reload. */
export type RouteMessage = { type: 'route_changed'; url: string };

/* The caller's agent-estimation allowance (GET /billing/me → agent_estimations),
 * for the panel's "(zbývá X)" counter + the upgrade prompt at zero. `metered`
 * is false for the operator/admin (unlimited) — the panel hides the counter. */
export interface AgentQuota {
  quota: number;
  used: number;
  remaining: number;
  is_trial: boolean;
  metered: boolean;
}

/* GET /billing/me — only the fields the panel reads. */
export interface BillingMe {
  plan: string;
  status: string;
  agent_estimations: AgentQuota;
}

/* The extension's own Supabase session state (Wave 1) — read by the panel
 * to decide whether to show data or a "please sign in" prompt, and to show
 * the signed-in email + a sign-out control. */
export interface AuthState {
  signedIn: boolean;
  email: string | null;
}

export interface ApiResponse<T> {
  ok: true;
  data: T;
}

export interface ApiError {
  ok: false;
  status: number;
  detail: string;
}

export type ApiResult<T> = ApiResponse<T> | ApiError;
