"""THE writer's lease of the autodedup lane: an adapter over `scraper.job_lease`, which owns the
row (`scheduled_jobs.autodedup_realtime`) and every statement that writes it (#2109).

The worker's pass, `rt_seed` and a live `apply` / `unapply` each hold it for their run, so one
of them at a time writes the live stream or production merges (A9). Every refusal names the
holder and when its lease ends; `release_stale` is the path for a holder that died with it (a
killed dispatch holds it for its whole TTL), `release_predecessor` the worker's own for a process
that died with it (E948, which `predecessor` only names), and a release that fails while another
error is already on its way out never replaces that error.
"""

from __future__ import annotations

import logging
import time
from contextlib import suppress
from typing import Any, Callable

from autodedup.incremental_sql import RT_LEASE_READ_SQL
from scraper import job_lease

NAME: str = "autodedup_realtime"
# The row's declarations, never read: it has no `due_at`, so nothing schedules it.
_CADENCE: str = "10 minutes"
_RUNNER: str = "railway-realtime-worker"
# A seed or a live dispatch holds it for its job's own timeout (`autodedup.yml`,
# `timeout-minutes: 300`), so a running one can never lose it to a pass.
DISPATCH_TTL_S: int = 300 * 60
# The dispatch argument that ends a dead holder's lease first (`release_stale`).
RELEASE_ARG: str = "release_lease"
# A database restart takes every connection a run holds, the fresh one too (2026-10-10:
# Postgres was down from 05:56:15Z to about 05:57:13Z, 58 s, and the lease then sat its whole
# TTL), so `release_after` opens NEW ones, one every NEW_CONNECTION_DELAY_S, for up to
# NEW_CONNECTION_BUDGET_S, three times that outage, before it leaves the lease to its TTL.
NEW_CONNECTION_BUDGET_S: float = 180.0
NEW_CONNECTION_DELAY_S: float = 20.0
# This module's own wait and clock, so a test stubs them here and nowhere else.
_sleep: Callable[[float], None] = time.sleep
_clock: Callable[[], float] = time.monotonic

LOG = logging.getLogger(__name__)


def take(conn: Any, holder: str, ttl: int) -> bool:
    return job_lease.acquire(conn, NAME, cadence=_CADENCE, runner=_RUNNER, ttl_s=ttl,
                             holder=holder) is not None


def release(conn: Any, holder: str) -> None:
    job_lease.end(conn, NAME, holder)


def release_predecessor(conn: Any, hostname: str, pid: int, booted_epoch: int) -> str | None:
    """End the live lease a dead predecessor of this process (this hostname and pid, a second
    before `booted_epoch`) left; its holder, or None (E948)."""
    return job_lease.end_predecessor(conn, NAME, host=hostname, pid=pid, booted_epoch=booted_epoch)


def predecessor(conn: Any, hostname: str, pid: int, booted_epoch: int) -> str | None:
    """The holder of that same lease, read and left live (E948)."""
    return job_lease.predecessor(conn, NAME, host=hostname, pid=pid, booted_epoch=booted_epoch)


# The outcome a run reports when its lease ended under it (E941).
LEASE_LOST: str = "lease_lost"
# The holder's lease ended while its run still ran: released by the worker's shutdown (E941) or by
# a `release_lease=` dispatch that took the holder for dead.
LeaseLost = job_lease.LeaseLost


def hold(conn: Any, holder: str) -> None:
    """Inside the caller's transaction, as its last statement before the commit: lock this
    holder's live lease row until the transaction ends, or raise `LeaseLost` so it rolls back
    (E941). A run whose lease was released under it then never commits beside the next holder,
    and a release that arrives after this statement waits for the commit."""
    try:
        job_lease.fence(conn, job_lease.Hold(NAME, holder, 0, 0.0))
    except LeaseLost as exc:
        raise LeaseLost(f"rt_lease is no longer held by {holder!r}: {describe(conn)}") from exc


def current(conn: Any) -> dict[str, Any] | None:
    """The row as it stands: holder, taken_at, expires_at and whether it is still live."""
    with conn.cursor() as cur:
        cur.execute(RT_LEASE_READ_SQL, {"name": NAME})
        rows = list(cur.fetchall())
    if not rows:
        return None
    holder, taken_at, expires_at, live = rows[0]
    return {"holder": holder, "taken_at": taken_at, "expires_at": expires_at, "live": bool(live)}


def describe(conn: Any) -> str:
    """Who holds the lease, for a refusal: the holder, since when, and when it ends by itself."""
    try:
        row = current(conn)
    except Exception as exc:  # noqa: BLE001 — a refusal must still be raised without it
        return f"(its holder could not be read: {type(exc).__name__})"
    if row is None:
        return "(no holder on record)"
    if not row["live"]:
        return (f"free (last held by {row['holder']!r} until {row['expires_at']}); a take refused "
                "now means its scheduled_jobs row is paused or retired")
    return (f"held by {row['holder']!r} since {row['taken_at']}, until {row['expires_at']} — "
            f"if that writer is dead (its job ended, its worker restarted), pass "
            f"{RELEASE_ARG}={row['holder']} to end it first")


def release_stale(conn: Any, holder: str) -> dict[str, Any]:
    """End the lease `holder` left behind, and only that one: a lease someone else holds now,
    or none at all, is refused. For a writer that died holding it (review A11, B13)."""
    row = current(conn)
    if row is None or not row["live"]:
        return {"released": None, "reason": "no live lease to release"}
    if str(row["holder"]) != holder:
        raise SystemExit(
            f"{RELEASE_ARG}={holder}: rt_lease is {describe(conn)} — not that "
            "holder's; nothing was released and nothing was written.")
    release(conn, holder)
    return {"released": holder, "expires_at_was": str(row["expires_at"])}


def release_after(conn: Any, holder: str, original: BaseException | None, *,
                  fallback: Any | None = None,
                  connect: Callable[[], Any] | None = None) -> bool:
    """Release at the end of a run. A release that fails while `original` is on its way out
    is noted ON it instead of replacing it: the lease then expires by itself.

    `fallback` is a second, live connection the caller already holds — the one a raised pass
    halves its rate on (E930) — and a release that fails on `conn` (the backend the server
    terminated) is retried there (E931): a lease left to its TTL skips every pass of the next
    ~35 min as "leased". When that fails too, as a database restart makes it, `connect` (the
    caller's factory for a NEW connection) is tried, one connection every
    `NEW_CONNECTION_DELAY_S`, the first one too, for up to `NEW_CONNECTION_BUDGET_S`; each is
    closed, and ONE note on `original` (with none, one warning) says how it ended. The
    statement is keyed by holder, so it ends this holder's row and no other's. Returns whether
    a release ran."""
    try:
        release(conn, holder)
        return True
    except Exception as exc:
        failed: Exception = exc
        if fallback is not None:
            try:
                release(fallback, holder)
            except Exception as again:  # noqa: BLE001 — noted below, never raised instead
                failed = again
            else:
                if original is not None:
                    original.add_note(f"releasing rt_lease for {holder!r} failed on "
                                      f"the pass's connection ({type(exc).__name__}: {exc}); "
                                      "released on the fresh one")
                return True
        tried = ""
        if connect is not None:
            started = _clock()
            tries, last = _release_on_new_connections(connect, holder)
            spent = _clock() - started
            if last is None:
                landed = (f"releasing rt_lease for {holder!r} failed "
                          f"({type(exc).__name__}: {exc}); released on a new connection "
                          f"{spent:.0f} s later (try {tries})")
                if original is not None:
                    original.add_note(landed)
                else:
                    LOG.warning("AUTODEDUP: %s", landed)
                return True
            failed = last
            tried = f"{tries} new connection{'s' if tries != 1 else ''} over {spent:.0f} s, the last with "
        if original is None:
            raise failed
        original.add_note(f"releasing rt_lease for {holder!r} also failed "
                          f"({tried}{type(failed).__name__}: {failed}); it expires by itself")
        return False


def _release_on_new_connections(connect: Callable[[], Any],
                                holder: str) -> tuple[int, Exception | None]:
    """`_release_on_new` once every `NEW_CONNECTION_DELAY_S`, waiting before the first try too
    (the run's own connections have just failed), until one lands or the next would start past
    `NEW_CONNECTION_BUDGET_S`. Returns the tries made and the last failure, None once a release
    ran."""
    deadline = _clock() + NEW_CONNECTION_BUDGET_S
    tries = 0
    while True:
        _sleep(NEW_CONNECTION_DELAY_S)
        tries += 1
        try:
            _release_on_new(connect, holder)
            return tries, None
        except Exception as exc:  # noqa: BLE001 — the caller notes the last one
            if _clock() + NEW_CONNECTION_DELAY_S > deadline:
                return tries, exc


def _release_on_new(connect: Callable[[], Any], holder: str) -> None:
    """`release` on a connection `connect` opens, closed whatever the release did."""
    new_conn = connect()
    try:
        release(new_conn, holder)
    finally:
        close = getattr(new_conn, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
