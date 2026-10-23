"""The publication rule: WHEN a property is served (rule 16/25, operator ruling 2026-10-23).

A property is PUBLISHED once the pipeline has settled it (`properties.settled_at`, stamped by the
maintenance pass once its first photo is stored and the engine has read every ad, holds no pending
question about them and keeps none of them in a group that straddles another property), or once it
is `CAP` old — the one bounded wait, so an engine backlog can delay a property but never hide it.
ONE expression, `visible_since`, defines both: published <=> visible_since <= now(); a watchdog's
"what appears after it was switched on" is visible_since > active_since.

The SQL surfaces carry the inline text verbatim (browse_projection, migration 668), pinned against
this module by tests/test_668_publication_gate.py; the Python sites render the EXISTS form, each
registered in tests/test_property_location_ad_rail.py PUBLISHED_RULE_SITES. One cap, spelled here only.
"""

from __future__ import annotations

# The one bounded wait. A change is a migration restating browse_projection's WHERE (665's recipe)
# plus this constant; never a setting (ruling: no flags).
CAP_SQL = "interval '6 hours'"


def visible_since_sql(properties: str = "p") -> str:
    """The moment a property became (or becomes) served, off the `properties` alias given."""
    return f"coalesce({properties}.settled_at, {properties}.first_seen_at + {CAP_SQL})"


def published_predicate(properties: str = "p") -> str:
    """The inline rule for a surface that already joins `properties` as `properties`."""
    return f"{visible_since_sql(properties)} <= now()"


def published_property_predicate(property_id: str = "l.property_id", *, alias: str = "pp") -> str:
    """The rule re-keyed onto a surface's own property-id expression (the served-rule shape)."""
    return (f"EXISTS (SELECT 1 FROM properties {alias} WHERE {alias}.id = {property_id}"
            f" AND {published_predicate(alias)})")


# The text browse_projection carries verbatim (migration 668).
PUBLISHED_PREDICATE = published_predicate()
