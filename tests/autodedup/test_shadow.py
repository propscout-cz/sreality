"""The wave shadow's engine half: the export step, the override and the gate (`harness shadow`).

Hermetic: the export step runs over stubbed exporters and fake connections, the gate over the
engine e2e's synthetic cohort, so nothing here reaches a database.
"""

from __future__ import annotations

import gzip
import io
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autodedup import harness, shadow
from autodedup.export import build_location
from autodedup.settings import Settings
from tests.autodedup.test_engine_e2e import (
    DUP_A,
    DUP_B,
    REPOST_A,
    REPOST_B,
    _listing,
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


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _cohort(path: Path) -> Path:
    records = build_records()
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
                        lambda conn, blocks: {block: 40_000 for block in blocks})
    monkeypatch.setattr(shadow, "export_cohort",
                        lambda *a, **k: pytest.fail("exported past the cap"))
    assert shadow.main([str(tmp_path)]) == 2
    printed = capsys.readouterr()
    assert f"town:{BEROUN} 40000 ads" in printed.out and "REFUSED" in printed.err
    assert shadow.main([str(tmp_path), "--max-ads", "1000000", "--max-blocks", "1"]) == 2


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


def _report(gained: list[list[Any]] = (), lost: list[list[Any]] = (), *,
            revoked: bool = False, merges: list[list[int]] = ()) -> dict[str, Any]:
    return {"revocation": {"revoked": revoked},
            "d83": {"lists": {"copairs_gained": list(gained), "copairs_lost": list(lost),
                              "merge_gained": list(merges), "merge_lost": []}}}


def _judge(report: dict[str, Any], *, changed: set[int] = frozenset(), listed: set[int] = None,
           cohort: set[int] = frozenset(range(1, 10)), together: set = frozenset(),
           recent: set = frozenset(), what_if: dict[str, Any] | None = None) -> dict[str, Any]:
    done = shadow.Override(set(changed if listed is None else listed), set(cohort), set(changed))
    return shadow.judge(report, done, together=set(together), recent=set(recent),
                        what_if=what_if or {})


def test_the_gate_fails_a_ruling_that_loses_its_standing():
    """A `same` pair the base held together and the candidate splits, or a `different` pair
    the candidate newly joins, fails; the reverse moves honour a ruling and pass."""
    assert not _judge(_report(lost=[[1, 2, "same", None]]))["pass"]
    assert not _judge(_report(gained=[[1, 2, "different", None]]))["pass"]
    verdict = _judge(_report(gained=[[1, 2, "same", None]],
                             lost=[[3, 4, "different", None], [5, 6, "unruled", None]]))
    assert verdict["pass"] and len(verdict["honoured_rulings"]) == 2


def test_the_gate_fails_a_revoked_e110_and_never_fails_on_d83_alone():
    assert not _judge(_report(revoked=True))["pass"]
    verdict = _judge(_report(gained=[[1, 9, "unruled", None]], merges=[[1, 9], [3, 4]]),
                     changed={9})
    assert verdict["pass"] and not verdict["partial"]
    assert verdict["d83_touching_movers"]["merge_gained"] == [[1, 9]]
    assert verdict["d83_touching_movers"]["copairs_gained"] == [[1, 9, "unruled", None]]


def test_a_limited_run_or_an_unjudged_mover_is_partial_and_still_judged():
    assert _judge(_report(), what_if={"limit": 50})["partial"]
    verdict = _judge(_report(), changed={1}, listed={1, 404})
    assert verdict["partial"] and verdict["movers_unjudged"] == 1 and verdict["pass"]


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
            settings: Settings | None = None) -> tuple[int, dict[str, Any], str]:
    base = _cohort(tmp_path / "base.jsonl.gz")
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
    assert any(line.startswith("shadow PASS  ids 9 requested, scope apply, limit 0")
               for line in printed.splitlines())
    for arm in ("base", "candidate"):
        assert (tmp_path / "judge" / arm / harness.RUN_FILE).is_file()
    assert (tmp_path / "judge" / "candidate" / "evaluate.json").is_file()


def test_harness_shadow_fails_when_a_move_splits_a_same_ruling(tmp_path: Path):
    """The twins share an address and nothing else: under a scorer that waives the unit gate
    they merge in the base, and moving one to Praha leaves them no shared probe."""
    code, verdict, printed = _shadow(
        tmp_path, [{**FAR, "listing_id": TWIN_B}], [_same(TWIN_A, TWIN_B)],
        settings=Settings(unit_evidence_required=False))
    assert code == 1 and not verdict["pass"] and not verdict["revoked"]
    assert verdict["broken_rulings"] == [[TWIN_A, TWIN_B, "same", None]]
    assert verdict["recent_same"]["broken"] == 1
    assert f"BROKEN {TWIN_A} x {TWIN_B} (same)" in printed


def test_harness_shadow_fails_when_e110_is_revoked(tmp_path: Path):
    """An operator negative inside a K-B family (the re-post pair) revokes E110: the gate fails
    whatever the movers did."""
    code, verdict, printed = _shadow(tmp_path, [FAR], [
        {"listing_lo": REPOST_A, "listing_hi": REPOST_B, "verdict": "different",
         "decided_at": "2026-10-28"}])
    assert code == 1 and verdict["revoked"] and not verdict["pass"]
    assert "shadow FAIL" in printed


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
