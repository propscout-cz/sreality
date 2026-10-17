"""The user-state TENANT tables, stated once for both tenancy gates.

Each table here holds per-account rows scoped by RLS. tests/test_migration_rls_grants.py
allows `authenticated` DML only on these; tests/test_tenant_isolation_live.py checks each has
`account_id`, a policy and no anon privilege on the replayed schema. A new tenant table is
added HERE, and nowhere else.
"""

from __future__ import annotations

TENANT_TABLES: tuple[str, ...] = (
    "collections",
    "tags",
    "property_notes",
    "filter_presets",
    "notification_subscriptions",
    "manual_rental_estimates",
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
    "property_note_attachments",
    "property_artifact_links",
    # Migration 609: owner-stamped by trigger; browser roles SELECT + DELETE, never INSERT.
    "collection_shares",
)
