"""The publication rule: WHEN a property is served (rule 16/25, operator rulings 2026-10-23, 2026-11-17).

A property is PUBLISHED once it carries `properties.settled_at`: ONE stored fact, written once by
the maintenance pass (`scripts/recompute_property_stats._SETTLE_SQL`) and read by every surface.
The pass stamps a property when the engine has read every ad it will read on it (an ad outside the
engine's scope is never read and never waited for) and either the pipeline has settled it (first
photo stored or none, no pending engine question, no group with an ad on another property) or it is
`CAP_SQL` old. So the cap bounds the wait for photos and for an engine DECISION, never for the
engine's READ: an ad the engine has not read yet may be a re-list of a property already told, and
publishing it would tell it again (the 2026-11-17 ruling; the engine's read lag has its own alarms,
dedup:unread_arrivals and dedup:held_unpublished).
published <=> settled_at IS NOT NULL; a watchdog's "what appeared after it was switched on" is
settled_at > active_since.

The SQL surfaces carry the inline text verbatim (browse_projection and the location scorecard's
served cohort, migrations 748 and 749, held by tests/test_browse_read_path_guardrail.py and
tests/location_data/test_location_quality_view_contract.py); the Python sites render it through
these helpers, each registered in tests/test_property_location_ad_rail.py PUBLISHED_RULE_SITES.
"""

from __future__ import annotations

# The stamper's one bounded wait (photos, an engine decision). Read by the stamper only; never a
# setting (ruling: no flags).
CAP_SQL = "interval '6 hours'"


def visible_since_sql(properties: str = "p") -> str:
    """The moment a property became served (NULL: not yet), off the `properties` alias given."""
    return f"{properties}.settled_at"


def published_predicate(properties: str = "p") -> str:
    """The inline rule for a surface that already joins `properties` as `properties`."""
    return f"{visible_since_sql(properties)} IS NOT NULL"


def published_property_predicate(property_id: str = "l.property_id") -> str:
    """The rule re-keyed onto a surface's own property-id expression (the served-rule shape)."""
    return (f"EXISTS (SELECT 1 FROM properties pp WHERE pp.id = {property_id}"
            f" AND {published_predicate('pp')})")


# The text browse_projection (748) and the location scorecard's served cohort (749) carry verbatim.
PUBLISHED_PREDICATE = published_predicate()
