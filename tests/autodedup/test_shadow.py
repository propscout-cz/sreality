"""The wave shadow's engine half: the export step, the override and the gate (`harness shadow`).

Hermetic: the export step runs over stubbed exporters and fake connections, the gate over the
engine e2e's synthetic cohort, so nothing here reaches a database.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import io
import json
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autodedup import harness, shadow
from autodedup.evaluate import RunView
from autodedup.export import build_location
from autodedup.settings import Settings
from tests.autodedup.test_engine_e2e import (
    DUP_A,
    DUP_B,
    REPOST_A,
    REPOST_B,
    _listing,
    _location,
    build_records,
)

PRAHA, BRNO, BEROUN = 554782, 582786, 531057
# Two ads one address and one set of attributes make the same: no photo, no text, no broker, so
# location is their only evidence (they merge only under a scorer that waives the unit gate).
TWIN_A, TWIN_B = 9001, 9002

FAR = {"listing_id": DUP_B, "obec_kod": PRAHA, "obec_name": "Praha", "cast_obce_kod": None,
       "cast_obce_name": None, "granularity": "obec", "granularity_rank": 20,
       "is_address_grain": False, "lat": 50.08, "lon": 14.42, "uncertainty_radius_m": 1000.0,
       "street_name": None, "house_number_cp": None, "house_number_co": None, "psc": None,
       "ruian_adm_kod": None, "country_status": "cz"}


@pytest.fixture(autouse=True)
def _no_artifact_after_a_stubbed_export(monkeypatch):
    """The export is stubbed across this file, so no artifact exists for the crossing read to open."""
    monkeypatch.setattr(shadow, "_cohort_ids", lambda path: set())


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _cohort(path: Path, extra: tuple[dict[str, Any], ...] = ()) -> Path:
    records = [*build_records(), *extra]
    home = next(r for r in records if r["t"] == "listing" and r["id"] == DUP_A)["location"]
    records += [_listing(i, "turnov", source=src, location=dict(home), disposition="2+1",
                         area_m2=55.0, floor=2, total_floors=4, price=4_200_000)
                for i, src in ((TWIN_A, "sreality"), (TWIN_B, "idnes"))]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def _same(lo: int, hi: int, on: str = "2026-10-28") -> dict[str, Any]:
    return {"listing_lo": lo, "listing_hi": hi, "verdict": "same", "decided_at": on}


# ------------------------------------------------------------------ step 2: the export


def test_blocks_are_each_movers_old_and_new_block_at_the_engines_grain():
    """E300's grain: a quarter in a split city, the town elsewhere. A split-city side with no
    quarter would mean exporting the whole city, so its mover rides ONE `ids` block with every
    ad a ruling or its property ties to it (W5): both arms then hold the pair."""
    movers = [
        {"listing_id": 1, "old": {"obec_kod": PRAHA, "cast_obce_kod": 490245},
         "new": {"obec_kod": PRAHA, "cast_obce_kod": 400000}},
        {"listing_id": 2, "old": {"obec_kod": BEROUN, "cast_obce_kod": 12},
         "new": {"obec_kod": BRNO, "cast_obce_kod": None}},
        {"listing_id": 3, "old": None, "new": {"obec_kod": BEROUN, "cast_obce_kod": 13}},
        {"listing_id": 4, "old": {"obec_kod": None},
         "new": {"obec_kod": PRAHA, "cast_obce_kod": 490245}},
        {"listing_id": 5, "old": {"obec_kod": PRAHA, "cast_obce_kod": None},
         "new": {"obec_kod": PRAHA, "cast_obce_kod": 400000}},
    ]
    asked: list[list[int]] = []
    blocks = shadow.blocks_for(movers, lambda ids: asked.append(ids) or [7, 2])
    assert blocks == ["quarter:490245", "quarter:400000", f"town:{BEROUN}", "ids:2+5+7"]
    assert asked == [[2, 5]]
    assert shadow.blocks_for(movers[:1], lambda ids: pytest.fail("asked")) == [
        "quarter:490245", "quarter:400000"]


def test_the_ids_block_is_exported_as_listed_and_its_ties_are_read_only(monkeypatch):
    """`ids:` round-trips through the export's own parser and selection, and `tied_ads` reads
    the pair and group verdicts and the property siblings in ONE read-only statement."""
    from autodedup.cohort import ids_block, parse_blocks
    from autodedup.export import fetch_block_ids

    (block,) = parse_blocks(ids_block([9, 3, 3]))
    assert (block.key, block.grain, block.code, block.ids) == ("ids", "ids", 2, (3, 9))
    assert fetch_block_ids(None, block, timeout_ms=1, negctl_max=1) == ([3, 9], {})
    conn = _Conn([(11,), (None,), (12,)])
    assert shadow.tied_ads(conn, [5, 4, 5]) == [11, 12]
    sql, params, in_transaction = conn.executed[0]
    assert params == {"ids": [4, 5]} and in_transaction
    assert all(table in sql for table in ("autodedup.verdicts", "autodedup.must_not_link",
                                          "autodedup.operator_merges", "property_id"))
    monkeypatch.setattr(shadow, "fetch_block_ids",
                        lambda conn, block, **_: (list(block.ids) or [1, 2, 3], {}))
    sizes = shadow.block_sizes(None, [f"town:{BEROUN}", ids_block([3, 9])])
    assert sizes == {f"town:{BEROUN}": 3, "ids:3+9": 2}   # keyed by the block as written


def test_the_export_step_calls_the_exporters_in_process_and_reads_the_live_scorer(
        monkeypatch, tmp_path: Path):
    called: dict[str, Any] = {}
    monkeypatch.setattr(shadow, "run_export", lambda factory, args, out: called.update(
        export=(args, Path(out))) or {"counts": {"listings": 3}})
    monkeypatch.setattr(shadow, "run_labels", lambda factory, args, out: called.update(
        labels=(args, Path(out))) or {"counts": {"labels": 2}})
    live = json.loads(json.dumps(Settings().to_dict()))  # as the jsonb column reads back
    summary = shadow.export_cohort(lambda: _Conn([(json.dumps(live), "w6_gold")]), tmp_path,
                                   [f"town:{BEROUN}", "quarter:490245"])
    assert called["export"] == ({"blocks": f"town:{BEROUN} quarter:490245"}, tmp_path / "base")
    assert called["labels"] == ({}, tmp_path / "labels")
    assert summary["cohort"] == {"listings": 3}
    scorer = json.loads((tmp_path / shadow.SCORER_FILE).read_text(encoding="utf-8"))
    assert (scorer["generation"], scorer["settings"], scorer["model_version"]) == (
        "rt", live, "w6_gold")


def test_no_shadow_path_imports_the_lane_that_writes_the_ledger_row():
    """`autodedup.iterations` rows are written by `lane.run` (and `record`). The shadow reaches
    the exporters directly, and the apply scope through `apply_scope`: importing every shadow
    module loads neither the lane, nor the ledger writer, nor the apply path."""
    code = ("import sys, autodedup.shadow, autodedup.harness, location_data.what_if; "
            "print([m for m in ('autodedup.lane', 'autodedup.iterations', 'autodedup.apply') "
            "if m in sys.modules])")
    loaded = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            check=True, cwd=Path(__file__).resolve().parents[2]).stdout.strip()
    assert loaded == "[]"


def test_an_oversized_shadow_is_refused_before_anything_is_exported(monkeypatch, tmp_path,
                                                                     capsys):
    _jsonl(tmp_path / shadow.MOVERS_FILE, [
        {"old": {"obec_kod": BEROUN}, "new": {"obec_kod": PRAHA, "cast_obce_kod": 490245}}])
    monkeypatch.setattr("scraper.db.connect_read_only", lambda: _Conn([]))
    monkeypatch.setattr(shadow, "block_sizes",
                        lambda conn, blocks: {block: 15_000 for block in blocks})
    monkeypatch.setattr(shadow, "export_cohort",
                        lambda *a, **k: pytest.fail("exported past the cap"))
    assert shadow.main([str(tmp_path), "--all-blocks"]) == 2
    printed = capsys.readouterr()
    assert f"town:{BEROUN} 15000 ads" in printed.out
    assert "groups needed under the caps: 2" in printed.out
    assert "dispatch --block-group i/2 for i in 1..2" in printed.err
    assert shadow.main([str(tmp_path), "--all-blocks", "--max-ads", "1000000",
                        "--max-blocks", "1"]) == 2


def test_blocks_are_cut_into_contiguous_sorted_groups_and_n_is_the_fewest_that_fit():
    blocks = ["town:9", "quarter:2", "town:1", "quarter:1", "town:5"]
    assert shadow.chunks(blocks, 2) == [["quarter:1", "quarter:2", "town:1"], ["town:5", "town:9"]]
    assert shadow.chunks(blocks, 1) == [sorted(blocks)]
    sizes = {"town:1": 30, "town:2": 30, "town:3": 30, "town:4": 5}
    assert shadow.groups_needed(sizes, max_blocks=40, max_ads=60) == 2
    assert shadow.groups_needed(sizes, max_blocks=1, max_ads=1000) == 4
    assert shadow.groups_needed({"town:1": 70}, max_blocks=40, max_ads=60) is None
    assert shadow.parse_group("2/3") == (2, 3)
    for bad in ("0/3", "4/3", "1", "a/b"):
        with pytest.raises(argparse.ArgumentTypeError):
            shadow.parse_group(bad)


def test_a_group_exports_only_its_chunk_and_records_it(monkeypatch, tmp_path: Path):
    """The block list comes from ALL movers; group 2/2 exports the second contiguous chunk, under
    the caps applied to that chunk alone."""
    _jsonl(tmp_path / shadow.MOVERS_FILE, [
        {"old": {"obec_kod": 1}, "new": {"obec_kod": 2}},
        {"old": {"obec_kod": 3}, "new": {"obec_kod": 4}}])
    monkeypatch.setattr("scraper.db.connect_read_only", lambda: _Conn([]))
    monkeypatch.setattr(shadow, "block_sizes", lambda conn, blocks: {b: 40_000 for b in blocks})
    exported: list[list[str]] = []
    monkeypatch.setattr(shadow, "export_cohort",
                        lambda factory, out, blocks: exported.append(blocks) or {})
    assert shadow.main([str(tmp_path), "--all-blocks", "--block-group", "2/4",
                        "--max-ads", "50000"]) == 0
    assert exported == [["town:2"]]
    group = json.loads((tmp_path / shadow.GROUP_FILE).read_text(encoding="utf-8"))
    assert (group["group"], group["blocks"], group["all_blocks"]) == ("2/4", ["town:2"], 4)


def _movers_three_blocks(tmp_path: Path, monkeypatch: Any) -> list[list[str]]:
    _jsonl(tmp_path / shadow.MOVERS_FILE, [
        {"listing_id": 1, "old": {"obec_kod": 1}, "new": {"obec_kod": 1}},
        {"listing_id": 2, "old": {"obec_kod": 2}, "new": {"obec_kod": 2}},
        {"listing_id": 3, "old": {"obec_kod": 3}, "new": {"obec_kod": 3}}])
    monkeypatch.setattr("scraper.db.connect_read_only", lambda: _Conn([]))
    monkeypatch.setattr(shadow, "block_sizes",
                        lambda conn, blocks: {b: 100 * int(b.split(":")[1]) for b in blocks})
    monkeypatch.setattr(shadow, "ruled_blocks", lambda conn: {"town:1", "town:77"})
    exported: list[list[str]] = []
    monkeypatch.setattr(shadow, "export_cohort",
                        lambda factory, out, blocks: exported.append(blocks) or {})
    return exported


def test_only_the_blocks_a_ruling_is_inside_are_exported(monkeypatch, tmp_path: Path, capsys):
    """Two of three blocks hold no ruling: no ruled pair can move there, so they could add only
    D83 lines; they are skipped, counted, and their movers are unjudgeable (never unjudged)."""
    exported = _movers_three_blocks(tmp_path, monkeypatch)
    assert shadow.main([str(tmp_path)]) == 0
    assert exported == [["town:1"]]
    assert "2 blocks skipped: no ruling inside (500 ads); 2 movers unjudgeable" in (
        capsys.readouterr().out)
    group = json.loads((tmp_path / shadow.GROUP_FILE).read_text(encoding="utf-8"))
    assert group["blocks_skipped_no_ruling"] == {"blocks": 2, "ads": 500}
    assert (group["unjudgeable"], group["rulings_filter"]) == ([2, 3], True)


def test_all_blocks_restores_every_movers_block(monkeypatch, tmp_path: Path):
    exported = _movers_three_blocks(tmp_path, monkeypatch)
    monkeypatch.setattr(shadow, "ruled_blocks",
                        lambda conn: pytest.fail("--all-blocks reads no ruling"))
    assert shadow.main([str(tmp_path), "--all-blocks"]) == 0
    assert exported == [["town:1", "town:2", "town:3"]]
    group = json.loads((tmp_path / shadow.GROUP_FILE).read_text(encoding="utf-8"))
    assert group["blocks_skipped_no_ruling"] == {"blocks": 0, "ads": 0}
    assert (group["unjudgeable"], group["rulings_filter"]) == ([], False)


def test_a_mover_entering_a_ruled_block_brings_its_old_block():
    """It reaches the cohort only through its old (stored) block, so both of its blocks stay;
    a mover with no ruled side is unjudgeable."""
    movers = [{"listing_id": 1, "old": {"obec_kod": 5}, "new": {"obec_kod": 1}},
              {"listing_id": 2, "old": {"obec_kod": 6}, "new": {"obec_kod": 7}},
              {"listing_id": 3, "old": {"obec_kod": PRAHA}, "new": {"obec_kod": 1}}]
    assert shadow.judgeable(movers, {"town:1"}) == ({"town:5", "town:1"}, {2})


def test_the_ids_block_survives_the_rulings_filter_and_its_movers_are_never_unjudgeable(
        monkeypatch, tmp_path: Path):
    """A mover with an unquartered side rides the `ids` block, so the filter keeps that block and
    never counts its mover unjudgeable, even when its other side sits in a block with no ruling."""
    _jsonl(tmp_path / shadow.MOVERS_FILE, [
        {"listing_id": 1, "old": {"obec_kod": 1}, "new": {"obec_kod": 1}},
        {"listing_id": 5, "old": {"obec_kod": PRAHA, "cast_obce_kod": None},
         "new": {"obec_kod": 9}}])
    monkeypatch.setattr("scraper.db.connect_read_only", lambda: _Conn([]))
    monkeypatch.setattr(shadow, "tied_ads", lambda conn, ids: [7])
    monkeypatch.setattr(shadow, "block_sizes", lambda conn, blocks: {b: 10 for b in blocks})
    monkeypatch.setattr(shadow, "ruled_blocks", lambda conn: {"town:1"})
    exported: list[list[str]] = []
    monkeypatch.setattr(shadow, "export_cohort",
                        lambda factory, out, blocks: exported.append(blocks) or {})
    assert shadow.main([str(tmp_path)]) == 0
    assert exported == [["ids:5+7", "town:1"]]
    group = json.loads((tmp_path / shadow.GROUP_FILE).read_text(encoding="utf-8"))
    assert (group["unjudgeable"], group["blocks_skipped_no_ruling"]) == (
        [], {"blocks": 1, "ads": 10})


def test_no_ruled_block_exports_nothing_and_never_the_trial_cohort(monkeypatch, tmp_path: Path,
                                                                  capsys):
    exported = _movers_three_blocks(tmp_path, monkeypatch)
    monkeypatch.setattr(shadow, "ruled_blocks", lambda conn: set())
    assert shadow.main([str(tmp_path)]) == 0 and exported == []
    assert "nothing in it can fail, nothing exported" in capsys.readouterr().out
    group = json.loads((tmp_path / shadow.GROUP_FILE).read_text(encoding="utf-8"))
    assert (group["blocks"], group["unjudgeable"]) == ([], [1, 2, 3])


def test_the_export_refuses_an_empty_block_list(tmp_path: Path):
    with pytest.raises(ValueError):
        shadow.export_cohort(lambda: pytest.fail("connected"), tmp_path, [])


def test_the_ruled_blocks_read_is_the_labels_lanes_sources_at_the_export_grain():
    sql = " ".join(shadow.RULED_BLOCKS_SQL.split())
    for source in ("autodedup.verdicts v where v.kind = 'pair'", "v.kind = 'cluster'",
                   "autodedup.must_not_link n where n.source = 'operator'",
                   "autodedup.operator_merges", "'quarter:' || ll.cast_obce_kod",
                   "'town:' || ll.obec_kod"):
        assert source in sql, source


def test_the_apply_scope_filter_is_the_apply_paths_own_predicate():
    """`apply_scope.Scope.outside` over the live row (a JSON string here, as a legacy row reads),
    without importing the apply path."""
    setting = json.dumps({"category_types": ["prodej"],
                          "blocks": [f"town:{BEROUN}", "quarter:490245"]})
    facts = [(1, 10, "prodej", "byt", BEROUN, 7, 3202), (2, 11, "prodej", "byt", PRAHA, 490245,
             None), (3, 12, "prodej", "byt", PRAHA, 400000, None),
             (4, 13, "pronajem", "byt", BEROUN, 7, 3202), (5, None, "prodej", "byt", None, None,
                                                         None)]
    conn = _Conn([(setting,)], facts)
    assert shadow.in_apply_scope(conn, [1, 2, 3, 4, 5]) == [1, 2]
    assert "app_settings" in conn.executed[0][0]
    assert all(in_transaction for *_rest, in_transaction in conn.executed)


# ------------------------------------------------------------------ step 3: the override


def test_the_override_rewrites_only_the_blocks_that_change(tmp_path: Path):
    """The mover's block is rebuilt by the export's own builder; a listed listing whose block
    comes out the same, every other listing and every image are byte-for-byte the base."""
    still = {**FAR, "listing_id": 7, "obec_kod": BEROUN, "obec_name": "Beroun"}
    was = {**FAR, "obec_kod": BEROUN, "obec_name": "Beroun", "lat": 49.96, "lon": 14.07}
    records = [{"t": "meta", "counts": {"listings": 3}},
               {"t": "listing", "id": 7, "location": build_location(still)},
               {"t": "listing", "id": DUP_B, "location": build_location(was)},
               {"t": "listing", "id": 9, "location": build_location(was)},
               {"t": "image", "listing_id": DUP_B, "image_id": 1}]
    base = tmp_path / "base.jsonl.gz"
    with gzip.open(base, "wt", encoding="utf-8") as handle:
        handle.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    positions = _jsonl(tmp_path / "positions.jsonl", [still, FAR, {**FAR, "listing_id": 404}])
    out = tmp_path / "out" / "cohort.jsonl.gz"
    assert shadow.override(base, positions, out) == shadow.Override(
        listed={7, DUP_B, 404}, cohort={7, DUP_B, 9}, changed={DUP_B})
    with gzip.open(base, "rt", encoding="utf-8") as a, gzip.open(out, "rt", encoding="utf-8") as b:
        pairs = list(zip(a, b))
    assert len(pairs) == len(records)
    assert [i for i, (x, y) in enumerate(pairs) if x != y] == [2]
    assert json.loads(pairs[2][1])["location"] == build_location(FAR)


# ------------------------------------------------------------------ step 3: the gate


def _e111(*negatives: tuple[int, int]) -> dict[str, Any]:
    return {"negatives": [list(pair) for pair in negatives], "revoked": bool(negatives)}


def _report(gained: list[list[Any]] = (), lost: list[list[Any]] = (), *,
            negatives: tuple = (), merges: list[list[int]] = ()) -> dict[str, Any]:
    return {"revocation": _e111(*negatives),
            "d83": {"lists": {"copairs_gained": list(gained), "copairs_lost": list(lost),
                              "merge_gained": list(merges), "merge_lost": []}}}


def _judge(report: dict[str, Any], *, changed: set[int] = frozenset(), listed: set[int] = None,
           cohort: set[int] = frozenset(range(1, 10)), together: set = frozenset(),
           recent: set = frozenset(), what_if: dict[str, Any] | None = None,
           base_negatives: tuple = (), elsewhere: set[int] = frozenset(),
           unjudgeable: set[int] = frozenset()) -> dict[str, Any]:
    done = shadow.Override(set(changed if listed is None else listed), set(cohort), set(changed))
    return shadow.judge(report, done, base_e111=_e111(*base_negatives), together=set(together),
                        recent=set(recent), what_if=what_if or {}, elsewhere=set(elsewhere),
                        unjudgeable=set(unjudgeable))


def test_the_gate_fails_a_ruling_that_loses_its_standing():
    """A `same` pair the base held together and the candidate splits, or a `different` pair
    the candidate newly joins, fails; the reverse moves honour a ruling and pass."""
    assert not _judge(_report(lost=[[1, 2, "same", None]]))["pass"]
    assert not _judge(_report(gained=[[1, 2, "different", None]]))["pass"]
    verdict = _judge(_report(gained=[[1, 2, "same", None]],
                             lost=[[3, 4, "different", None], [5, 6, "unruled", None]]))
    assert verdict["pass"] and len(verdict["honoured_rulings"]) == 2


def test_e111_fails_only_on_a_negative_the_move_brought_into_a_family():
    """E111 reports and never decides: a negative the base already holds is today's state
    (PASS, both arms reported); one the candidate adds is the move's doing (FAIL)."""
    standing = _judge(_report(negatives=[(3, 4)]), base_negatives=[(3, 4)])
    assert standing["pass"] and standing["e111"]["new_negatives"] == []
    assert standing["e111"]["base"]["negatives"] == standing["e111"]["candidate"]["negatives"]
    new = _judge(_report(negatives=[(3, 4), (5, 6)]), base_negatives=[(3, 4)])
    assert not new["pass"] and new["e111"]["new_negatives"] == [[5, 6]]
    assert _judge(_report(), base_negatives=[(3, 4)])["pass"]  # the move dissolved one


def test_the_gate_never_fails_on_d83_alone():
    verdict = _judge(_report(gained=[[1, 9, "unruled", None]], merges=[[1, 9], [3, 4]]),
                     changed={9})
    assert verdict["pass"] and not verdict["partial"]
    assert verdict["d83_touching_movers"]["merge_gained"] == [[1, 9]]
    assert verdict["d83_touching_movers"]["copairs_gained"] == [[1, 9, "unruled", None]]


def test_a_limited_run_or_an_unjudged_mover_is_partial_and_still_judged():
    assert _judge(_report(), what_if={"limit": 50})["partial"]
    verdict = _judge(_report(), changed={1}, listed={1, 404})
    assert verdict["partial"] and verdict["movers_unjudged"] == 1 and verdict["pass"]


def test_an_unjudgeable_mover_is_neither_unjudged_nor_elsewhere_nor_partial():
    """No ruling sits on either of its blocks: it cannot fail, so it is reported apart; the
    recent `same` pairs are still counted over all of them (a skipped block holds none)."""
    verdict = _judge(_report(), changed={1}, listed={1, 2, 3}, elsewhere={2, 3}, unjudgeable={3},
                     recent={(1, 9), (40, 41)})
    assert (verdict["movers_unjudgeable"], verdict["movers_elsewhere"],
            verdict["movers_unjudged"], verdict["partial"]) == (1, 1, 0, False)
    assert (verdict["recent_same"]["pairs"], verdict["recent_same"]["unjudged"]) == (2, 1)


def test_a_mover_another_group_exports_is_not_unjudged(tmp_path: Path):
    movers = _jsonl(tmp_path / shadow.MOVERS_FILE, [
        {"listing_id": 1, "old": {"obec_kod": BEROUN}}, {"listing_id": 404, "old": {"obec_kod": 7}},
        {"listing_id": 405, "old": None}, {"listing_id": 406, "old": {"obec_kod": PRAHA}}])
    elsewhere = shadow.outside_group(movers, {"blocks": [f"town:{BEROUN}"]})
    assert elsewhere == {404, 406}
    assert shadow.outside_group(movers, {"blocks": ["ids:406"]}) == {1, 404}
    assert shadow.outside_group(movers, {}) == set()
    verdict = _judge(_report(), changed={1}, listed={1, 404, 405, 406}, elsewhere=elsewhere)
    assert (verdict["movers_elsewhere"], verdict["movers_unjudged"]) == (2, 1)


def test_the_recent_same_pairs_are_counted_apart():
    """Judged (both ads in the cohort) splits into held, broken and apart in both; unjudged is
    printed, never a fail."""
    recent = {(1, 2), (3, 4), (5, 6), (7, 404)}
    verdict = _judge(_report(lost=[[3, 4, "same", None]]), together={(1, 2)}, recent=recent)
    assert verdict["recent_same"] == {"pairs": 4, "judged": 3, "held": 1, "broken": 1,
                                      "apart_in_both": 1, "unjudged": 1}


def test_the_recent_same_pairs_are_the_newest_rulings_since_the_date(tmp_path: Path):
    _jsonl(tmp_path / "operator_labels.jsonl", [
        _same(1, 2), _same(3, 4, "2026-10-20"), _same(5, 6),
        {"listing_lo": 5, "listing_hi": 6, "verdict": "different", "decided_at": "2026-10-29"}])
    _jsonl(tmp_path / "operator_merges.jsonl", [
        {"merged_at": "2026-10-30", "members": [{"listing_id": 9}, {"listing_id": 7},
                                                {"listing_id": 8}]}])
    assert shadow.recent_same(tmp_path) == {(1, 2), (7, 8), (7, 9), (8, 9)}


def _shadow(tmp_path: Path, positions: list[dict[str, Any]], labels: list[dict[str, Any]],
            settings: Settings | None = None, extra: tuple[dict[str, Any], ...] = ()
            ) -> tuple[int, dict[str, Any], str]:
    base = _cohort(tmp_path / "base.jsonl.gz", extra)
    rulings = tmp_path / "labels"
    _jsonl(rulings / "operator_labels.jsonl", labels)
    _jsonl(rulings / "must_not_link.jsonl", [])
    _jsonl(rulings / "must_link.jsonl", [])
    scorer = tmp_path / "scorer.json"
    scorer.write_text(json.dumps({"settings": (settings or Settings()).to_dict(),
                                  "model_version": None}), encoding="utf-8")
    moved = _jsonl(tmp_path / "w" / shadow.POSITIONS_FILE, positions)
    (moved.parent / shadow.WHAT_IF_FILE).write_text(json.dumps(
        {"requested": 9, "scope": "apply", "limit": 0}), encoding="utf-8")
    out = io.StringIO()
    code = harness.main(["shadow", str(base), str(moved), str(rulings), "--scorer", str(scorer),
                         "--out", str(tmp_path / "judge")], out=out)
    verdict = json.loads((tmp_path / "judge" / shadow.SHADOW_FILE).read_text(encoding="utf-8"))
    return code, verdict, out.getvalue()


def test_harness_shadow_passes_a_move_that_keeps_every_ruling(tmp_path: Path):
    code, verdict, printed = _shadow(tmp_path, [FAR], [_same(DUP_A, DUP_B)])
    assert code == 0 and verdict["pass"] and not verdict["partial"]
    assert (verdict["movers_in_cohort"], verdict["movers_listed"]) == (1, 1)
    assert verdict["recent_same"]["held"] == 1 and verdict["recent_same"]["since"] == "2026-10-27"
    assert any(line.startswith("shadow PASS  group 1/1  ids 9 requested, scope apply, limit 0")
               and "E111 base 0 / candidate 0, new 0" in line for line in printed.splitlines())
    for arm in ("base", "candidate"):
        assert (tmp_path / "judge" / arm / harness.RUN_FILE).is_file()
    assert (tmp_path / "judge" / "candidate" / "evaluate.json").is_file()


def test_harness_shadow_fails_when_a_move_splits_a_same_ruling(tmp_path: Path):
    """The twins share an address and nothing else: under a scorer that waives the unit gate
    they merge in the base, and moving one to Praha leaves them no shared probe."""
    code, verdict, printed = _shadow(
        tmp_path, [{**FAR, "listing_id": TWIN_B}], [_same(TWIN_A, TWIN_B)],
        settings=Settings(unit_evidence_required=False))
    assert code == 1 and not verdict["pass"] and verdict["e111"]["new_negatives"] == []
    assert verdict["broken_rulings"] == [[TWIN_A, TWIN_B, "same", None]]
    assert verdict["recent_same"]["broken"] == 1
    assert f"BROKEN {TWIN_A} x {TWIN_B} (same)" in printed


def test_harness_shadow_reports_a_standing_revocation_and_passes(tmp_path: Path):
    """An operator negative inside a K-B family (the re-post pair) revokes E110 in BOTH arms:
    today's state, reported on both, never the wave's FAIL (run 41213246616's false FAIL)."""
    code, verdict, printed = _shadow(tmp_path, [FAR], [
        {"listing_lo": REPOST_A, "listing_hi": REPOST_B, "verdict": "different",
         "decided_at": "2026-10-28"}])
    assert code == 0 and verdict["pass"]
    assert verdict["e111"]["base"]["negatives"] == [[REPOST_A, REPOST_B]]
    assert verdict["e111"]["candidate"]["negatives"] == [[REPOST_A, REPOST_B]]
    assert "E111 base 1 / candidate 1, new 0" in printed


# ------------------------------------------------------------------ fakes


class _Conn:
    """A connection answering its statements in turn (the last answer repeats); records if a
    transaction held."""

    def __init__(self, *answers: list[tuple[Any, ...]]) -> None:
        self.answers, self.executed, self.depth = list(answers), [], 0
        self.description = [SimpleNamespace(name="value")]

    @contextmanager
    def transaction(self):
        self.depth += 1
        try:
            yield self
        finally:
            self.depth -= 1

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql: str, params: Any = None, **kwargs: Any) -> None:
        self.executed.append((sql, params, self.depth > 0))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    def close(self) -> None:
        return None


# ------------------------------------------------------------------ the door report


def _door(**over: Any) -> dict[str, Any]:
    return {"street_key": "turnov|hluboka", "psc": "51101", "granularity": "address_point",
            "granularity_rank": 80, "is_address_grain": True, "uncertainty_radius_m": 10.0, **over}


def _arms(rows: dict[int, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]], settings: Settings,
          pairs: dict[tuple[int, int], tuple[dict[str, Any], dict[str, Any]]] = {},  # noqa: B006
          lists: dict[str, Any] | None = None) -> dict[str, Any]:
    """`door_report` over real fingerprints: id -> (the advert's fields, base door, candidate door)."""
    arms, ids = [], sorted(rows)
    for arm, name in ((1, "base"), (2, "candidate")):
        path = Path(f"{tempfile.mkdtemp()}/{name}.jsonl.gz")
        records = [{"t": "meta", "exported_at": "2026-09-16T10:00:00+00:00", "generator_version": "t",
                    "blocks": [], "counts": {}, "params": {"catalog_df": 8}},
                   *(_listing(i, "turnov", location=_location(**rows[i][arm]),
                              **{"disposition": "2+1", "area_m2": 55.0, **rows[i][0]}) for i in ids)]
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.writelines(json.dumps(record) + "\n" for record in records)
        data = harness.load(path)
        run = RunView({key: value[arm - 1] for key, value in pairs.items() if value[arm - 1]}, {})
        arms.append(shadow.Arm(run, data.listings, harness.fingerprints_of(data, settings)))
    changed = {i for i in ids if rows[i][1] != rows[i][2]}
    return shadow.door_report(*arms, lists or {"merge_gained": [], "merge_lost": []}, changed, settings)


def test_the_door_report_names_new_addr_pairs_flips_and_two_doors_in_one_building():
    same = _door(ruian_adm_kod=701, house_number="7", house_number_cp="7", cast_obce_kod=9)
    one = _door(ruian_adm_kod=702, house_number="9/1", house_number_cp="9", cast_obce_kod=9)
    two = _door(ruian_adm_kod=703, house_number="9/3", house_number_cp="9", cast_obce_kod=9)
    far = _door(ruian_adm_kod=704, house_number="9/5", house_number_cp="9", cast_obce_kod=8)
    held = _door(ruian_adm_kod=705, house_number="11", house_number_cp="11", cast_obce_kod=9)
    flip = {"zone": "band", "feats": {"same_house_number": [1.0, True]}}
    door = _arms({1: ({}, {}, same), 2: ({}, {}, same), 3: ({}, {}, one), 4: ({}, {}, two),
                  5: ({}, {}, far), 6: ({}, held, held), 7: ({}, held, held)}, Settings(),
                 pairs={(1, 2): ({"zone": "band", "feats": {"same_house_number": [0.0, False]}}, flip)},
                 lists={"merge_gained": [[1, 2]], "merge_lost": [[3, 4]]})
    assert door["merge_lost"] == [[3, 4, "sreality|sreality", True]], "both gained část 9"
    assert door["door_gainers"] == 5
    assert door["same_house_number_to_1"] == [[1, 2, "band"]]
    assert [1, 2, "band"] in door["addr_new"] and not door["cap_displaced"]
    assert [6, 7, None] not in door["addr_new"], "both arms held that door: not new"
    assert door["two_doors_one_building"] == [[3, 4, 702, 703, None]], "another část is no building"
    assert door["merge_gained"] == [[1, 2]]


def test_the_door_report_names_a_pair_the_new_addr_candidate_pushes_past_the_cap():
    """Cap 1: B reached M in the base and now spends its one slot on A through `addr`; M spends
    its own on A, so no side reaches the pair. (A, M) survives from M's side."""
    door = _door(ruian_adm_kod=701, house_number="7", house_number_cp="7")
    report = _arms({1: ({}, {}, {}), 2: ({}, {}, door), 3: ({}, {}, door)},
                   Settings(max_candidates_per_listing=1))
    assert report["cap_displaced"] == [[1, 3, None, "sreality|sreality", False]]
    assert [2, 3, None] in report["addr_new"]


def test_the_door_report_names_new_and_gone_blockers():
    """E242 and E272 fire off the door the wave gave; a blocker the base held and the move took away is `gone`."""
    settings = Settings(d43_stored_house_number="guarded", d43_floor_sequential_address_split=True)
    rent = {"category_type": "pronajem", "source": "sreality", "floor": 2, "price": 15_000,
            "description": " ".join(f"Pronajmeme byt cislo {n} se slunecnym balkonem v klidne casti Turnova."
                                    for n in range(12)),
            "broker_key": "one", "broker_firm_id": 1}
    other = {**rent, "floor": 3, "price": 19_000, "broker_key": "two", "broker_firm_id": 2,
             "description": " ".join(f"Zahrada a vyhled {n} nedaleko nadrazi, kuchyn po rekonstrukci, sklep."
                                     for n in range(12))}
    door_a = _door(ruian_adm_kod=701, house_number="7", house_number_cp="7")
    door_b = _door(ruian_adm_kod=702, house_number="9", house_number_cp="9")
    row = {"zone": "band", "feats": {}}
    report = _arms({1: (rent, {}, door_a), 2: (other, {}, door_b)}, settings, pairs={(1, 2): (row, row)})
    assert report["e242"]["new"] == [[1, 2, "band"]] and report["e242"]["candidate"] == 1
    assert report["e272"]["new"] == [[1, 2, "band"]] and not report["e272"]["gone"]
    undone = _arms({1: (rent, door_a, {}), 2: (other, door_b, {})}, settings, pairs={(1, 2): (row, row)})
    assert undone["e242"]["gone"] == [[1, 2, "band"]] and undone["e272"]["gone"] == [[1, 2, "band"]]


_FLAT = {"category_type": "pronajem", "source": "sreality", "floor": 2, "price": 15_000, "broker_key": "one",
         "broker_firm_id": 1, "disposition": "2+1", "area_m2": 55.0,
         "description": " ".join(f"Pronajmeme byt cislo {n} se slunecnym balkonem v klidne casti Turnova."
                                 for n in range(12))}
_OTHER_FLAT = {**_FLAT, "floor": 3, "price": 19_000, "broker_key": "two", "broker_firm_id": 2,
               "description": " ".join(f"Zahrada a vyhled {n} nedaleko nadrazi, kuchyn po rekonstrukci, sklep."
                                       for n in range(12))}


def test_the_door_report_lists_a_row_whose_pin_key_moved_and_how_far():
    on_street = _location(lat=50.5875, lon=15.1583)
    on_pin = _location(lat=50.5885, lon=15.1583, ruian_adm_kod=701, house_number="7", house_number_cp="7")
    still = _location(ruian_adm_kod=702, house_number="9", house_number_cp="9")
    report = _arms({1: ({}, on_street, on_pin), 2: ({}, _location(), still)}, Settings())
    assert report["position_moved"]["count"] == 1
    (listing, metres), = report["position_moved"]["listings"]
    assert listing == 1 and metres == pytest.approx(111.2, abs=0.5)


def test_the_entrance_path_of_e242_counts_under_the_live_settings():
    """E271: one source, two agencies, one č.p. and two č.o., and one template over both. Off without the setting."""
    bodies = {"description": _FLAT["description"]}
    left = _door(ruian_adm_kod=701, house_number="7/1", house_number_cp="7")
    right = _door(ruian_adm_kod=702, house_number="7/3", house_number_cp="7")
    row = {"zone": "band", "feats": {}}
    rows = {1: (_FLAT, {}, left), 2: ({**_OTHER_FLAT, **bodies}, {}, right)}
    live = _arms(rows, harness.named_settings("w31"), pairs={(1, 2): (row, row)})
    assert live["e242"]["new"] == [[1, 2, "band"]] and not live["e242"]["gone"]
    off = _arms(rows, dataclasses.replace(harness.named_settings("w31"), d43_house_number_entrance="off"),
                pairs={(1, 2): (row, row)})
    assert not off["e242"]["new"]


def test_the_door_report_reads_the_runs_settings_never_the_defaults(tmp_path: Path):
    """`harness shadow` hands the report the scorer's settings, the ones both arms ran under. Two rented flats gain two
    doors on one street, so both blockers can fire: under the live row's settings they do, under the defaults they
    are off (`d43_stored_house_number`, `d43_floor_sequential_address_split`). A report falling back to the defaults
    reads nothing here."""
    live = harness.named_settings("w31")
    assert (live.d43_stored_house_number, live.d43_floor_sequential_address_split,
            live.d43_house_number_entrance) == ("guarded", True, "stored")
    defaults = Settings()
    assert defaults.d43_stored_house_number == "off" and not defaults.d43_floor_sequential_address_split
    door = {"obec_kod": 577626, "obec_name": "Turnov", "cast_obce_kod": None, "cast_obce_name": None,
            "granularity": "address_point", "granularity_rank": 80, "is_address_grain": True, "lat": 50.5875,
            "lon": 15.1583, "uncertainty_radius_m": 10.0, "street_name": "Hluboká", "house_number_co": None,
            "psc": "51101", "country_status": "cz"}
    flats = (_listing(8001, "turnov", **_FLAT), _listing(8002, "turnov", **_OTHER_FLAT))
    moved = [{**door, "listing_id": 8001, "house_number_cp": "7", "ruian_adm_kod": 701},
             {**door, "listing_id": 8002, "house_number_cp": "9", "ruian_adm_kod": 702}]
    (tmp_path / "live").mkdir()
    (tmp_path / "off").mkdir()
    _, verdict, _ = _shadow(tmp_path / "live", moved, [], settings=live, extra=flats)
    assert [row[:2] for row in verdict["door"]["e242"]["new"]] == [[8001, 8002]]
    assert [row[:2] for row in verdict["door"]["e272"]["new"]] == [[8001, 8002]]
    _, off, _ = _shadow(tmp_path / "off", moved, [], settings=defaults, extra=flats)
    assert not off["door"]["e242"]["new"] and not off["door"]["e272"]["new"]


def test_harness_shadow_writes_the_door_report_and_it_never_decides(tmp_path: Path):
    code, verdict, printed = _shadow(tmp_path, [FAR], [_same(DUP_A, DUP_B)])
    assert code == 0 and verdict["pass"]
    assert set(verdict["door"]) >= {"e242", "e272", "same_house_number_to_1", "addr_new", "cap_displaced",
                                    "merge_gained", "merge_lost", "two_doors_one_building"}
    assert any(line.startswith("  door report (base -> candidate") for line in printed.splitlines())
    assert "this gate is a sample: it says nothing about any block outside group 1/1 of scope apply" in printed


def test_the_door_report_splits_a_block_key_and_marks_the_part_gained():
    """A door's část moves `o<obec>` to `c<cast>`: the pair stops sharing `attr_dispo` and `attr_area`."""
    part = _door(ruian_adm_kod=701, house_number="7", house_number_cp="7", cast_obce_kod=9)
    held = _location(cast_obce_kod=8)
    row = {"zone": "band", "feats": {}}
    report = _arms({1: ({}, {}, part), 2: ({"source": "idnes"}, {}, {}), 3: ({}, {}, {}), 4: ({}, held, held),
                    5: ({}, held, held), 6: ({}, _location(cast_obce_kod=7), _location(cast_obce_kod=7))}, Settings(),
                   pairs={(1, 2): (row, None), (2, 3): (row, row), (4, 5): (row, row), (5, 6): (row, row)},
                   lists={"merge_gained": [], "merge_lost": [[1, 2]]})
    assert report["block_key_split"] == {"count": 1, "pairs": [[1, 2, None, "idnes|sreality"]]}
    assert report["merge_lost"] == [[1, 2, "idnes|sreality", True]]


_MOVER = {"listing_id": 1, "source": "ceskereality", "old": {"obec_kod": 11}, "new": {"obec_kod": 11, "cast_obce_kod": 5}}


def _loser(listing_id: int, kod: int, obec: int = 11, source: str = "sreality") -> dict[str, Any]:
    """A Q23 loser: a door in the stored row, none in the candidate."""
    return {"listing_id": listing_id, "source": source, "old": {"obec_kod": obec, "ruian_adm_kod": kod},
            "new": {"obec_kod": obec, "ruian_adm_kod": None}}


def test_each_mover_counts_once_at_the_group_that_exports_its_stored_row():
    """Old block town:11 in group 1, new block quarter:99 in group 2; an unquartered mover at the `ids` block. Q23
    losers count twice over: every one, and those on a č.p. spanning 2+ části (`spanning`, the registry's answer)."""
    moved = {"listing_id": 2, "source": "idnes", "old": {"obec_kod": 11, "ruian_adm_kod": 70},
             "new": {"obec_kod": PRAHA, "cast_obce_kod": 99, "ruian_adm_kod": 71}}
    bare = {"listing_id": 3, "source": "remax", "old": {"obec_kod": PRAHA},
            "new": {"obec_kod": PRAHA, "cast_obce_kod": 99}}
    lost = {"listing_id": 4, "source": "idnes", "old": {"obec_kod": 77}, "new": {"obec_kod": 77}}
    counts = shadow.movers_by_group([_MOVER, moved, bare, lost, _loser(5, 72), _loser(6, 73)],
                                    [["ids:3", "town:11"], ["quarter:99"]], {6})
    assert counts[1]["movers"] == 5 and 2 not in counts
    assert (counts[1]["q23"], counts[1]["q23_spanning"]) == (2, 1), "a door kept or moved is no loser"
    assert "rental" not in counts[1]
    assert counts[1]["part"]["ceskereality"] == 1 and counts[1]["sources"]["remax"] == 1
    assert counts[None]["movers"] == 1


def _gate_rows() -> dict[int, dict[str, Any]]:
    return {i: {"movers": 10, "q23": 0, "q23_spanning": 0, "sources": Counter(idnes=10), "part": Counter()}
            for i in range(1, 31)}


def test_the_gate_sample_takes_three_q23_groups_and_the_part_group_then_the_hash_order():
    """The engine owner's stage 1 (2026-11-07, F1 + Q23): up to 3 groups by the Q23 losers on a č.p. spanning 2+
    části, then by all Q23 losers, ties to the lowest group; then the 1 group with the most part-gaining movers of any
    portal. The ceskereality and rental picks are gone: group 7 (most ceskereality part-gainers) is not picked."""
    rows = _gate_rows()
    for i, spanning, losers in ((5, 3, 10), (6, 3, 20), (3, 1, 50), (9, 1, 50), (12, 0, 80)):
        rows[i]["q23_spanning"], rows[i]["q23"] = spanning, losers
    rows[7]["part"]["ceskereality"] = 44
    rows[14]["part"].update(sreality=40, idnes=5)
    sample = shadow.gate_sample(rows, 30)
    assert sample["stage_1"] == [6, 5, 3, 14]
    order = sorted((i for i in range(1, 31) if i not in sample["stage_1"]),
                   key=lambda i: hashlib.sha256(f"w5b-gate-{i}".encode()).hexdigest())
    assert sample["stage_2"] == order[:12] and sample["remaining"] == 26
    assert sample["covered"] == {"idnes": [160, 300]} and sample["covered_all"] == [160, 300]
    held = {i for i in [*sample["stage_1"], *sample["stage_2"]]}
    assert sample["q23"] == [sum(rows[i]["q23"] for i in held), 210]
    assert sample["q23_spanning"] == [sum(rows[i]["q23_spanning"] for i in held), 8]


def test_the_part_group_already_picked_for_q23_is_not_picked_twice():
    rows = _gate_rows()
    rows[4]["q23"] = 1
    rows[4]["part"]["bazos"] = 9
    assert shadow.gate_sample(rows, 30)["stage_1"] == [4]


def test_a_refused_scoping_run_prints_the_groups_and_the_sample(monkeypatch, tmp_path, capsys):
    """The one registry read is bounded by the Q23 losers: their ids and their old doors' kódy, nobody else's."""
    _jsonl(tmp_path / shadow.MOVERS_FILE, [
        {"listing_id": 1, "source": "ceskereality", "old": {"obec_kod": BEROUN},
         "new": {"obec_kod": BEROUN, "cast_obce_kod": 5}},
        {"listing_id": 2, "source": "idnes", "old": {"obec_kod": BRNO, "cast_obce_kod": 7, "ruian_adm_kod": 71},
         "new": {"obec_kod": BRNO, "cast_obce_kod": 7, "ruian_adm_kod": None}}])
    conn = _Conn([(2,)])
    monkeypatch.setattr("scraper.db.connect_read_only", lambda: conn)
    monkeypatch.setattr(shadow, "block_sizes", lambda conn, blocks: {block: 15_000 for block in blocks})
    monkeypatch.setattr(shadow, "export_cohort", lambda *a, **k: pytest.fail("exported past the cap"))
    assert shadow.main([str(tmp_path), "--all-blocks"]) == 2
    out = capsys.readouterr().out
    assert ("[shadow] group 1/2: 1 movers, 1 Q23 losers (1 on a č.p. spanning 2+ části); sources idnes=1; "
            "part-gaining -") in out
    assert ("[shadow] group 2/2: 1 movers, 0 Q23 losers (0 on a č.p. spanning 2+ části); sources ceskereality=1; "
            "part-gaining ceskereality=1") in out
    assert ("[shadow] gate sample: stage 1 [1, 2]; stage 2 [] of the 0 remaining; covers 2 of 2 movers: "
            "ceskereality=1/1 idnes=1/1; Q23 losers 1/1, spanning 1/1") in out
    assert conn.executed[-1][1:] == ({"ids": [2], "kods": [71]}, True)
    assert "ruian_address_points" in conn.executed[-1][0] and "category_type" not in conn.executed[-1][0]


def test_no_q23_loser_asks_the_registry_nothing():
    conn = _Conn([])
    movers = [{"listing_id": 1, "old": {"ruian_adm_kod": 5}, "new": {"ruian_adm_kod": 6}},
              {"listing_id": 2, "old": {"ruian_adm_kod": None}, "new": {"ruian_adm_kod": None}}]
    assert shadow.spanning_losers(conn, movers) == set() and conn.executed == []


def test_the_door_log_names_the_pieces_first_and_last_block_and_their_count(tmp_path: Path):
    """Coverage is confirmed from the log: a split piece prints the span of block keys it judged."""
    (tmp_path / "w").mkdir()
    (tmp_path / "w" / shadow.GROUP_FILE).write_text(json.dumps(
        {"group": "2/9", "blocks": ["town:531057", "town:577626", "town:584"]}), encoding="utf-8")
    _, verdict, printed = _shadow(tmp_path, [FAR], [_same(DUP_A, DUP_B)])
    assert verdict["door"]["piece"] == {"first": "town:531057", "last": "town:584", "blocks": 3}
    assert "  piece town:531057 .. town:584, 3 blocks" in printed


def test_the_door_log_shortens_an_ids_block_to_its_key():
    assert shadow.piece_span({"blocks": ["ids:1+2+3", "town:7"]}) == {"first": "ids", "last": "town:7", "blocks": 2}
    assert shadow.piece_span({}) == {"first": None, "last": None, "blocks": 0}


# ------------------------------------------------------------------ crossing pairs and a piece's state


_REAL_COHORT_IDS = shadow._cohort_ids


def test_crossing_pairs_are_the_stored_pairs_a_mover_holds_outside_the_cohort():
    """Read off the live store by the piece's mover ids, both index arms; a pair inside the cohort is judged, so it
    does not count, and a mover outside the cohort is another piece's."""
    conn = _Conn([(1, 900, "merge"), (1, 901, "band"), (2, 902, "band"), (800, 1, "reject"), (1, 3, "merge")])
    out = shadow.crossing_pairs(conn, [1, 2, 7], {1, 2, 3})
    assert out["movers"] == 2 and out["pairs"] == 4
    assert out["by_zone"] == {"merge": 1, "band": 2, "reject": 1}
    assert out["merge_pairs"] == [[1, 900]]
    sql, params, in_transaction = conn.executed[-1]
    assert in_transaction and sorted(params["ids"]) == [1, 2], "bounded by the movers in this cohort"
    flat = " ".join(sql.split()).lower()
    assert "p.listing_lo = any(%(ids)s::bigint[])" in flat and "p.listing_hi = any(%(ids)s::bigint[])" in flat
    idle = _Conn([])
    assert shadow.crossing_pairs(idle, [7], {1}) == {"movers": 0, "pairs": 0,
                                                     "by_zone": {"merge": 0, "band": 0, "reject": 0},
                                                     "merge_pairs": []}
    assert not idle.executed, "no mover in the cohort, no read"


def test_the_gate_reports_what_step_two_read_and_when_the_run_started(tmp_path: Path):
    (tmp_path / "w").mkdir()
    crossing = {"movers": 2, "pairs": 5, "by_zone": {"merge": 1, "band": 3, "reject": 1}}
    (tmp_path / "w" / shadow.GROUP_FILE).write_text(json.dumps(
        {"group": "2/9", "blocks": ["town:531057"], "crossing_pairs": crossing,
         "started_at": "2026-11-03T08:00:00+00:00"}), encoding="utf-8")
    _, verdict, printed = _shadow(tmp_path, [FAR], [_same(DUP_A, DUP_B)])
    assert verdict["state"] == "judged" and verdict["started_at"] == "2026-11-03T08:00:00+00:00"
    assert verdict["door"]["crossing_pairs"] == crossing
    assert "crossing pairs 2 movers, 5 pairs (merge 1, band 3, reject 1)" in printed
    assert "started 2026-11-03T08:00:00+00:00" in printed


def test_a_gate_with_no_crossing_read_says_so_rather_than_zero(tmp_path: Path):
    _, verdict, printed = _shadow(tmp_path, [FAR], [_same(DUP_A, DUP_B)])
    assert verdict["door"]["crossing_pairs"] is None and "crossing pairs not read" in printed
    assert verdict["started_at"]


def test_a_piece_with_no_ruling_writes_a_marked_zero_a_failed_piece_never_has(monkeypatch, tmp_path: Path):
    """Step 3 is skipped when nothing is exported, so step 2 writes the piece's result: a failed piece has no
    `shadow.json` at all."""
    _jsonl(tmp_path / shadow.MOVERS_FILE, [{"listing_id": 1, "old": {"obec_kod": 1}, "new": {"obec_kod": 1}}])
    monkeypatch.setattr("scraper.db.connect_read_only", lambda: _Conn([]))
    monkeypatch.setattr(shadow, "block_sizes", lambda conn, blocks: {b: 10 for b in blocks})
    monkeypatch.setattr(shadow, "ruled_blocks", lambda conn: set())
    monkeypatch.setattr(shadow, "export_cohort", lambda *a, **k: pytest.fail("exported nothing to export"))
    assert shadow.main([str(tmp_path)]) == 0
    verdict = json.loads((tmp_path / "judge" / shadow.SHADOW_FILE).read_text(encoding="utf-8"))
    assert verdict["state"] == "nothing_exported" and verdict["pass"] is None and verdict["started_at"]
    assert verdict["door"]["crossing_pairs"] == {
        "movers": 0, "pairs": 0, "by_zone": {"merge": 0, "band": 0, "reject": 0}, "merge_pairs": [],
        "zero": "no_ruling_in_blocks"}
    assert verdict["door"]["piece"]["blocks"] == 0


def test_an_exported_piece_records_its_crossing_pairs_and_start_for_the_gate(monkeypatch, tmp_path: Path):
    _jsonl(tmp_path / shadow.MOVERS_FILE, [
        {"listing_id": 1, "source": "idnes", "old": {"obec_kod": 2}, "new": {"obec_kod": 2}}])
    monkeypatch.setattr("scraper.db.connect_read_only", lambda: _Conn([(1, 900, "band")]))
    monkeypatch.setattr(shadow, "block_sizes", lambda conn, blocks: {b: 10 for b in blocks})
    monkeypatch.setattr(shadow, "export_cohort", lambda *a, **k: {})
    monkeypatch.setattr(shadow, "_cohort_ids", lambda path: {1})
    assert shadow.main([str(tmp_path), "--all-blocks"]) == 0
    group = json.loads((tmp_path / shadow.GROUP_FILE).read_text(encoding="utf-8"))
    assert group["crossing_pairs"] == {"movers": 1, "pairs": 1, "by_zone": {"merge": 0, "band": 1, "reject": 0},
                                       "merge_pairs": []}
    assert group["started_at"]


def test_the_cohort_ids_are_the_artifacts_listings(tmp_path: Path):
    assert _REAL_COHORT_IDS(_cohort(tmp_path / "c.jsonl.gz")) >= {DUP_A, DUP_B, TWIN_A}
