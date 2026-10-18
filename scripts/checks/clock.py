"""The per-check wall clock every health check's statement runs under (wave 2 of the Health sprint).

Before W0.4 `run_checks` computed EVERY result before persisting ANY of them, so a job
timeout wrote zero rows — blinding db_saturation at exactly the moment (DB saturation) that
made the checks slow in the first place. Results are persisted as each check completes, the
lane runs under the sum of its checks' budgets, and each check on a wall clock of its own
`Check.budget_s`: a deadline, so every statement it sends gets a server-side
statement_timeout of the time the check has LEFT. One fixed timeout per statement let a
check of several statements spend its budget once per statement (dual_write_parity: p50
57 s on a 45 s budget).

`LANE` is the one running lane; `run_checks` starts it and arms it for each check, and every
statement a check sends goes through `fetchone` / `fetchall` (or re-arms `SET LOCAL` itself
with `statement_timeout_clause()`), so a check module never imports the harness.
"""

from __future__ import annotations

import time as _time
from typing import Any

# The default per-check budget. The slowest DB check is floor_convention's cross-portal
# self-join, 11-35s over four measured runs (the shared measure_plausibility read is ~12s),
# so at its worst 45s is only 1.3x the known worst case — a new check above ~20s needs its
# query reworked, not a wider budget.
CHECK_BUDGET_S = 45.0
# Outside run_checks (ad-hoc use, tests) keep the historical 10-minute ceiling.
DEFAULT_STATEMENT_TIMEOUT_MS = 600_000


class LaneBudget:
    """The running lane's deadline and its current check's, reset by every run_checks."""

    def __init__(self) -> None:
        self.deadline: float | None = None
        self.check_deadline: float | None = None

    def start(self, budget_s: float) -> None:
        self.deadline = _time.monotonic() + budget_s

    def reset(self) -> None:
        self.deadline = None
        self.check_deadline = None

    def remaining(self) -> float | None:
        if self.deadline is None:
            return None
        return self.deadline - _time.monotonic()

    def arm_for_check(self, check_budget_s: float) -> float:
        """Start the next check's clock: its own budget, cut to what the lane has left (so
        its deadline is never past the lane's); returns that budget, <= 0 once the lane's
        is spent."""
        left = self.remaining()
        budget = check_budget_s if left is None else min(check_budget_s, left)
        self.check_deadline = _time.monotonic() + budget
        return budget

    def statement_timeout_ms(self) -> float:
        """What the running check has left for its next statement; the default outside one."""
        if self.check_deadline is None:
            return DEFAULT_STATEMENT_TIMEOUT_MS
        # Never under 1 s: a timeout of 0 would switch the timeout off, and a check that spent
        # its budget between statements gets one short try, cancelled unless it is quick.
        return max(1000.0, (self.check_deadline - _time.monotonic()) * 1000.0)


LANE = LaneBudget()


def statement_timeout_clause() -> str:
    """The `SET LOCAL statement_timeout` for the next statement: the running check's time left.

    Server-side enforcement is what gives a check a real wall-clock bound: the
    connection is autocommit and shared by every check, so a Python-side timeout
    (a thread we cannot cancel, or a signal raised mid-query) would leave the
    connection wedged for everyone after it. Postgres cancelling its own query is
    the only mechanism that hands the connection back clean.
    """
    return f"{int(LANE.statement_timeout_ms())}ms"


def fetchone(conn: Any, sql: str, params: Any = None) -> tuple[Any, ...] | None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL statement_timeout = '{statement_timeout_clause()}'")
        cur.execute(sql, params or ())
        return cur.fetchone()


def fetchall(conn: Any, sql: str, params: Any = None) -> list[tuple[Any, ...]]:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL statement_timeout = '{statement_timeout_clause()}'")
        cur.execute(sql, params or ())
        return cur.fetchall()
