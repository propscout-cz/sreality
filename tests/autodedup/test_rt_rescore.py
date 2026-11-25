"""E991 — `rt_seed recut=true rescore=true` switches the generation's SETTINGS in place.

The operator, 2026-11-23: generation `rt` goes from w31 to w34 in place, never by a fresh re-seed
(the nation's rebuild takes days and re-announces). w34 moves two things a stored row cannot see:
C4c's window candidates (a pair w31's 8 % area bar vetoed AT RETRIEVAL, so no row and no ledger
entry exists for it) and K-R's structured order code (read only under a w34 cut). A re-cut alone
re-reads no advert, so the switch also opens a sweep that re-claims every in-scope advert once.
The proof: a w31 store switched in place and run to quiescence equals a fresh w34 seed's.
"""

from __future__ import annotations

import json
import random
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from autodedup import incremental_lane
from autodedup.incremental import Limits, bootstrap_key
from autodedup.incremental_lane import (
    CURSOR_RESCORE,
    RESET_CURSORS,
    SqlWork,
    rescore_key,
    reset_generation,
    run_incremental,
    run_rt_seed,
)
from autodedup.incremental_scope import Scope, ScopeBlock
from tests.autodedup import lane_world
from tests.autodedup.fake_pg import FakePg

GEN = "rt"
MODEL = "w6_gold"
C4C = (4100, 4101)
KR = (4200, 4201)


def _vector(seed: int) -> str:
    rng = random.Random(seed)
    return "[" + ",".join(f"{rng.uniform(-0.1, 0.1):.4f}" for _ in range(512)) + "]"


def _advert(conn: FakePg, listing_id: int, source: str, phashes: list[int], **columns: Any) -> None:
    lane_world._listing_row(conn, listing_id, source, phashes)
    conn.listings[listing_id].update(columns)


def _world() -> FakePg:
    """`lane_world`'s nine flats plus two pairs w34 decides and w31 cannot: 4100/4101, one 3+kk at
    60 and 68 m² (11.8 % apart) with one price and one gallery — vetoed by w31's area bar before
    it is a candidate, a window candidate under w34 (C4b + C4c); and 4200/4201, two 4+1 flats
    that share nothing but the structured order code (`IDNES-778899` / `778899`). Every photograph
    carries its pHash, CLIP vector and tags, so no merge waits on evidence (E93). 4008 is
    delisted, so the sweep reads both of its cycles (live rows first)."""
    conn = lane_world.world()
    conn.listings[4008].update(is_active=False,
                               inactive_at=lane_world.EXPORTED_AT - timedelta(days=3))
    gallery = [0x1234_5678_9ABC_0001 + 7919 * k for k in range(4)]
    _advert(conn, C4C[0], "sreality", gallery, disposition="3+kk", area_m2=60.0,
            usable_area=60.0, price_czk=7_100_000,
            description="Svetly byt 3+kk s lodzii a vyhledem do zahrady, nova kuchyne. " * 3)
    _advert(conn, C4C[1], "bezrealitky", gallery, disposition="3+kk", area_m2=68.0,
            usable_area=68.0, price_czk=7_100_000,
            description="Nabizime byt 3+kk po rekonstrukci, lodzie, kuchyne na miru. " * 3)
    _advert(conn, KR[0], "idnes", [0x0FED_CBA9_8765_0001 + 31 * k for k in range(3)],
            disposition="4+1", area_m2=95.0, usable_area=95.0, price_czk=9_900_000,
            ad_reference="IDNES-778899",
            description="Rodinny byt 4+1 se dvema koupelnami a velkou terasou, garaz. " * 3)
    _advert(conn, KR[1], "sreality", [0x0ABC_DEF0_1234_0001 + 37 * k for k in range(3)],
            disposition="4+1", area_m2=95.0, usable_area=95.0, price_czk=9_950_000,
            ad_reference="778899",
            description="Prostorny ctyrpokojovy byt, dve koupelny, terasa, garazove stani. " * 3)
    for row in conn.image_rows:
        conn.clip_vectors[int(row["image_id"])] = _vector(int(row["phash"]) % 97)
        row["clip_tagged_at"] = lane_world.EXPORTED_AT
    return conn


def _seed(conn: FakePg, tmp_path: Path, settings: str, **args: str) -> dict[str, Any]:
    return run_rt_seed(lambda: conn, {"settings": settings, "model": MODEL,
                                      "rt_scope": lane_world.SCOPE, **args}, tmp_path)


def _quiesce(conn: FakePg, keys: set[tuple[str, int]] | None = None) -> list[dict[str, Any]]:
    """Passes until the build is over, no rescore is owed and a pass claims nothing; with `keys`,
    every `rt_fp` row named there must still be there after each pass (the publication gate's
    "read by the engine": the switch never takes one away)."""
    out: list[dict[str, Any]] = []
    for _ in range(60):
        out.append(run_incremental(lambda: conn))
        assert not out[-1]["aborted"], out[-1]
        if keys is not None:
            assert keys <= set(conn.rt_fp), "an rt_fp row went"
        rescore = conn.settings.get(rescore_key(GEN))
        if (conn.settings.get(bootstrap_key(GEN)) is False
                and (not isinstance(rescore, dict) or rescore.get("done"))
                and not out[-1]["counts"].get("claimed")):
            return out
    raise AssertionError("the lane never went quiet")


def _pairs(conn: FakePg) -> dict[tuple[int, int], dict[str, Any]]:
    return {(lo, hi): dict(row) for (g, lo, hi), row in conn.pairs.items() if g == GEN}


def _decisions(conn: FakePg) -> dict[tuple[int, int], dict[str, Any]]:
    """`_pairs` without the census context a pass stamps beside a decision."""
    return {key: {name: value for name, value in row.items() if name != "context"}
            for key, row in _pairs(conn).items()}


def _groups(conn: FakePg) -> list[list[int]]:
    groups: dict[int, set[int]] = {}
    for g, key, listing_id in conn.cluster_members:
        if g == GEN:
            groups.setdefault(key, set()).add(listing_id)
    return sorted(sorted(members) for members in groups.values())


def _fingerprints(conn: FakePg) -> dict[int, dict[str, Any]]:
    """`rt_fp` as the decisions read it: every column but the two clocks."""
    return {i: {name: value for name, value in row.items()
                if name not in ("updated_at", "first_decided_at")}
            for (g, i), row in conn.rt_fp.items() if g == GEN}


def _scorer(conn: FakePg) -> tuple[dict[str, Any], str]:
    """The settings and the model the generation's calibration row names (what a pass reads)."""
    row = conn.calibration[GEN]
    return json.loads(row["settings"]), str(row["model_version"])


def _fresh_w34(tmp_path: Path) -> FakePg:
    conn = _world()
    _seed(conn, tmp_path, "w34")
    _quiesce(conn)
    return conn


def _built_w31(tmp_path: Path) -> FakePg:
    conn = _world()
    _seed(conn, tmp_path, "w31")
    _quiesce(conn)
    # The switch happens later than the build, as in production: every row was refreshed before it.
    conn.now += timedelta(hours=1)
    return conn


# ------------------------------------------------------------------ the equivalence


def test_the_world_holds_one_pair_only_c4c_finds_and_one_only_the_structured_code_certifies(
        tmp_path: Path) -> None:
    w31, w34 = _built_w31(tmp_path), _fresh_w34(tmp_path)
    assert C4C not in _pairs(w31), "w31's area bar vetoes it before it is a candidate"
    assert _pairs(w34)[C4C]["zone"] in ("merge", "band")
    assert _pairs(w31)[KR]["certificate"] is None and _pairs(w31)[KR]["zone"] == "reject"
    assert (_pairs(w34)[KR]["certificate"], _pairs(w34)[KR]["zone"]) == ("K-R", "merge")


@pytest.mark.parametrize("max_listings", [500, 4])
def test_a_w31_store_switched_in_place_ends_as_a_fresh_w34_seed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_listings: int) -> None:
    """At a claim of 4 the sweep takes several passes, its cursor stopping at the last advert
    each one took; at 500 one pass. Either way every in-scope advert is re-claimed once, and the
    pairs (every column, cluster keys too), groups, fingerprints, postings, census cells and the calibration equal
    a fresh w34 seed's. The reference is built at the full claim: a build of 4 adverts a pass
    stamps on an early pair the census cell as it stood then (`context.cell_n` 8 of 13), which
    no later pass rewrites; its decisions are the same, and so is the switch's (below)."""
    fresh = _fresh_w34(tmp_path)
    monkeypatch.setattr(incremental_lane, "PASS_LIMITS",
                        Limits(max_listings=max_listings, max_component=400))
    small = _fresh_w34(tmp_path)
    conn = _built_w31(tmp_path)
    held = {key for key in conn.rt_fp if key[0] == GEN}
    before_pairs = set(_pairs(conn))
    out = _seed(conn, tmp_path, "w34", recut="true", rescore="true")

    assert out["mode"] == "rescore" and out["fresh"] is False and out["settings_name"] == "w34"
    record = out["widening"]["rescore"]
    assert record["dials"] == ["area_veto_basis_floor_exempt", "area_veto_gallery_exempt",
                               "ref_code_structured_categories"]
    assert record["since"] == conn.now.isoformat()
    assert conn.settings[rescore_key(GEN)] == {**record}
    assert conn.cursors[CURSOR_RESCORE]["last_listing_id"] == 0
    # The seed itself writes no decision and takes away no row: the switch is the cut, the
    # settings on the generation, the sweep's record.
    assert held == {key for key in conn.rt_fp if key[0] == GEN}
    assert set(_pairs(conn)) == before_pairs
    assert _scorer(conn)[0]["ref_code_structured_categories"] == ["byt", "dum", "pozemek"]
    assert _scorer(conn)[1] == MODEL

    conn.now += timedelta(minutes=10)
    passes = _quiesce(conn, keys=held)
    taken = sum((p.get("rescore") or {}).get("taken", 0) for p in passes)
    assert taken == sum(p["feeds"].get("rescore", 0) for p in passes) == len(held), (
        "every in-scope advert re-claimed exactly once")
    assert conn.settings[rescore_key(GEN)]["done"] is True
    assert conn.cursors[CURSOR_RESCORE]["last_listing_id"] == 0
    if max_listings == 4:
        assert sum(1 for p in passes if (p.get("rescore") or {}).get("taken")) >= 3
    assert passes[-1]["rescore"] is None, "a done sweep reads no slice"

    assert _pairs(conn) == _pairs(fresh)
    assert _decisions(conn) == _decisions(small) and _groups(small) == _groups(fresh)
    assert _groups(conn) == _groups(fresh) and list(KR) in _groups(fresh)
    assert _fingerprints(conn) == _fingerprints(fresh)
    assert {row for row in conn.fp_key if row[0] == GEN} == {
        row for row in fresh.fp_key if row[0] == GEN}
    assert conn.cells == fresh.cells
    assert conn.calibration[GEN]["digest"] == fresh.calibration[GEN]["digest"]
    assert _scorer(conn) == _scorer(fresh)


def test_without_the_sweep_the_switch_never_finds_the_window_pair_or_the_code(
        tmp_path: Path) -> None:
    """Why the sweep exists: under w34 an advert nothing re-claims keeps its w31 retrieval and
    its w31 decisions — the re-cut and the moved engine digest re-read none of them."""
    conn = _built_w31(tmp_path)
    _seed(conn, tmp_path, "w34", recut="true", rescore="true")
    del conn.settings[rescore_key(GEN)]
    _quiesce(conn)
    assert C4C not in _pairs(conn)
    assert _pairs(conn)[KR]["certificate"] is None
    assert _pairs(conn) != _pairs(_fresh_w34(tmp_path))


# ------------------------------------------------------------------ the sweep's slices


def _sweep_world() -> FakePg:
    """Sixteen `rt_fp` rows: 1–12 live, 13–16 delisted; 7 and 15 out of scope, 11 and 16
    refreshed after the switch, every other row before it."""
    db = FakePg()
    before, after = db.now - timedelta(hours=2), db.now + timedelta(minutes=1)
    for listing_id in range(1, 17):
        db.locations[listing_id] = {"obec_kod": 577626 if listing_id in (7, 15) else 563510}
        db.rt_fp[(GEN, listing_id)] = {
            "is_active": listing_id <= 12, "first_decided_at": before, "ev_complete": True,
            "block_key": None, "updated_at": after if listing_id in (11, 16) else before}
    return db


LIVE_OWED = [1, 2, 3, 4, 5, 6, 8, 9, 10, 12]
REST_OWED = [13, 14]


def test_the_sweep_reads_the_live_rows_then_the_rest_in_slices_and_ends_once() -> None:
    db = _sweep_world()
    record = {"since": db.now.isoformat(), "settings": "w34"}
    db.settings[rescore_key(GEN)] = dict(record)
    scope = Scope((ScopeBlock("obec", 563510),))
    claimed: list[int] = []
    phases: list[Any] = []
    for _ in range(20):
        work = SqlWork(db, scope, GEN, straggler_window=0, revive_slice=0, drift_slice=0,
                       enter_slice=0, evidence_slice=0, rescore=db.settings[rescore_key(GEN)],
                       rescore_slice=4)
        if work.rescore is None:
            break
        items = work.claim(3)
        assert all(item.feed == "rescore" and item.redecide for item in items)
        assert len(items) <= 3
        claimed += [item.listing_id for item in items]
        work.commit(items)
        phases.append(db.settings[rescore_key(GEN)].get("phase"))
    assert claimed == LIVE_OWED + REST_OWED, (
        "every owed in-scope row once, the live ones first, each cycle in id order; never one "
        "out of scope, never one refreshed since")
    # Slices of 4 live rows: 1–4 (3 taken, the cursor at 3), 4–7, 8–11, then 12 short and taken
    # whole, which ends the live cycle; 13–16, then an empty slice, which ends the rest's.
    assert phases == [None, None, None, "inactive", "inactive", "inactive"]
    assert db.settings[rescore_key(GEN)] == {**record, "phase": "inactive", "done": True}
    assert db.cursors[CURSOR_RESCORE]["last_listing_id"] == 0


def test_a_sweep_whose_pass_is_refused_moves_no_cursor_and_ends_nothing() -> None:
    db = _sweep_world()
    db.settings[rescore_key(GEN)] = {"since": db.now.isoformat(), "settings": "w34"}
    work = SqlWork(db, Scope((ScopeBlock("obec", 563510),)), GEN, straggler_window=0,
                   revive_slice=0, drift_slice=0, enter_slice=0, evidence_slice=0,
                   rescore=db.settings[rescore_key(GEN)])
    items = work.claim(50)
    assert [item.listing_id for item in items] == LIVE_OWED
    assert work.rescore_report and work.rescore_report["ended"]
    work.commit(items[:-1])
    assert CURSOR_RESCORE not in db.cursors and "phase" not in db.settings[rescore_key(GEN)]


def test_no_rescore_row_or_a_done_one_reads_no_slice() -> None:
    from autodedup import incremental_sql as S

    db = _sweep_world()
    scope = Scope((ScopeBlock("obec", 563510),))
    for rescore in (None, {"since": db.now.isoformat(), "done": True}):
        work = SqlWork(db, scope, GEN, straggler_window=0, revive_slice=0, drift_slice=0,
                       enter_slice=0, evidence_slice=0, rescore=rescore)
        assert work.claim(50) == [] and work.rescore_report is None
    assert S.RT_RESCORE_SQL not in db.statements


# ------------------------------------------------------------------ what the seed refuses


def test_rescore_without_recut_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    conn = _built_w31(tmp_path)
    before = conn.snapshot()
    with pytest.raises(SystemExit, match="rescore=true switches the settings in place THROUGH "
                                         "a re-cut.*pass recut=true"):
        _seed(conn, tmp_path, "w34", rescore="true")
    assert conn.snapshot() == before


def test_rescore_with_another_model_is_a_rebuild(tmp_path: Path) -> None:
    conn = _built_w31(tmp_path)
    before = conn.snapshot()
    with pytest.raises(SystemExit, match="rescore=true switches only its SETTINGS in place.*"
                                         "a new model is a REBUILD — pass fresh=true"):
        run_rt_seed(lambda: conn, {"settings": "w34", "model": "prior", "recut": "true",
                                   "rescore": "true"}, tmp_path)
    assert conn.snapshot() == before


def test_without_rescore_another_scorer_is_refused_as_before(tmp_path: Path) -> None:
    conn = _built_w31(tmp_path)
    before = conn.snapshot()
    with pytest.raises(SystemExit) as refused:
        _seed(conn, tmp_path, "w34", recut="true")
    assert str(refused.value) == (
        "generation 'rt' is scored by model 'w6_gold' and its own settings; this seed names "
        "another scorer. A widening keeps the scorer every stored decision was taken under — a "
        "new scorer is a REBUILD: pass fresh=true. Nothing was written.")
    assert conn.snapshot() == before


def test_rescore_beside_a_wider_scope_the_same_settings_or_fresh_is_refused(
        tmp_path: Path) -> None:
    conn = _built_w31(tmp_path)
    lane_world._listing_row(conn, 5000, "sreality", [123])
    conn.locations[5000]["obec_kod"] = 577626
    before = conn.snapshot()
    with pytest.raises(SystemExit, match="widens it too. One thing at a time"):
        run_rt_seed(lambda: conn, {"settings": "w34", "model": MODEL, "recut": "true",
                                   "rescore": "true",
                                   "rt_scope": f"{lane_world.SCOPE} obec:577626"}, tmp_path)
    with pytest.raises(SystemExit, match="already scored by: nothing to switch"):
        _seed(conn, tmp_path, "w31", recut="true", rescore="true")
    with pytest.raises(SystemExit, match="a first or fresh seed is scored by the settings"):
        _seed(conn, tmp_path, "w34", recut="true", rescore="true", fresh="true")
    assert conn.snapshot() == before
    with pytest.raises(SystemExit, match="a first or fresh seed is scored by the settings"):
        _seed(_world(), tmp_path, "w34", recut="true", rescore="true")


def test_a_fresh_seed_forgets_the_rescore_and_its_cursor(tmp_path: Path) -> None:
    conn = _built_w31(tmp_path)
    _seed(conn, tmp_path, "w34", recut="true", rescore="true")
    assert CURSOR_RESCORE in RESET_CURSORS
    reset_generation(conn, GEN)
    assert rescore_key(GEN) not in conn.settings and CURSOR_RESCORE not in conn.cursors


def test_the_sweep_leaves_the_stale_re_claims_their_room() -> None:
    """#2056 review, finding 4: the stale re-claims ride on the same pass, so the sweep's room
    is what the limit leaves after them; the pass holds no more than its limit."""
    db = _sweep_world()
    db.settings[rescore_key(GEN)] = {"since": db.now.isoformat(), "settings": "w34"}
    work = SqlWork(db, Scope((ScopeBlock("obec", 563510),)), GEN, straggler_window=0,
                   revive_slice=0, drift_slice=0, enter_slice=0, evidence_slice=0,
                   rescore=db.settings[rescore_key(GEN)])
    work.stale_claims = [101, 102, 103]
    items = work.claim(8)
    assert [item.listing_id for item in items if item.feed == "rescore"] == LIVE_OWED[:5]
    assert [item.listing_id for item in items if item.feed == "stale"] == [101, 102, 103]
    assert len(items) == 8


RM = (4300, 4301)
NEW_COLUMNS = ("source", "area_basis", "block_key", "ref_codes")


def _with_a_realitymix_floor_pair(conn: FakePg) -> FakePg:
    """4300/4301: one 2+1 at 88 m² of realitymix's total floor area and 100 m² usable on
    sreality (12 % apart), one price and one gallery. w31 vetoes it on area at retrieval; under
    w34 the floor-basis exemption reads the STORED `source` and `area_basis` of the neighbour."""
    gallery = [0x7777_1234_9ABC_0001 + 104729 * k for k in range(4)]
    _advert(conn, RM[0], "realitymix", gallery, disposition="2+1", area_m2=88.0,
            usable_area=88.0, area_basis="floor", price_czk=5_300_000,
            description="Byt 2+1 s balkonem a sklepem, puvodni stav, klidna ulice. " * 3)
    _advert(conn, RM[1], "sreality", gallery, disposition="2+1", area_m2=100.0,
            usable_area=100.0, price_czk=5_300_000,
            description="Prodej bytu 2+1, balkon, sklep, dobra dostupnost do centra. " * 3)
    for row in conn.image_rows:
        conn.clip_vectors[int(row["image_id"])] = _vector(int(row["phash"]) % 97)
        row["clip_tagged_at"] = lane_world.EXPORTED_AT
    return conn


@pytest.mark.parametrize("max_listings", [500, 3])
def test_rows_main_wrote_with_none_of_the_new_columns_end_as_a_fresh_w34_seed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_listings: int) -> None:
    """#2056 review, (e): production's rows before the switch hold NULL in every column
    migration 769 adds, and during the sweep a refreshed advert sits beside neighbours that
    still do. The end state is the fresh seed's all the same, the realitymix floor pair and
    the order-code pair included, and no row keeps a NULL."""
    fresh = _with_a_realitymix_floor_pair(_world())
    _seed(fresh, tmp_path, "w34")
    _quiesce(fresh)
    assert RM in _pairs(fresh), "the exemption seats the pair under w34"

    conn = _with_a_realitymix_floor_pair(_world())
    _seed(conn, tmp_path, "w31")
    _quiesce(conn)
    assert RM not in _pairs(conn), "w31's area bar vetoes it before it is a candidate"
    for key, row in conn.rt_fp.items():
        if key[0] == GEN:
            row.update(dict.fromkeys(NEW_COLUMNS))
    conn.now += timedelta(hours=1)
    held = {key for key in conn.rt_fp if key[0] == GEN}
    _seed(conn, tmp_path, "w34", recut="true", rescore="true")
    conn.now += timedelta(minutes=10)
    # At 3 adverts a pass the sweep takes several passes, and each decides its adverts beside
    # neighbours whose stored columns are still NULL.
    monkeypatch.setattr(incremental_lane, "PASS_LIMITS",
                        Limits(max_listings=max_listings, max_component=400))
    passes = _quiesce(conn, keys=held)
    if max_listings == 3:
        assert sum(1 for p in passes if (p.get("rescore") or {}).get("taken")) >= 4

    assert _decisions(conn) == _decisions(fresh)
    assert _groups(conn) == _groups(fresh) and list(KR) in _groups(conn)
    assert _fingerprints(conn) == _fingerprints(fresh)
    assert all(row["source"] is not None and row["block_key"] is not None
               for (g, _), row in conn.rt_fp.items() if g == GEN)
    assert conn.rt_fp[(GEN, KR[0])]["ref_codes"] == ["778899"]


def test_a_w31_cut_is_written_without_the_keys_main_does_not_know(tmp_path: Path) -> None:
    """#2056 review, finding 5: main refuses a settings key it does not know. A generation
    switched BACK to w31 in place stores none of the four, so a reverted worker reads it; a w34
    cut names every one it sets, and both read back as the row that wrote them."""
    from autodedup.incremental_lane import SETTINGS_KEYS_SINCE_W31, stored_settings
    from autodedup.settings import Settings

    conn = _fresh_w34(tmp_path)
    stored = json.loads(conn.calibration[GEN]["settings"])
    assert {"area_veto_gallery_exempt", "area_veto_basis_floor_exempt",
            "ref_code_structured_categories"} <= set(stored)
    conn.now += timedelta(hours=1)
    _seed(conn, tmp_path, "w31", recut="true", rescore="true")
    stored = json.loads(conn.calibration[GEN]["settings"])
    assert not set(SETTINGS_KEYS_SINCE_W31) & set(stored)
    w31 = Settings.from_json(Path(incremental_lane.__file__).parent / "settings/w31.json")
    assert Settings.from_dict(stored) == w31 == Settings.from_dict(stored_settings(w31))
    conn.now += timedelta(minutes=10)
    _quiesce(conn)
    assert _pairs(conn)[KR]["certificate"] is None, "back on w31 the column certifies nothing"


def test_a_stale_queue_as_long_as_the_limit_still_lets_the_sweep_take_one() -> None:
    """The delta review's blocker: room 0 with rows owed read `taken[-1]` of an empty list. The
    sweep takes one advert, its cursor stops there, and the next pass goes on from it."""
    db = _sweep_world()
    db.settings[rescore_key(GEN)] = {"since": db.now.isoformat(), "settings": "w34"}
    scope = Scope((ScopeBlock("obec", 563510),))
    seen: list[int] = []
    for _ in range(3):
        work = SqlWork(db, scope, GEN, straggler_window=0, revive_slice=0, drift_slice=0,
                       enter_slice=0, evidence_slice=0, rescore=db.settings[rescore_key(GEN)])
        work.stale_claims = list(range(101, 109))
        items = work.claim(8)
        swept = [item.listing_id for item in items if item.feed == "rescore"]
        assert len(swept) == 1 and len(items) == 9
        seen += swept
        work.commit(items)
        assert db.cursors[CURSOR_RESCORE]["last_listing_id"] == swept[0]
    assert seen == LIVE_OWED[:3]
