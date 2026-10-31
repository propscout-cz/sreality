"""The wave shadow's first step (`resolver.what_if`): a wave's positions, computed without a write.

Hermetic: the claim changes are pure or run over a fake connection, the W4 re-mine reads the real
git contract against a page in the shape realitymix serves, and nothing here reaches a database.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from autodedup.export import build_location
from autodedup.export_sql import COHORT_LOCATION_SQL
from location_data import claims_intake, contracts, page_readers
from location_data.claims_intake import ScanRow
from location_data.page_readers import ArchivedPayload
from location_data.resolver import core, drain, what_if
from tests.location_data import claim_intake_fixtures as fx
from tests.location_data import mini_mirror as mm

T0 = datetime(2026, 10, 1, tzinfo=UTC)
CONTRACT = next(c for c in contracts.load_all() if c.source == "realitymix")

# The shape W4 is about: the server's geocode on `#print-map`, the interactive `#map` blank.
HIDDEN_PIN_PAGE = b"""<html><head>
<link rel="canonical" href="https://realitymix.cz/detail/beroun/byt-12345.html">
</head><body>
<div id="print-map" data-gps-lat="49.9638" data-gps-lon="14.0720" data-address="Beroun"></div>
<div id="map" data-gps-lat="" data-gps-lon="" data-address="Beroun"></div>
</body></html>"""


def _at(claim: Any, days: int) -> Any:
    return replace(claim, observed_at=T0 + timedelta(days=days))


def _v8() -> contracts.PortalContract:
    """W4's bump as a contract value: the marker reads the interactive map only."""
    entries = [replace(e, locator={**e.locator, "css": "div#map"})
               if e.entry_id == "rm.det.agency_est_flag" else e for e in CONTRACT.entries]
    return replace(CONTRACT, version=CONTRACT.version + 1, entries=entries)


def _mined(contract: contracts.PortalContract, first_id: int) -> list[Any]:
    row = fx.listing("realitymix", {}, listing_id=7, native="12345")
    payload = ArchivedPayload(id=9001, page_kind="detail", first_observed_at=T0,
                              body=HIDDEN_PIN_PAGE)
    register = page_readers.ScopeRegister.from_zones(contract.source, contract.exclusion_zones)
    result = page_readers.extract_page(payload, row, contracts.entries_of(contract),
                                       register=register)
    return [what_if.as_resolver_claim(c, first_id + n) for n, c in enumerate(result.claims)]


# ------------------------------------------------------------------ the claim changes


def test_purge_keeps_one_claim_per_type_and_author_the_newest_observation():
    """W3's keeper: the read already took the newest version, the purge takes the newest
    observation of it (the id breaks a tie), and the operator's claim is its own key."""
    old_pin = _at(mm.claim(1, "coordinate", lat=50.0, lon=14.0), 0)
    new_pin = _at(mm.claim(2, "coordinate", lat=50.1, lon=14.1), 1)
    first_town, second_town = (_at(mm.claim(i, "obec_name", value_text="Praha"), 0)
                               for i in (3, 4))
    operator = _at(mm.claim(5, "obec_name", value_text="Brno", licence_class="operator"), 0)
    out = what_if.purge(None, {7: [new_pin, old_pin, second_town, first_town, operator], 8: []})
    assert [c.id for c in out[7]] == [2, 4, 5]
    assert out[8] == []


def test_a_remine_restates_the_types_it_mined_and_keeps_the_rest():
    """`resolve_db._claims_sql` after a bump: per claim type the newest version (the re-mine's
    where it stated the type, today's otherwise); a type the contract stopped declaring is not
    read; the operator's claims and another portal's are untouched."""
    today = [
        mm.claim(1, "coordinate", source="realitymix", lat=50.0, lon=14.0),
        mm.claim(2, "obec_name", source="realitymix", value_text="Beroun"),
        mm.claim(3, "street_name", source="realitymix", value_text="Husova"),
        mm.claim(4, "obec_name", source="realitymix", value_text="Beroun",
                 licence_class="operator"),
        mm.claim(5, "obec_name", source="sreality", value_text="Beroun"),
    ]
    mined = [mm.claim(what_if.NEW_CLAIM_ID, "coordinate", source="realitymix", lat=50.0, lon=14.0),
             mm.claim(what_if.NEW_CLAIM_ID + 1, "precision_declaration", source="realitymix",
                      value_text="estimated")]
    declared = frozenset({"coordinate", "obec_name", "precision_declaration"})
    out = what_if.restate(today, mined, "realitymix", declared)
    assert [c.id for c in out] == [2, 4, 5, what_if.NEW_CLAIM_ID, what_if.NEW_CLAIM_ID + 1]


def test_an_intake_claim_reads_back_as_the_resolver_reads_the_stored_row():
    pin = next(c for c in _mined(CONTRACT, 11) if c.claim_type == "coordinate")
    assert pin.id >= 11 and (pin.lat, pin.lon) == (49.9638, 14.072)
    assert (pin.source, pin.surface, pin.licence_class) == ("realitymix", "archived_html", "portal")
    assert pin.observed_at == T0


def test_w4_remine_declares_the_hidden_pin_estimated(monkeypatch):
    """The W4 path over the real contract: today (v7) the marker reads `#print-map` and never
    fires; re-mined under the bump it reads the blank `#map`. Every type the bump states is
    replaced by its newer claim, and the listing of another portal is left alone."""
    today = _mined(CONTRACT, 1)
    assert "precision_declaration" not in {c.claim_type for c in today}
    scan = ScanRow(row=fx.listing("realitymix", {}, listing_id=7, native="12345"),
                   body=ArchivedPayload(id=9001, page_kind="detail", first_observed_at=T0),
                   body_unmined=False, contract_version=CONTRACT.version, snapshot_cursor=None)
    other = ScanRow(row=fx.listing("sreality", {}, listing_id=8), body=None, body_unmined=False,
                    contract_version=None, snapshot_cursor=None)
    monkeypatch.setattr(claims_intake, "scan_rows", lambda cur, ids: [scan, other])
    monkeypatch.setattr(page_readers, "load_bodies",
                        lambda cur, ids, store: ({9001: HIDDEN_PIN_PAGE}, 1))
    out = what_if.remine(_v8(), store=object())(_FakeConn(), {7: today, 8: []})
    assert out[8] == []
    declared = [c for c in out[7] if c.claim_type == "precision_declaration"]
    assert [(c.value_text, c.blur_evidence) for c in declared] == [("estimated", "declared")]
    assert all(c.id >= what_if.NEW_CLAIM_ID for c in out[7])
    assert ({c.claim_type for c in out[7]}
            == {c.claim_type for c in today} | {"precision_declaration"})


# ------------------------------------------------------------------ positions and movers


def test_a_position_row_carries_every_field_the_cohort_export_reads():
    """The override rebuilds the artifact's block from this row with `build_location`, which
    reads `COHORT_LOCATION_SQL`'s columns by name: one missing is a silent null."""
    resolution = core.resolve([mm.claim(1, "obec_name", value_text="Praha")], mm.context(),
                              resolver_version="v", registry_version="r", listing_id=7,
                              source="sreality")
    row = what_if.position_row(resolution, {resolution.granularity: (20, False)})
    assert set(re.findall(r"\bAS (\w+)", COHORT_LOCATION_SQL)) <= set(row)
    assert (row["granularity_rank"], row["is_address_grain"]) == (20, False)
    assert build_location(row)["obec_kod"] == resolution.obec_kod


def test_a_mover_is_a_changed_field_not_the_stores_rounding():
    old = {"listing_id": 7, "obec_kod": 554782, "granularity": "street", "lat": 50.1,
           "uncertainty_radius_m": Decimal("707.106781186548"), "is_address_grain": False}
    same = {**old, "uncertainty_radius_m": 707.1067811865476}
    assert not what_if.moved(old, same)
    assert what_if.moved(old, {**same, "obec_kod": 599212})
    assert what_if.moved(old, {**same, "granularity": "obec"})
    assert what_if.moved(old, {**same, "lat": None})


def test_run_writes_the_movers_and_only_the_movers(monkeypatch, tmp_path: Path):
    """A listing whose new row equals today's is not a mover; a changed one and a listing
    with no row today are, each with the old row beside the new."""
    still, moves, fresh = (core.resolve([mm.claim(1, "obec_name", value_text="Praha", listing_id=i)],
                                        mm.context(), resolver_version="v", registry_version="r",
                                        listing_id=i, source="sreality") for i in (1, 2, 3))
    rank = (20, False)
    names = sorted(re.findall(r"\bAS (\w+)", COHORT_LOCATION_SQL))

    def today(item: Any) -> dict[str, Any]:
        row = what_if.position_row(item, {item.granularity: rank})
        return {name: row.get(name) for name in names}

    stored = {1: today(still), 2: {**today(moves), "obec_kod": 599212}}
    monkeypatch.setattr(what_if, "_stored", lambda conn, ids: (names, stored))
    monkeypatch.setattr(drain, "recompute", lambda conn, ids, **kw: iter(
        [([1, 2], [still, moves]), ([4], RuntimeError("boom")), ([3], [fresh])]))
    conn = _FakeConn(rows=[(still.granularity, *rank)])
    tally = what_if.run(conn, [1, 2, 3, 4], tmp_path)
    movers = [json.loads(line) for line in (tmp_path / what_if.MOVERS_FILE).open()]
    positions = [json.loads(line) for line in (tmp_path / what_if.POSITIONS_FILE).open()]
    assert [(m["listing_id"], m["old"] and m["old"]["obec_kod"]) for m in movers] == [
        (2, 599212), (3, None)]
    assert [p["listing_id"] for p in positions] == [2, 3]
    assert positions[0] == movers[0]["new"] and set(positions[0]) == set(names)
    assert tally["sreality"]["movers"] == 2 and tally["*"]["failed"] == 1


def test_the_dry_run_and_the_shadow_share_one_read_path():
    """`recompute` is the one prefetch -> change -> warm -> four steps loop; the dry run is a
    caller of it, not a second copy."""
    import inspect

    assert "recompute(" in inspect.getsource(drain.dry_run)
    assert "_prefetch(" not in inspect.getsource(drain.dry_run)
    body = inspect.getsource(drain.recompute)
    assert body.index("_prefetch(") < body.index("claim_change(") < body.index("_warm(")


# ------------------------------------------------------------------ fakes


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class _FakeConn:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []

    @contextmanager
    def transaction(self):
        yield self

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.rows)
