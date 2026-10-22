"""The reason line, pinned against the job that broke the signature engine.

Job 116299577551 (the hourly lane, 2026-10-15 07:21Z) went red on `CHECK llm_errors status=fail`,
and the retired engine filed it as `Metadata: read`, a GITHUB_TOKEN permission row of the runner
setup. The fixture is that job's log as `/actions/jobs/{id}/logs` serves it, trimmed to the runner
setup, two step groups and the cleanup tail; Actions had masked its secrets as ***, and the runner
VM id is zeroed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.failure_signature import MAX_REASON_LEN, reason_line

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflow_job_116299577551.txt"
_EXIT_1 = "##[error]Process completed with exit code 1."


def _actions_log(*lines: str) -> str:
    """Actions prefixes EVERY log line with an ISO timestamp; nothing is at column 0."""
    return "".join(f"2026-08-26T20:15:0{i % 10}.1234567Z {ln}\n" for i, ln in enumerate(lines))


def test_the_job_that_broke_the_engine_reads_as_its_check_line() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    assert "Metadata: read" in text
    assert reason_line(text) == (
        "2026-10-15 07:21:27,594 INFO verify_pipeline CHECK llm_errors status=fail value=0.0035")


def test_the_last_failing_check_is_the_reason_and_a_warn_never_is() -> None:
    text = _actions_log(
        "INFO verify_pipeline CHECK llm_errors status=fail value=1",
        "INFO verify_pipeline CHECK property_maintenance status=fail value=2",
        "INFO verify_pipeline CHECK coverage status=warn value=3",
        "INFO verify_pipeline CHECK dedup status=ok value=4",
    )
    assert reason_line(text) == "INFO verify_pipeline CHECK property_maintenance status=fail value=2"
    assert reason_line(_actions_log("INFO verify_pipeline CHECK coverage status=warn value=3",
                                    _EXIT_1)) == _EXIT_1


def test_a_failing_check_outranks_an_exception() -> None:
    text = _actions_log("INFO verify_pipeline CHECK dedup status=fail value=1.0",
                        "ValueError: raised after the verdict", _EXIT_1)
    assert reason_line(text) == "INFO verify_pipeline CHECK dedup status=fail value=1.0"


@pytest.mark.parametrize("cls", ["CheckViolation", "QueryCanceled", "AdminShutdown",
                                 "AmbiguousFunction", "InsufficientPrivilege"])
def test_a_traceback_is_read_at_its_final_line(cls: str) -> None:
    """The corpus's most important classes are not named *Error; their traceback names them, and
    the psycopg DETAIL row under it (a whole listing row) never reaches the reason."""
    text = _actions_log(
        "Traceback (most recent call last):",
        '  File "/home/runner/work/sreality/sreality/scraper/db.py", line 341, in write',
        "    cur.execute(sql, params)",
        f"psycopg.errors.{cls}: something broke",
        "DETAIL:  Failing row contains (81234, mmreality, 4471203, Praha 5, 8900000, plot).",
        _EXIT_1,
    )
    assert reason_line(text) == f"psycopg.errors.{cls}: something broke"


def test_the_last_exception_is_the_reason_across_chained_tracebacks() -> None:
    text = _actions_log(
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "KeyError: 'a'",
        "",
        "During handling of the above exception, another exception occurred:",
        "",
        "Traceback (most recent call last):",
        '  File "x.py", line 3, in <module>',
        "RuntimeError: b",
        _EXIT_1,
    )
    assert reason_line(text) == "RuntimeError: b"


def test_an_error_line_is_read_without_a_traceback() -> None:
    text = _actions_log("ENRICH id=771 calling the proxy",
                        "requests.exceptions.HTTPError: 403 from https://a.cz/x", _EXIT_1)
    assert reason_line(text) == "requests.exceptions.HTTPError: 403 from https://a.cz/x"


def test_log_labels_and_runner_rows_are_never_the_reason() -> None:
    text = _actions_log(
        "ERROR: could not connect",
        "WARNING: slow query",
        "Metadata: read",
        "Actions: read",
        "Note: switching to 'f3dc54e1fb89da6852db922499a0233137369e1d'.",
        "##[group]Run python -m scripts.backfill",
        "ValueError: an echoed line inside the step's group",
        "##[endgroup]",
        "##[error]The action has timed out after 60 minutes.",
    )
    assert reason_line(text) == "##[error]The action has timed out after 60 minutes."


def test_the_annotation_is_the_last_tier() -> None:
    text = _actions_log(_EXIT_1, "Post job cleanup.", "Cleaning up orphan processes")
    assert reason_line(text) == _EXIT_1


def test_an_unreadable_log_has_no_reason() -> None:
    assert reason_line(None) is None
    assert reason_line("") is None
    assert reason_line(_actions_log("Post job cleanup.", "Cleaning up orphan processes")) is None


def test_the_reason_is_one_line_cut_to_300_characters() -> None:
    reason = reason_line(_actions_log("RuntimeError: " + "word " * 200, "the next line"))
    assert reason is not None and reason.startswith("RuntimeError: word")
    assert len(reason) == MAX_REASON_LEN == 300 and "next line" not in reason
