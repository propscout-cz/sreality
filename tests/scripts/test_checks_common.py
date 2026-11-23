"""Hermetic tests for scripts.checks.common: the shared result shape."""

from __future__ import annotations

import pytest

from scripts.checks import common
from scripts.checks import platform as plat


def test_platform_reads_the_moved_helpers_under_its_old_names() -> None:
    """One shape: the wave-3 module's private names are these functions, not copies."""
    for private, public in (("_cell", "cell"), ("_judged", "judged"), ("_result", "result"),
                            ("_oneline", "oneline"), ("_ago", "ago"), ("_num", "num"),
                            ("_as_utc", "as_utc"), ("_iso", "iso"), ("_now", "now"),
                            ("_graded", "graded")):
        assert getattr(plat, private) is getattr(common, public), private


def test_the_verdict_is_the_worst_measured_cell_and_counts_its_cells() -> None:
    cells = [common.cell("a/x", "warn", "late"), common.cell("b/x", "fail", "stopped"),
             common.cell("c/x", "fail", "stopped"), common.cell("d/x", "unknown", "Not measured")]
    out = common.result("scraping", cells, {"inflow": "no portal"})
    assert (out["status"], out["value"]) == ("fail", 2)
    assert out["details"]["not_measured"] == {"inflow": "no portal"}
    assert "1 Not measured" in out["message"] and "Not read: inflow: no portal" in out["message"]


def test_a_check_that_measured_nothing_is_unknown_with_the_reason() -> None:
    out = common.result("coverage", [common.cell("a/x", "unknown", "Not measured: no walk")],
                        {"walks": "no index walk in 30 d"})
    assert (out["status"], out["value"]) == ("unknown", None)
    assert out["details"]["skipped"] == "walks: no index walk in 30 d"


@pytest.mark.parametrize("value, status", [(0.02, "ok"), (0.021, "warn"), (0.051, "fail")])
def test_graded_is_strictly_above_each_line(value: float, status: str) -> None:
    assert common.graded(value, 0.02, 0.05) == status
