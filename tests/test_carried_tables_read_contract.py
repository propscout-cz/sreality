"""After a merge or a split the SPA re-reads the curation the merge moved by table
(`refreshAfterSplit`, frontend/src/lib/mergedAdverts.ts `CARRIED_TABLES`, through
lib/curationSync's map). `tsc` cannot see the registry that list copies,
`toolkit.property_carriers.PROPERTY_CARRIERS` (rule 18): every table a carrier keeps true
across a merge is in it, and nothing else is, bar the tables named in `NOT_CURATION`."""

from __future__ import annotations

import re
from pathlib import Path

from toolkit.property_carriers import carried_columns

ROOT = Path(__file__).resolve().parents[1]
_TS = ROOT / "frontend" / "src" / "lib" / "mergedAdverts.ts"

# Carried tables the SPA's curation map does not re-read, each with why.
NOT_CURATION: dict[str, str] = {
    "notification_dispatches":
        "the event table (rule 16): alerts are no curation (GLOSSARY), read by the feed's own keys",
}


def _carried_tables_ts() -> list[str]:
    src = _TS.read_text(encoding="utf-8")
    m = re.search(r"\bconst CARRIED_TABLES\b[^=]*=\s*\[(.*?)\];", src, re.DOTALL)
    assert m, f"CARRIED_TABLES not found in {_TS.relative_to(ROOT)}"
    return re.findall(r"'([^']*)'", m.group(1))


def _carried_tables_py() -> set[str]:
    return {table for table, _column in carried_columns()}


def test_the_spa_rereads_every_table_a_merge_carries() -> None:
    ts = _carried_tables_ts()
    assert len(ts) == len(set(ts)), f"CARRIED_TABLES repeats a table: {ts}"
    expected = _carried_tables_py() - set(NOT_CURATION)
    assert set(ts) == expected, (
        f"CARRIED_TABLES lacks {sorted(expected - set(ts))} and names {sorted(set(ts) - expected)} "
        "beyond PROPERTY_CARRIERS (toolkit/property_carriers.py): copy the carrier list, or name "
        "a carried table that is no curation in NOT_CURATION with its reason")


def test_not_curation_names_carried_tables_only() -> None:
    stale = sorted(set(NOT_CURATION) - _carried_tables_py())
    assert not stale, f"NOT_CURATION names tables no carrier carries: {stale}"
