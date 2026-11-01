"""The user-state TENANT tables, stated once for both tenancy gates.

Each table here holds per-account rows scoped by RLS. tests/test_migration_rls_grants.py
allows `authenticated` DML only on these; tests/test_tenant_isolation_live.py checks each has
`account_id`, a policy and no anon privilege on the replayed schema. A new tenant table is
added HERE, and nowhere else; so is a tenant table whose browser writes a grant limits (BROWSER_WRITE_COLUMNS), and
whether it advances the curation clock (CLOCKED_TABLES) or why not (NOT_CLOCKED; tests/test_curation_sync_census.py).
"""

from __future__ import annotations

TENANT_TABLES: tuple[str, ...] = (
    "collections",
    "tags",
    "property_notes",
    "filter_presets",
    # Migration 672: the boxes presets sit in.
    "filter_preset_groups",
    "notification_subscriptions",
    "collection_properties",
    "property_tags",
    "notification_dispatches",
    "estimation_cohort_entries",
    "estimation_trace_payloads",
    "estimation_feedback",
    "building_run_attachments",
    "estimation_runs",
    "building_runs",
    "property_pipeline",
    "pipeline_stages",
    "property_pipeline_events",
    "entitlements",
    "property_dismissals",
    # Migration 660: a property's files (592's property_note_attachments, renamed).
    "property_attachments",
    "property_artifact_links",
    # Migration 610: owner-stamped by trigger; browser roles SELECT + DELETE, never INSERT.
    "collection_shares",
    # Migration 626: the Stats board; since 647 one unnamed WORKING board per account plus named
    # dashboards, written by the API.
    "stats_dashboards",
    # Migration 641: account_id = the owner; browser roles SELECT + DELETE, never INSERT.
    # (delegated_edits is not here: browser roles only SELECT it, and it names its owner
    # `owner_account_id`.)
    "pipeline_shares",
    # Migration 647: account_id = the owner, stamped from the dashboard; browser roles SELECT + DELETE.
    "stats_dashboard_shares",
    # Migration 696: the curation clock; browser roles SELECT only (BROWSER_WRITE_COLUMNS), written by its trigger.
    "curation_clock",
    # Migration 704: the agent run ledger (ADR-0001); browser roles SELECT only (BROWSER_WRITE_COLUMNS).
    "agent_runs",
    "agent_run_steps",
    "agent_artifacts",
    # Migration 713: an account's own daily agent limit; browser roles SELECT only (BROWSER_WRITE_COLUMNS).
    "agent_account_limits",
)

# Tenant tables the browser reads but writes only by these column grants (migration 679, rule 12): the API
# creates and finishes every run on the service role, so `authenticated` may UPDATE the listed columns and
# INSERT nothing. Both tenancy gates hold every migration from 679 on, and the replayed schema, to this.
BROWSER_WRITE_COLUMNS: dict[str, frozenset[str]] = {
    "estimation_runs": frozenset({"scenario"}),
    "building_runs": frozenset(),
    # Migration 696: only bump_curation_clock() writes the clock (docs/design/curation-sync.md § 3.2).
    "curation_clock": frozenset(),
    # Migration 704: the service role writes these, runs and steps in api/agents/executor.py; no code writes an artifact yet.
    "agent_runs": frozenset(),
    "agent_run_steps": frozenset(),
    "agent_artifacts": frozenset(),
    # Migration 713: the service role writes it, PUT /runs/limit in api/routes/agent_runs.py, after the route's validation.
    "agent_account_limits": frozenset(),
}
BROWSER_WRITE_COLUMNS_SINCE = 679

# A tenant table's earlier name, which older migrations still grant on (592) and which may survive as
# a compatibility view (660's, until its drop). Not a table: the live checks read TENANT_TABLES alone.
RENAMED_TENANT_TABLES: dict[str, str] = {
    "property_note_attachments": "property_attachments",
}

# Curation sync (migration 696, docs/design/curation-sync.md § 3.2): each table whose change advances the curation
# clock, and the family it advances. One deferred `<table>_curation_clock` trigger per entry; the SPA's table→read map
# (frontend/src/lib/curationSync.ts SYNC_TABLES) names the same tables with the same families
# (tests/test_curation_sync_census.py).
CLOCKED_TABLES: dict[str, str] = {
    "property_pipeline": "board",
    "pipeline_stages": "board",
    "property_dismissals": "dismissals",
    "collections": "collections",
    "collection_properties": "collections",
    "tags": "tags",
    "property_tags": "tags",
    "property_notes": "notes",
    "property_attachments": "notes",
    "property_artifact_links": "links",
    "pipeline_shares": "shares",
    "collection_shares": "shares",
}

# Every tenant table (and the per-account tables beside them) the clock deliberately does not count, with why. A new
# tenant table lands in CLOCKED_TABLES or here.
NOT_CLOCKED: dict[str, str] = {
    "curation_clock": "the clock itself: a bump that clocked its own write would never settle",
    "delegated_edits": "written by stamp_delegated_edit() in the transaction of the curation row it stamps, which "
                       "bumps that row's family",
    "property_pipeline_events": "the board ledger, written only in the transaction of a property_pipeline change, "
                                "which bumps board (the health check's board_bumps cell leans on exactly this)",
    "notification_dispatches": "the rule-16 event table, not curation; the bell keeps its own polls (ruling Q1b)",
    "filter_presets": "presets are not curation (follow-up F5)",
    "filter_preset_groups": "preset groups are not curation (follow-up F5)",
    "notification_subscriptions": "watchdogs are not curation (ruling Q1b, follow-up F5)",
    "estimation_runs": "estimation runs started elsewhere are out of scope (ruling Q1d, follow-up F5)",
    "estimation_cohort_entries": "a run's cohort, immutable with its run (rule 12; follow-up F5)",
    "estimation_trace_payloads": "a run's trace, immutable with its run (rule 12; follow-up F5)",
    "estimation_feedback": "estimation feedback, out of scope with the runs (follow-up F5)",
    "building_runs": "building runs are estimations, out of scope (follow-up F5)",
    "building_run_attachments": "a building run's files, out of scope with the runs (follow-up F5)",
    "entitlements": "billing state, written by the Stripe webhook; not curation (follow-up F5)",
    "stats_dashboards": "Stats dashboards are not curation (follow-up F5)",
    "stats_dashboard_shares": "dashboard shares widen dashboard reads, not curation (follow-up F5)",
    "agent_runs": "a run is not curation: it ends in proposals the user applies, each under its own table's clock",
    "agent_run_steps": "a run's diary, not curation",
    "agent_artifacts": "a run's files, not curation",
    "agent_account_limits": "a spending setting read at claim time, not curation",
}
