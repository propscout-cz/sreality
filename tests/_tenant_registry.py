"""The user-state TENANT tables, stated once for both tenancy gates.

Each table here holds per-account rows scoped by RLS. tests/test_migration_rls_grants.py
allows `authenticated` DML only on these; tests/test_tenant_isolation_live.py checks each has
`account_id`, a policy and no anon privilege on the replayed schema. A new tenant table is
added HERE, and nowhere else; so is a tenant table whose browser writes a grant limits (BROWSER_WRITE_COLUMNS).
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
)

# Tenant tables the browser reads but writes only by these column grants (migration 679, rule 12): the API
# creates and finishes every run on the service role, so `authenticated` may UPDATE the listed columns and
# INSERT nothing. Both tenancy gates hold every migration from 679 on, and the replayed schema, to this.
BROWSER_WRITE_COLUMNS: dict[str, frozenset[str]] = {
    "estimation_runs": frozenset({"scenario"}),
    "building_runs": frozenset(),
}
BROWSER_WRITE_COLUMNS_SINCE = 679

# A tenant table's earlier name, which older migrations still grant on (592) and which may survive as
# a compatibility view (660's, until its drop). Not a table: the live checks read TENANT_TABLES alone.
RENAMED_TENANT_TABLES: dict[str, str] = {
    "property_note_attachments": "property_attachments",
}
