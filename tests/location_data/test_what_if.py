"""The wave shadow's first step (`location_data.what_if`): a wave's positions, without a write.

Hermetic: the claim changes are pure or run over a fake connection, the W4 re-mine reads the real
git contract against a page in the shape realitymix serves, and nothing here reaches a database.
"""

from __future__ import annotations

import ast
import gzip
import inspect
import json
import re
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from autodedup import export, labels_lane, shadow
from autodedup.export import build_location
from autodedup.export_sql import COHORT_LOCATION_SQL
from location_data import claims_intake, contracts, page_readers, what_if
from location_data.claims_intake import ScanRow
from location_data.page_readers import ArchivedPayload
from location_data.resolver import core, drain, resolve_db
from tests.location_data import claim_intake_fixtures as fx
from tests.location_data import mini_mirror as mm

T0 = datetime(2026, 10, 1, tzinfo=UTC)
CONTRACT = next(c for c in contracts.load_all() if c.source == "realitymix")
NAMES = re.findall(r"\bAS (\w+)", COHORT_LOCATION_SQL)

# The shape W4 is about: the server's geocode on `#print-map`, the interactive `#map` blank.
HIDDEN_PIN_PAGE = b"""<html><head>
<link rel="canonical" href="https://realitymix.cz/detail/beroun/byt-12345.html">
</head><body>
<div id="print-map" data-gps-lat="49.9638" data-gps-lon="14.0720" data-address="Beroun"></div>
<div id="map" data-gps-lat="" data-gps-lon="" data-address="Beroun"></div>
</body></html>"""


def _at(claim: Any, days: int) -> Any:
    return replace(claim, observed_at=T0 + timedelta(days=days))


def _marker_reads(css: str, version: int) -> contracts.PortalContract:
    """The shipped contract with the `estimated` marker re-pointed, so the test states both sides
    of W4 itself and never depends on which version the checkout ships."""
    entries = [replace(e, locator={**e.locator, "css": css})
               if e.entry_id == "rm.det.agency_est_flag" else e for e in CONTRACT.entries]
    return replace(CONTRACT, version=version, entries=entries)


def _v7() -> contracts.PortalContract:
    """Before W4: the marker read the print node first, which always carries a value."""
    return _marker_reads("div#print-map, div#map", CONTRACT.version)


def _v8() -> contracts.PortalContract:
    """W4's bump as a contract value: the marker reads the interactive map only."""
    return _marker_reads("div#map", CONTRACT.version + 1)


def _mined(contract: contracts.PortalContract, first_id: int) -> list[Any]:
    row = fx.listing("realitymix", {}, listing_id=7, native="12345")
    payload = ArchivedPayload(id=9001, page_kind="detail", first_observed_at=T0,
                              body=HIDDEN_PIN_PAGE)
    register = page_readers.ScopeRegister.from_zones(contract.source, contract.exclusion_zones)
    result = page_readers.extract_page(payload, row, contracts.entries_of(contract),
                                       register=register)
    return [resolve_db.claim_from_intake(c, first_id + n) for n, c in enumerate(result.claims)]


def _scan(listing_id: int, body_id: int | None, source: str = "realitymix") -> ScanRow:
    body = None if body_id is None else ArchivedPayload(id=body_id, page_kind="detail",
                                                        first_observed_at=T0)
    return ScanRow(row=fx.listing(source, {}, listing_id=listing_id, native="12345"), body=body,
                   body_unmined=False, contract_version=CONTRACT.version, snapshot_cursor=None)


def _resolved(listing_id: int) -> Any:
    return core.resolve([mm.claim(1, "obec_name", value_text="Praha", listing_id=listing_id)],
                        mm.context(), resolver_version="v", registry_version="r",
                        listing_id=listing_id, source="sreality")


# ------------------------------------------------------------------ the claim changes


def test_purge_keeps_one_claim_per_type_and_author_the_newest_observation():
    """W3's keeper: the newest observation of today's read (the id breaks a tie); the
    operator's claim is its own key."""
    old_pin = _at(mm.claim(1, "coordinate", lat=50.0, lon=14.0), 0)
    new_pin = _at(mm.claim(2, "coordinate", lat=50.1, lon=14.1), 1)
    first_town, second_town = (_at(mm.claim(i, "obec_name", value_text="Praha"), 0)
                               for i in (3, 4))
    operator = _at(mm.claim(5, "obec_name", value_text="Brno", licence_class="operator"), 0)
    out = what_if.purge(None, {7: [new_pin, old_pin, second_town, first_town, operator], 8: []})
    assert [c.id for c in out[7]] == [2, 4, 5]
    assert out[8] == []


def test_a_remine_restates_the_admissible_types_it_mined_and_keeps_the_rest():
    """Per type the newest version (the re-mine's where it stated the type); a type the contract
    stopped declaring is not read; operator and other-portal claims stay; an inadmissible
    licence (`odbl`, a geocode) is never read, so it restates nothing."""
    today = [
        mm.claim(1, "coordinate", source="realitymix", lat=50.0, lon=14.0),
        mm.claim(2, "obec_name", source="realitymix", value_text="Beroun"),
        mm.claim(3, "street_name", source="realitymix", value_text="Husova"),
        mm.claim(4, "obec_name", source="realitymix", value_text="Beroun",
                 licence_class="operator"),
        mm.claim(5, "obec_name", source="sreality", value_text="Beroun"),
        mm.claim(6, "psc", source="realitymix", value_text="26601"),
    ]
    new = what_if.NEW_CLAIM_ID
    mined = [mm.claim(new, "coordinate", source="realitymix", lat=50.0, lon=14.0),
             mm.claim(new + 1, "precision_declaration", source="realitymix",
                      value_text="estimated"),
             mm.claim(new + 2, "psc", source="realitymix", value_text="26601",
                      licence_class="odbl")]
    declared = frozenset({"coordinate", "obec_name", "precision_declaration", "psc"})
    out = what_if.restate(today, mined, "realitymix", declared)
    assert [c.id for c in out] == [2, 4, 5, 6, new, new + 1]


def test_restate_is_pinned_to_the_claim_reads_invariants():
    """`restate` re-states `resolve_db._claims_sql` in Python; when the read changes, this fails
    and `restate` changes with it (both go when W3 replaces the read)."""
    sql = " ".join(resolve_db._CLAIMS_BULK_SQL.split())
    for invariant in (
        "max(pc.version) OVER (PARTITION BY c.listing_id, c.source, c.claim_type)",  # per type
        "pc.version <= act.version",  # never past the active contract
        "contract_version IS NULL OR contract_version = newest_version",  # newest wins
        "ace.claim_type = c.claim_type",  # a type the active contract declares
        "c.contract_entry_id IS NULL AND c.licence_class = 'operator'",  # operator kept
        f"c.licence_class IN {resolve_db._ADMISSIBLE_LICENCE_CLASSES}",
    ):
        assert invariant in sql, invariant
    assert resolve_db.ADMISSIBLE_LICENCE_CLASSES == ("portal", "operator")


def test_an_intake_claim_reads_back_as_the_resolver_reads_the_stored_row():
    pin = next(c for c in _mined(CONTRACT, 11) if c.claim_type == "coordinate")
    assert pin.id >= 11 and (pin.lat, pin.lon) == (49.9638, 14.072)
    assert (pin.source, pin.surface, pin.licence_class) == ("realitymix", "archived_html", "portal")
    assert pin.observed_at == T0


def test_w4_remine_declares_the_hidden_pin_estimated(monkeypatch):
    """The W4 path over the real contract: today (v7) the marker reads `#print-map` and never
    fires; re-mined under the bump it reads the blank `#map`. Every type the bump states is
    replaced by its newer claim, and another portal's listing is left alone."""
    today = _mined(_v7(), 1)
    assert "precision_declaration" not in {c.claim_type for c in today}
    monkeypatch.setattr(claims_intake, "scan_rows",
                        lambda cur, ids: [_scan(7, 9001), _scan(8, None, "sreality")])
    monkeypatch.setattr(page_readers, "load_bodies",
                        lambda cur, ids, store: ({9001: HIDDEN_PIN_PAGE}, 1))
    unmined: set[int] = set()
    out = what_if.remine(_v8(), object(), unmined)(_FakeConn(), {7: today, 8: []})
    assert out[8] == [] and unmined == set()
    declared = [c for c in out[7] if c.claim_type == "precision_declaration"]
    assert [(c.value_text, c.blur_evidence) for c in declared] == [("estimated", "declared")]
    assert all(c.id >= what_if.NEW_CLAIM_ID for c in out[7])
    assert ({c.claim_type for c in out[7]}
            == {c.claim_type for c in today} | {"precision_declaration"})


def test_a_page_that_would_not_remine_is_counted_not_hidden(monkeypatch):
    """A stored body the bucket did not serve keeps today's page claims (as the lane would leave
    it unstamped) and joins `unmined`, which makes the run exit 1; no stored body is no failure."""
    today = _mined(_v7(), 1)
    monkeypatch.setattr(claims_intake, "scan_rows",
                        lambda cur, ids: [_scan(7, 9001), _scan(9, None)])
    monkeypatch.setattr(page_readers, "load_bodies", lambda cur, ids, store: ({}, 1))
    unmined: set[int] = set()
    out = what_if.remine(_v8(), object(), unmined)(_FakeConn(), {7: today, 9: []})
    assert unmined == {7}
    assert out[7] == today


# ------------------------------------------------------------------ positions and movers


def test_a_position_row_carries_every_field_the_cohort_export_reads():
    """The override rebuilds the artifact's block from this row with `build_location`, which
    reads `COHORT_LOCATION_SQL`'s columns by name: one missing is a silent null."""
    resolution = _resolved(7)
    row = what_if.position_row(resolution, {resolution.granularity: (20, False)})
    assert set(NAMES) <= set(row)
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
    with no row today are, each with the old row beside the new; a failed page is counted."""
    still, moves, fresh = (_resolved(i) for i in (1, 2, 3))
    rank = (20, False)

    def today(item: Any) -> tuple[Any, ...]:
        row = what_if.position_row(item, {item.granularity: rank})
        return tuple(row.get(name) for name in NAMES)

    stored = [today(still), (2, 599212, *today(moves)[2:])]
    reads = {what_if._RANK_SQL: ([], [(still.granularity, *rank)]),
             COHORT_LOCATION_SQL: (NAMES, stored)}
    monkeypatch.setattr(what_if, "read", lambda conn, sql, params=None, **kw: reads[sql])
    monkeypatch.setattr(drain, "recompute", lambda conn, ids, **kw: iter(
        [([1, 2], [still, moves]), ([4], RuntimeError("boom")), ([3], [fresh])]))
    counts = what_if.run(None, [1, 2, 3, 4], tmp_path)
    movers = [json.loads(line) for line in (tmp_path / shadow.MOVERS_FILE).open()]
    positions = [json.loads(line) for line in (tmp_path / shadow.POSITIONS_FILE).open()]
    assert [(m["listing_id"], m["old"] and m["old"]["obec_kod"]) for m in movers] == [
        (2, 599212), (3, None)]
    assert [p["listing_id"] for p in positions] == [2, 3]
    assert positions[0] == movers[0]["new"] and list(positions[0]) == sorted(NAMES)
    assert counts["sreality"]["movers"] == 2 and counts["*"]["failed"] == 1
    assert counts["sreality"]["town"] == 2  # 2 moved town, 3 had none: drain.tally by name


def test_an_operators_id_select_runs_as_one_wrapped_read_only_statement():
    """The workflow's free-text SELECT: wrapped as a subquery (no second statement, no
    data-modifying CTE), sent on the extended protocol, inside the read path's transaction, under
    its own statement budget (`SET LOCAL`'s bindable spelling, gone with that transaction)."""
    conn = _FakeConn(rows=[(3,), (1,)])
    assert what_if.select_ids(conn, " SELECT id FROM listings WHERE source LIKE 'r%';\n",
                              timeout_s=1200) == [3, 1]
    budget, select = conn.executed
    assert budget[:2] == ("SELECT set_config('statement_timeout', %s, true)", ("1200s",))
    assert select[0] == ("SELECT * FROM (\nSELECT id FROM listings WHERE source LIKE 'r%'\n)"
                         " AS shadow_ids")
    assert select[2] == {"binary": True} and budget[3] and select[3]


def test_an_r2_id_list_is_read_as_the_w3_dry_run_writes_it():
    """Gzipped, one listing id per line: W3's ids_sql is a full scan of 14.8 M claims, so its
    purge dry run writes the movers' ids to R2 and the shadow reads them back."""
    class Store:
        def download_bytes(self, key: str) -> bytes:
            assert key == "backups/w3-claims/2026-11-01/w3_shadow_ids_1_1.txt.gz"
            return gzip.compress(b"101\n7\n\n42\n")

    assert what_if.ids_from_r2(Store(), "backups/w3-claims/2026-11-01/w3_shadow_ids_1_1.txt.gz") \
        == [101, 7, 42]


def test_the_dry_run_and_the_shadow_share_one_read_path():
    """`recompute` is the one prefetch -> change -> warm -> four steps loop; the dry run is a
    caller of it, and both compare through `StoredRow` by name."""
    assert "recompute(" in inspect.getsource(drain.dry_run)
    assert "_prefetch(" not in inspect.getsource(drain.dry_run)
    body = inspect.getsource(drain.recompute)
    assert body.index("_prefetch(") < body.index("claim_change(") < body.index("_warm(")
    assert set(drain.StoredRow._fields) <= set(NAMES)
    assert "AS lat" in drain._SAMPLE_SQL and "AS lon" in drain._SAMPLE_SQL


def _sql_sites(module: Any) -> list[str]:
    """Every `.cursor(` / `.execute(` call in `module` not inside a `with` that opens a
    transaction (`.transaction()` or `read_cursor(...)`)."""
    tree = ast.parse(inspect.getsource(module))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def opens(item: ast.withitem) -> bool:
        call = item.context_expr
        name = getattr(call, "func", None)
        return isinstance(call, ast.Call) and (
            isinstance(name, ast.Attribute) and name.attr == "transaction"
            or isinstance(name, ast.Name) and name.id == "read_cursor")

    loose = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("cursor", "execute")):
            up = parents.get(node)
            while up is not None and not (isinstance(up, ast.With) and any(map(opens, up.items))):
                up = parents.get(up)
            if up is None:
                loose.append(f"{module.__name__}:{node.lineno}")
    return loose


def test_the_wave_shadow_reaches_sql_only_inside_a_read_only_transaction():
    """`connect_read_only` makes every `conn.transaction()` BEGIN READ ONLY and nothing else, so a
    statement outside one would not be covered: none of the shadow's SQL paths has one."""
    assert [site for module in (what_if, shadow, export, labels_lane)
            for site in _sql_sites(module)] == []
    conn = _FakeConn(rows=[(1,)])
    shadow.read(conn, "SELECT 1")
    assert [entry[3] for entry in conn.executed] == [True]


# ------------------------------------------------------------------ fakes


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self.conn = conn
        self.description = [SimpleNamespace(name="id")]

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None, **kwargs: Any) -> None:
        self.conn.executed.append((sql, params, kwargs, self.conn.depth > 0))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.conn.rows


class _FakeConn:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows, self.executed, self.depth = rows or [], [], 0

    @contextmanager
    def transaction(self):
        self.depth += 1
        try:
            yield self
        finally:
            self.depth -= 1

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)
