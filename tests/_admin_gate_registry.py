"""The admin-only relations and every object sanctioned to read them ungated, stated once for both admin gates.

A view or function that reads a relation in ADMIN_ONLY_RELATIONS embeds a sound is_platform_admin() gate, or it is
registered in UNGATED. tests/test_migration_rls_grants.py exempts a migration's CREATE only when UNGATED sanctions that
(file, name) pair; tests/test_tenant_isolation_live.py waives live_allowlist() in its sweep of the replayed schema and
asserts every live 'acl' entry stays dark to both browser roles. A new admin relation or ungated object is added HERE
(one that changes live_allowlist() also updates its pin, test_live_allowlist_is_pinned): the in-migration
`-- ci-allow-ungated:` comment is retired from ANNOTATION_CUTOVER on, and the older ones must each be registered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ANNOTATION_CUTOVER = 676

# Base relations and matviews that hold admin-only operational data. A browser reaches one only through an admin-gated
# view or API route, or through an UNGATED entry below.
#
# Deliberately absent: listings / properties / images, shared-market data behind many legitimately open views
# (listings_public, properties_public, browse_*, price_stat_*); listing them would force a large, churny allowlist. The
# residual blind spot, an admin-only aggregate over ONLY those tables, is covered by the enumerated admin-view lists in
# tests/test_tenant_isolation_live.py. Tenant tables are absent too: RLS + security_invoker scope them, with their own
# tests.
ADMIN_ONLY_RELATIONS: tuple[str, ...] = (
    # Dropped by migration 475 with the legacy decision engine; kept so the scan of older migrations is unchanged.
    "dedup_engine_runs", "dedup_scan_state", "property_identity_candidates",
    # FROZEN, not dead: a paid verdict cache and the manual decision notes, still admin-only.
    "dedup_vision_bakeoff_results", "dedup_decision_feedback",
    "property_merge_events",
    # The carry record (migration 589): read only by the merge route, the brake's dry run and the split, all
    # service-role.
    "property_merge_carries",
    "listing_detail_queue", "listing_fetch_failures", "detail_queue_completions",
    "llm_calls", "parsed_url_cache", "pipeline_check_results",
    # tag_taxonomy + image_tag_labels (migration 442) are the tag ground truth. image_tag_annotations / phash_pair_notes
    # left with image_training_examples: no app code touches the three since the tag-annotation-matrix cutover.
    "image_border_cases", "tag_taxonomy", "image_tag_labels",
    # The append-only decision history behind image_tag_labels (migration 446).
    "image_tag_label_events",
    # The per-tag review queue (450) and the sealed exam roster (458); publishing the roster would hand anyone the
    # answer key's index.
    "tag_candidates", "tag_exam_cohorts", "tag_exam_members", "tag_exam_sets",
    # The screener's guesses and the sitting pre-answers (459, 461): machine output about the answer key.
    "tag_exam_screens", "tag_exam_suggestions",
    "workflow_failures", "workflow_run_health",
    # Migration 340: scrape_runs_public / recent_scrape_runs() shipped ungated while this list was seeded from 318's
    # objects instead of a table inventory.
    "scrape_runs", "worker_heartbeats",
    "dedup_funnel_resolutions_archive",
    # The tagging bake-off store (migration 489, schema dedup_sim), read through /new-dedup/tagging-bakeoff/*.
    "dedup_sim.tag_head_bakeoff_runs",
    "dedup_sim.tag_head_bakeoff_arms",
    "dedup_sim.tag_head_bakeoff_vectors",
    "dedup_sim.tag_head_bakeoff_scores",
    "dedup_sim.tag_head_bakeoff_metrics",
    # The versioned tag model (migration 490, PUBLIC schema so it survives the drop of dedup_sim), read through
    # /new-dedup/tags/*. images_public publishes the ACTIVE model's per-photo scores (UNGATED below).
    "tag_head_models", "tag_head_model_heads", "image_tag_scores",
    # The Level-0 candidate store (migration 492, schema dedup_sim), read through /new-dedup/candidates/*.
    "dedup_sim.candidate_inputs",
    "dedup_sim.candidate_generations",
    "dedup_sim.candidate_pairs",
    # The autodedup engine's own store (migration 528): the browser client is pinned to schema `public`, so the
    # progress page and the validation UI read it through the admin-gated API.
    "autodedup.settings",
    "autodedup.runs",
    "autodedup.iterations",
    "autodedup.listing_fp",
    "autodedup.image_band",
    "autodedup.phash_pop",
    "autodedup.exploded_blocks",
    "autodedup.pairs",
    "autodedup.judgements",
    "autodedup.must_not_link",
    "autodedup.clusters",
    "autodedup.cluster_members",
    "autodedup.cluster_conflicts",
    "autodedup.merges",
    "autodedup.verdicts",
    "autodedup.labels",
    "autodedup.models",
    "autodedup.eval_samples",
    "autodedup.resolve_queue",
    "autodedup.scan_cursor",
    "autodedup.judge_queue",
    # A1's apply ledger (migration 558), dry runs and refusals included.
    "autodedup.applied_merges",
    # The operator's own Browse merges as engine data (migration 564, E299).
    "autodedup.operator_merges",
    # The sold-transaction fetch LEDGER (migration 542): a cell is fetched only where some account holds a live
    # deal-pipeline card, so its obec set is a projection of tenant state: publishing it would show every account the
    # map and cadence of every other account's deal sourcing. Its one reader is sold_coverage (UNGATED).
    "sold_transaction_fetches",
    # Auth identities (migration 610): emails are PII. The one reader is account_email (UNGATED); the browser reaches a
    # label only through share_party_emails(), counterparties only.
    "auth.users",
    # The health check registry and the incident table (619 / 462), read only through pipeline_checks_public.
    "health_checks",
    "ops_incidents",
    # The push device registry and its delivery ledger (migration 654).
    "push_subscriptions",
    "notification_deliveries",
    # The scheduled-jobs lease and its run history (migration 675): holders, host names and log tails.
    "scheduled_jobs",
    "job_runs",
)

Kind = Literal["view", "matview", "function"]
# reachable: a browser role reads or calls it on purpose; `why` says what makes that safe.
# acl:       neither browser role holds SELECT / EXECUTE on it; the live lane asserts that.
# trigger:   a trigger function, which nothing can call as an RPC whatever its ACL.
# regated:   created ungated before migration 318, which re-created it with the gate.
Guard = Literal["reachable", "acl", "trigger", "regated"]


@dataclass(frozen=True)
class Ungated:
    """One object allowed to read admin-only data without the gate, and the migrations allowed to create it so."""

    kind: Kind
    guard: Guard
    sanctioned_in: tuple[str, ...]
    why: str
    dropped_in: str | None = None


_OPS_MATVIEW = "Admin ops matview kept dark by its revoke; a matview cannot embed is_platform_admin()."
_REGATED = "Created ungated before the gate existed; migration 318 re-created it with the gate."

UNGATED: dict[str, Ungated] = {
    "images_failure_overview_mv": Ungated(
        "matview", "acl", ("354_health_image_matviews_on_listing_id.sql",), _OPS_MATVIEW,
        dropped_in="643_drop_legacy_health_layer.sql"),
    "image_storage_overview_mv": Ungated(
        "matview", "acl", ("354_health_image_matviews_on_listing_id.sql",), _OPS_MATVIEW,
        dropped_in="643_drop_legacy_health_layer.sql"),
    "snapshot_churn_24h_mv": Ungated(
        "matview", "acl", ("354_health_image_matviews_on_listing_id.sql",), _OPS_MATVIEW,
        dropped_in="643_drop_legacy_health_layer.sql"),
    "scraper_health_checks_mv": Ungated(
        "matview", "acl", ("354_health_image_matviews_on_listing_id.sql",), _OPS_MATVIEW,
        dropped_in="643_drop_legacy_health_layer.sql"),
    "health_summary_mv": Ungated(
        "matview", "acl", ("354_health_image_matviews_on_listing_id.sql",), _OPS_MATVIEW,
        dropped_in="643_drop_legacy_health_layer.sql"),
    "dedup_funnel_resolutions_mv": Ungated(
        "matview", "acl", ("361_readmodel_views_listing_id.sql",), _OPS_MATVIEW,
        dropped_in="432_new_dedup_teardown.sql"),
    "dedup_llm_cost_by_category_mv": Ungated(
        "matview", "acl", ("361_readmodel_views_listing_id.sql",), _OPS_MATVIEW,
        dropped_in="432_new_dedup_teardown.sql"),
    "refresh_health_matviews": Ungated(
        "function", "acl",
        ("371_pg_cron_statement_timeout_scoping.sql", "441_stamp_derived_artifact.sql"),
        "Only refreshes matviews and returns void; EXECUTE is held by pg_cron and the operator.",
        dropped_in="643_drop_legacy_health_layer.sql"),
    "llm_cost_hour_union": Ungated(
        "view", "acl", ("437_llm_cost_hour_rollup_and_derived_artifacts.sql",),
        "The union source behind llm_cost_{daily,hourly}_public, every privilege revoked. The gate sits in each "
        "wrapper's outermost scope, where is_platform_admin() renders as a One-Time Filter above the Append; inside "
        "the union it would run per branch and would couple llm_cost_today_usd(), the API's spend guard, to the "
        "admin gate. tests/test_llm_cost_rollup_indexable.py asserts the ACL too."),
    "refresh_llm_cost_rollups": Ungated(
        "function", "acl", ("437_llm_cost_hour_rollup_and_derived_artifacts.sql",),
        "Called by pg_cron and the operator only; EXECUTE revoked from every browser role."),
    "llm_cost_today_usd": Ungated(
        "function", "acl", ("437_llm_cost_hour_rollup_and_derived_artifacts.sql",),
        "The API's spend guard, called over its own service connection; EXECUTE revoked from every browser role."),
    "log_image_tag_label_event": Ungated(
        "function", "trigger", ("446_tag_annotation_provenance.sql",),
        "An AFTER trigger on a backend-only table, not SECURITY DEFINER: it runs as whoever writes image_tag_labels, "
        "who already reads it. Not its ACL: production grants authenticated EXECUTE despite 446's revoke from public."),
    "refresh_location_compare_cohort": Ungated(
        "function", "acl", ("493_location_compare_cohort.sql",),
        "A write-only maintenance function returning void; EXECUTE revoked from every browser role.",
        dropped_in="502_location_w2b_drop_old_projection.sql"),
    "sold_coverage": Ungated(
        "function", "reachable", ("545_sold_comps_read_surface.sql",),
        "The operator's listing page says when we last looked there. SECURITY DEFINER and narrow: a point in, four "
        "facts about the one municipality containing it out; never a cell list, a bbox, `error` or `pages`, so it "
        "cannot be walked to enumerate which towns are being worked."),
    "images_public": Ungated(
        "view", "reachable", ("591_images_public_tag_head_scores.sql",),
        "Every signed-in Browse card's cover is chosen by the active tag model's scores (operator ruling "
        "2026-10-07). It publishes a photo's per-head scores and which model is active: market facts about a photo, "
        "like its CLIP tag columns since 236; never a label, a labeller, model artifacts or metrics."),
    "account_email": Ungated(
        "function", "acl", ("610_collection_shares.sql",),
        "Reads auth.users; EXECUTE revoked from every browser role, asserted at the end of 610."),
    "health_liveness": Ungated(
        "function", "acl",
        ("620_health_liveness.sql", "621_platform_checks.sql", "633_health_explicit_cells.sql",
         "655_health_liveness_without_api_cell.sql", "675_scheduled_jobs_expand.sql", "682_health_job_cells.sql"),
        "Reads the health ledgers and the worker heartbeat, writes one pipeline_check_results row; EXECUTE revoked "
        "from public, anon and authenticated."),
    "health_job_cells": Ungated(
        "function", "acl", ("682_health_job_cells.sql",),
        "Reads scheduled_jobs, job_runs and worker_heartbeats for the pg_cron watcher (security invoker, called by "
        "the definer watcher); EXECUTE revoked from public, anon and authenticated."),
    "rebuild_browse_list": Ungated(
        "function", "acl",
        ("630_one_browse_read_model.sql", "649_web_portal.sql", "658_rebuild_browse_list_without_notice.sql"),
        "Reads property_merge_events only to re-patch browse_list_next; EXECUTE revoked from public, anon and "
        "authenticated (590)."),
    "location_jobs": Ungated(
        "view", "acl", ("675_scheduled_jobs_expand.sql",),
        "The temporary alias of scheduled_jobs that keeps the pre-rename deployment on its lease; security invoker "
        "over an RLS table, every privilege revoked. tests/scraper/test_job_lease_live.py asserts the ACL too."),
    "dedup_label_events": Ungated(
        "view", "regated",
        ("300_dedup_golden_set_foundation.sql", "311_source_trust_rank_and_sign_check.sql"), _REGATED,
        dropped_in="475_new_dedup_teardown_finish.sql"),
    "dedup_vision_bakeoff_results_public": Ungated(
        "view", "regated", ("303_dedup_vision_bakeoff_results.sql",), _REGATED),
    "dedup_recency_backlog": Ungated(
        "view", "regated", ("307_dedup_recency_instrumentation.sql",), _REGATED,
        dropped_in="475_new_dedup_teardown_finish.sql"),
    "image_tag_annotations_public": Ungated(
        "view", "regated", ("308_clip_phash_audit_tooling.sql",), _REGATED),
    "phash_pair_notes_public": Ungated(
        "view", "regated", ("308_clip_phash_audit_tooling.sql",), _REGATED),
    "image_training_examples_public": Ungated(
        "view", "regated", ("309_clip_training_examples.sql",), _REGATED),
    "image_border_cases_public": Ungated(
        "view", "regated", ("310_image_border_cases.sql",), _REGATED),
}


def sanctioned(migration: str, name: str) -> bool:
    """True when `migration` (a file name) may create `name` without the admin gate."""
    entry = UNGATED.get(name)
    return entry is not None and migration in entry.sanctioned_in


def live_allowlist() -> frozenset[str]:
    """What the live sweep waives: each live view (its view arm ignores grants) and each function a browser may call."""
    return frozenset(
        name for name, e in UNGATED.items()
        if e.dropped_in is None and e.guard in ("acl", "reachable")
        and (e.kind == "view" or (e.kind == "function" and e.guard == "reachable"))
    )
