"""W16: the location chain's predicates are spelled once, in `location_data/location_steps.py`.

The readouts this rail first held together (the candidates funnel and the location audit
waterfall) are gone: the waterfall in W1 (migration 719), the funnel with the candidate lane
(#2681). What it still holds is the spelling every remaining reader carries: the town-missing
fragment that scraper.db and the scorecard view copy VERBATIM, the consumer rule imported and
never retyped, and the town floor applied by rank.
"""

from __future__ import annotations

import re
from pathlib import Path

from location_data import location_steps as ls

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "migrations"
STEPS_PY = REPO / "location_data" / "location_steps.py"

_LINE_COMMENT = re.compile(r"--.*$", re.MULTILINE)


def _squeeze(t: str) -> str:
    return " ".join(t.split()).lower()


def test_town_missing_is_one_fragment_the_scorecard_and_the_refetch_lane_share() -> None:
    """Rule 25's invariant, broken, is spelled once: the scorecard view (migration 698, which
    the location gauge reads) and scraper.db carry it VERBATIM (a migration cannot import it, and
    importing it in scraper.db is a cycle: location_steps -> claims_common -> loader_db ->
    scraper.db), held here. RED by: a lane that re-derives "no town" and queues a different set
    from the one the gauge judges."""
    from scraper import db

    missing = _squeeze(ls.town_missing_sql())
    scorecard = _squeeze(_LINE_COMMENT.sub("", (
        MIGRATIONS / "698_location_quality_scorecard.sql").read_text(encoding="utf-8")))
    assert _squeeze(ls.step_flags_sql()["has_town"]) in missing
    assert missing in _squeeze(db._LOCATION_REFETCH_CANDIDATES_SQL)
    assert missing in scorecard


def test_the_consumer_rule_is_imported_and_never_retyped() -> None:
    """`located` is claims_common's own rule. RED by: a local `geom IS NOT NULL` here,
    which would leave this chain behind the next change to what "served" means."""
    from location_data.claims_common import served_location_predicate

    assert ls.step_flags_sql()["located"] == served_location_predicate("l.id")
    body = STEPS_PY.read_text(encoding="utf-8")
    assert "from location_data.claims_common import served_location_predicate" in body
    # A claim's `value_geom` (the town-evidence probe) is not the answer row's `geom`.
    assert not re.search(r"\bgeom IS NOT NULL", body)


def test_the_town_floor_is_applied_by_rank_never_by_enum_order() -> None:
    floor = ls.obec_rank_floor_sql("gr")
    assert "location_granularity_rank" in floor
    assert "granularity >=" not in floor and "granularity > " not in floor
    assert _squeeze(floor) in _squeeze(ls.step_flags_sql()["has_town"])
