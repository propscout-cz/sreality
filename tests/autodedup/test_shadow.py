"""The wave shadow's autodedup half: the export step, the override and the gate (`harness shadow`).

Hermetic: the export step runs over stubbed exporters and a fake connection, and the gate over
the engine e2e's synthetic cohort, so nothing here reaches a database.
"""

from __future__ import annotations

import gzip
import io
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from autodedup import harness, iterations, shadow
from autodedup.export import build_location
from autodedup.settings import Settings
from tests.autodedup.test_engine_e2e import DUP_A, DUP_B, REPOST_A, REPOST_B, build_records

PRAHA, BRNO, BEROUN = 554782, 582786, 531057

FAR = {"listing_id": DUP_B, "obec_kod": PRAHA, "obec_name": "Praha", "cast_obce_kod": None,
       "cast_obce_name": None, "granularity": "obec", "granularity_rank": 20,
       "is_address_grain": False, "lat": 50.08, "lon": 14.42, "uncertainty_radius_m": 1000.0,
       "street_name": None, "house_number_cp": None, "house_number_co": None, "psc": None,
       "ruian_adm_kod": None, "country_status": "cz"}


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _cohort(path: Path) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for record in build_records():
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def _listings(path: Path) -> dict[int, dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return {r["id"]: r for r in map(json.loads, handle) if r["t"] == "listing"}


# ------------------------------------------------------------------ step 2: the export


def test_blocks_are_each_movers_old_and_new_block_at_the_engines_grain():
    """E300's grain: a quarter in a split city, the town elsewhere; a split-city side with no
    quarter would mean exporting the whole city, so it is left out and counted."""
    movers = [
        {"old": {"obec_kod": PRAHA, "cast_obce_kod": 490245},
         "new": {"obec_kod": PRAHA, "cast_obce_kod": 400000}},
        {"old": {"obec_kod": BEROUN, "cast_obce_kod": 12},
         "new": {"obec_kod": BRNO, "cast_obce_kod": None}},
        {"old": None, "new": {"obec_kod": BEROUN, "cast_obce_kod": 13}},
        {"old": {"obec_kod": None}, "new": {"obec_kod": PRAHA, "cast_obce_kod": 490245}},
    ]
    assert shadow.blocks_for(movers) == (
        ["quarter:490245", "quarter:400000", f"town:{BEROUN}"], 1)


def test_the_export_step_writes_no_ledger_row_and_reads_the_live_scorer(monkeypatch,
                                                                         tmp_path: Path):
    """The exporters run IN-PROCESS, never through `lane.run`, which is what writes the
    `autodedup.iterations` row; the scorer is the live generation's calibration row."""
    _jsonl(tmp_path / shadow.MOVERS_FILE, [
        {"old": {"obec_kod": BEROUN}, "new": {"obec_kod": PRAHA, "cast_obce_kod": 490245}}])
    called: dict[str, Any] = {}

    def no_ledger(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the shadow wrote an iterations row")

    monkeypatch.setattr(iterations, "start_iteration", no_ledger)
    monkeypatch.setattr(shadow, "run_export", lambda factory, args, out: called.update(
        export=(args, Path(out))) or {"counts": {"listings": 3}})
    monkeypatch.setattr(shadow, "run_labels", lambda factory, args, out: called.update(
        labels=(args, Path(out))) or {"counts": {"labels": 2}})
    live = json.loads(json.dumps(Settings().to_dict()))  # as the jsonb column reads back
    summary = shadow.export(lambda: _ScorerConn((json.dumps(live), "w6_gold")), tmp_path)
    assert called["export"] == ({"blocks": f"town:{BEROUN} quarter:490245"}, tmp_path / "base")
    assert called["labels"] == ({}, tmp_path / "labels")
    assert summary["cohort"] == {"listings": 3}
    scorer = json.loads((tmp_path / shadow.SCORER_FILE).read_text(encoding="utf-8"))
    assert (scorer["generation"], scorer["settings"], scorer["model_version"]) == (
        "rt", live, "w6_gold")


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
    positions = _jsonl(tmp_path / "positions.jsonl", [still, FAR])
    out = tmp_path / "out" / "cohort.jsonl.gz"
    assert shadow.override(base, positions, out) == {DUP_B}
    with gzip.open(base, "rt", encoding="utf-8") as a, gzip.open(out, "rt", encoding="utf-8") as b:
        pairs = list(zip(a, b))
    assert len(pairs) == len(records)
    assert [i for i, (x, y) in enumerate(pairs) if x != y] == [2]
    assert json.loads(pairs[2][1])["location"] == build_location(FAR)


# ------------------------------------------------------------------ step 3: the gate


def _report(gained: list[list[Any]] = (), lost: list[list[Any]] = (), *,
            revoked: bool = False, merges: list[list[int]] = ()) -> dict[str, Any]:
    return {"revocation": {"revoked": revoked},
            "d83": {"lists": {"copairs_gained": list(gained), "copairs_lost": list(lost),
                              "merge_gained": list(merges), "merge_lost": []}}}


def test_the_gate_fails_a_ruling_that_loses_its_standing():
    """A `same` pair the base held together and the candidate splits, or a `different` pair
    the candidate newly joins, fails; the reverse moves honour a ruling and pass."""
    assert not shadow.judge(_report(lost=[[1, 2, "same", None]]), set())["pass"]
    assert not shadow.judge(_report(gained=[[1, 2, "different", None]]), set())["pass"]
    verdict = shadow.judge(_report(gained=[[1, 2, "same", None]],
                                   lost=[[3, 4, "different", None], [5, 6, "unruled", None]]),
                           set())
    assert verdict["pass"] and len(verdict["honoured_rulings"]) == 2


def test_the_gate_fails_a_revoked_e110_and_never_fails_on_d83_alone():
    assert not shadow.judge(_report(revoked=True), set())["pass"]
    verdict = shadow.judge(_report(gained=[[1, 9, "unruled", None]], merges=[[1, 9], [3, 4]]),
                           {9})
    assert verdict["pass"]
    assert verdict["d83_touching_movers"]["merge_gained"] == [[1, 9]]
    assert verdict["d83_touching_movers"]["copairs_gained"] == [[1, 9, "unruled", None]]


def _shadow(tmp_path: Path, positions: list[dict[str, Any]],
            labels: list[dict[str, Any]]) -> tuple[int, dict[str, Any], str]:
    base = _cohort(tmp_path / "base.jsonl.gz")
    rulings = tmp_path / "labels"
    _jsonl(rulings / "operator_labels.jsonl", labels)
    _jsonl(rulings / "must_not_link.jsonl", [])
    _jsonl(rulings / "must_link.jsonl", [])
    scorer = tmp_path / "scorer.json"
    scorer.write_text(json.dumps({"settings": Settings().to_dict(), "model_version": None}),
                      encoding="utf-8")
    out = io.StringIO()
    code = harness.main(["shadow", str(base), str(_jsonl(tmp_path / "positions.jsonl", positions)),
                         str(rulings), "--scorer", str(scorer), "--out", str(tmp_path / "judge")],
                        out=out)
    verdict = json.loads((tmp_path / "judge" / shadow.SHADOW_FILE).read_text(encoding="utf-8"))
    return code, verdict, out.getvalue()


def test_harness_shadow_passes_a_move_that_keeps_every_ruling(tmp_path: Path):
    code, verdict, printed = _shadow(tmp_path, [FAR], [
        {"listing_lo": DUP_A, "listing_hi": DUP_B, "verdict": "same", "decided_at": "2026-10-28"}])
    assert code == 0 and verdict["pass"]
    assert (verdict["movers_in_cohort"], verdict["movers_listed"]) == (1, 1)
    assert any(line.startswith("shadow PASS") for line in printed.splitlines())
    for arm in ("base", "candidate"):
        assert (tmp_path / "judge" / arm / harness.RUN_FILE).is_file()
    assert (tmp_path / "judge" / "candidate" / "evaluate.json").is_file()


def test_harness_shadow_fails_when_e110_is_revoked(tmp_path: Path):
    """An operator negative inside a K-B family (the re-post pair) revokes E110: the gate fails
    whatever the movers did."""
    code, verdict, printed = _shadow(tmp_path, [FAR], [
        {"listing_lo": REPOST_A, "listing_hi": REPOST_B, "verdict": "different",
         "decided_at": "2026-10-28"}])
    assert code == 1 and verdict["revoked"] and not verdict["pass"]
    assert "shadow FAIL" in printed


# ------------------------------------------------------------------ fakes


class _ScorerConn:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self.row = row

    @contextmanager
    def transaction(self):
        yield self

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql: str, params: Any = None) -> None:
        assert "autodedup.rt_calibration" in sql and params == {"generation": "rt"}

    def fetchone(self) -> tuple[Any, ...]:
        return self.row

    def close(self) -> None:
        return None
