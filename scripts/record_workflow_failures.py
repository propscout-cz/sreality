"""record_workflow_failures.py — poll the Actions API, persist the production runs' record.

The jobs check's GitHub record. This poller (the first step of the hourly health canary,
verify_pipeline.yml) lists recently completed runs and keeps the PRODUCTION ones: event
`schedule`, or `push` on main. A pull request, a branch push or a dispatch is attended and stays
in the Actions tab, and the canary's own runs are never kept. A workflow that dispatches itself
is recorded by its cron run only: every chained hop is a dispatch. Each failed-ish run goes into
`workflow_failures` (migration 178) with its event, branch and reason line, idempotent via
ON CONFLICT (run_id) DO NOTHING; each workflow seen gets its `workflow_run_health` row: its last
success, its newest scheduled start times (a push has no cadence to be overdue against), its
Actions state and whether its file is on main.

Windowing is a HIGH-WATER-MARK CURSOR (`app_settings.workflow_failures_cursor`),
not a fixed lookback. The canary's cron is hourly but the GitHub Actions throttle
delivers about five runs a day, so the old fixed 40-min lookback silently dropped every
red run that completed in the uncovered gap (13 liveness reds → only 2 recorded).
The cursor advances to the newest run seen each poll and pages back until it
reaches the previous cursor, so no completed run is skipped. Crucially, it advances
ONLY on a poll that reached back past the cursor — a poll that hit the page cap
first has an uncovered window and holds the cursor instead (see _advance_cursor).

A dead poller is invisible in `workflow_failures` itself — it just stops
accumulating rows, which looks exactly like "nothing failed". The liveness signal is
the AGE of this cursor, the jobs check's `gh/poller` cell.

`cancelled` is recorded ONLY when the run ran at least
`CANCELLED_MIN_DURATION_MINUTES`: a `timeout-minutes` kill (which GitHub reports as
`cancelled`) runs to its budget, whereas a `cancel-in-progress` supersession is
killed in seconds. Without this gate, enabling cancel-in-progress anywhere would
flood the table with superseded runs.

Needs GITHUB_REPOSITORY + GITHUB_TOKEN (the default Actions token with
`actions: read`) + SUPABASE_DB_URL. A run from a branch other than main writes nothing.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.failure_signature import reason_line

LOG = logging.getLogger("record_workflow_failures")

# Must match the workflow's `name:` — the poller never records its own runs,
# or one red poll would keep re-alarming the surface it feeds.
MONITOR_WORKFLOW_NAME = "Monitoring: health canary (hourly)"

# Always-record conclusions. `cancelled` is handled conditionally (duration gate).
ALERT_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
CANCELLED_MIN_DURATION_MINUTES = 8   # >= this ⇒ a timeout-minutes kill, not a supersession
BOOTSTRAP_MINUTES = 120              # first-ever run: how far back to seed the cursor
CURSOR_OVERLAP_MINUTES = 5           # re-scan slightly before the cursor (ON CONFLICT makes it safe)
PER_PAGE = 100
# 1,000 runs. Raised from 5 (500) in W0.2 of the reliability program: at ~100 failures/day
# and a real inter-poll gap of 80–256 min, 500 was close enough to the ceiling that the cap
# was reachable — and hitting it used to drop runs permanently (see _advance_cursor). Cost is
# bounded and cheap: at most 10 list requests per poll against the Actions API's 1,000/hour
# per-repo budget for GITHUB_TOKEN, and the loop still stops as soon as a page reaches back
# past `since`, so the extra pages are only ever fetched when they are actually needed.
MAX_PAGES = 10
CURSOR_KEY = "workflow_failures_cursor"
# The scheduled start-gap history the jobs check reads (its p90 arm needs 10); no backfill, it
# accumulates.
RECENT_STARTS_KEPT = 20

API_TIMEOUT_S = 30

_REPO_ROOT = Path(__file__).resolve().parents[1]

# One row per production workflow, born on its first production run (a failure too). A stale page
# never lowers last_success_at (greatest() also skips a failure-only row's NULL), and the starts
# are a union with the stored ones.
_UPSERT_HEALTH_SQL = """
INSERT INTO workflow_run_health
  (workflow_path, workflow_name, last_success_at, last_success_run_id, recent_starts, updated_at)
VALUES (%(path)s, %(name)s, %(success_at)s, %(success_run_id)s, %(starts)s::timestamptz[], now())
ON CONFLICT (workflow_path) DO UPDATE SET
  workflow_name = excluded.workflow_name,
  last_success_at = greatest(workflow_run_health.last_success_at, excluded.last_success_at),
  last_success_run_id = CASE
    WHEN excluded.last_success_at >=
         coalesce(workflow_run_health.last_success_at, '-infinity'::timestamptz)
    THEN excluded.last_success_run_id
    ELSE workflow_run_health.last_success_run_id END,
  recent_starts = array(
    SELECT DISTINCT s FROM unnest(workflow_run_health.recent_starts || excluded.recent_starts) AS u(s)
    ORDER BY s DESC LIMIT %(kept)s),
  updated_at = now()
"""

_INSERT_FAILURE_SQL = """
INSERT INTO workflow_failures
  (run_id, workflow_name, workflow_path, conclusion, run_started_at, html_url, event, head_branch)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id) DO NOTHING
"""

# A row is a job's once a production run claimed it (on_main set). A legacy row of a dispatch-only
# workflow never is, so it never gets a cell.
_CLAIMED_PATHS_SQL = "SELECT workflow_path FROM workflow_run_health WHERE on_main IS NOT NULL"

# Refreshed on every poll, not only for the workflows seen: a disabled workflow or one whose file
# left main has no new runs to report it.
_REFRESH_FACTS_SQL = """
UPDATE workflow_run_health AS h
SET on_main = v.on_main, state = coalesce(v.state, h.state), updated_at = now()
FROM unnest(%s::text[], %s::boolean[], %s::text[]) AS v(workflow_path, on_main, state)
WHERE h.workflow_path = v.workflow_path
  AND (h.on_main IS DISTINCT FROM v.on_main OR h.state IS DISTINCT FROM coalesce(v.state, h.state))
"""

_STORE_REASON_SQL = "UPDATE workflow_failures SET reason = %s WHERE run_id = %s"


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_minutes(run: dict[str, Any]) -> float | None:
    """Run wall-clock, using `updated_at` as the completion proxy (the list endpoint
    has no completed-at field)."""
    started = parse_ts(run.get("run_started_at"))
    completed = parse_ts(run.get("updated_at"))
    if started is None or completed is None:
        return None
    return (completed - started).total_seconds() / 60.0


def is_production_run(run: dict[str, Any]) -> bool:
    """A job's record: a scheduled run or a push to main, never the canary's own run."""
    if run.get("name") == MONITOR_WORKFLOW_NAME:
        return False
    event = run.get("event")
    return event == "schedule" or (event == "push" and run.get("head_branch") == "main")


def select_failed_runs(
    runs: list[dict[str, Any]],
    *,
    since: datetime,
    cancelled_min_duration: float = CANCELLED_MIN_DURATION_MINUTES,
    started_check: Any = None,
) -> list[dict[str, Any]]:
    """Pure filter over parsed `/actions/runs` JSON (no network, testable).

    Keeps production runs (`is_production_run`) completed at/after `since` (completion ≈
    `updated_at`) with an alerting conclusion. `cancelled` is kept only when the run ran
    >= `cancelled_min_duration` minutes — a timeout-minutes kill, not a quick supersession.

    `started_check(run_id) -> bool` closes the duration gate's blind spot: a superseded run
    that sat QUEUED >= 8 min before cancel-in-progress killed it never ran at all, yet its
    run_started_at (= queue entry) makes the duration look like a timeout kill (8 of 10
    RealityMix/iDNES drain cancels on 2026-07-11 were falsely recorded this way). When
    provided, a long-looking cancelled run is kept only if at least one JOB actually
    started; None (e.g. in pure tests) keeps the duration-only behavior.
    """
    out: list[dict[str, Any]] = []
    for run in runs:
        if not is_production_run(run):
            continue
        completed_at = parse_ts(run.get("updated_at"))
        if completed_at is None or completed_at < since:
            continue
        conclusion = run.get("conclusion")
        if conclusion in ALERT_CONCLUSIONS:
            pass
        elif conclusion == "cancelled":
            dur = _duration_minutes(run)
            if dur is None or dur < cancelled_min_duration:
                continue  # supersession / quick cancel — not a real failure
            if started_check is not None and not started_check(int(run["id"])):
                continue  # never-started queued run superseded — not a failure
        else:
            continue
        out.append(
            {
                "run_id": int(run["id"]),
                "workflow_name": run.get("name") or "(unnamed)",
                "workflow_path": run.get("path"),
                "conclusion": conclusion,
                "run_started_at": parse_ts(run.get("run_started_at")),
                "html_url": run.get("html_url"),
                "event": run.get("event"),
                "head_branch": run.get("head_branch"),
            }
        )
    return out


def select_workflow_health(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One workflow_run_health row per production workflow in the pages, starts from schedules."""
    rows: dict[str, dict[str, Any]] = {}
    for run in runs:
        path = run.get("path")
        if not path or not is_production_run(run):
            continue
        # The API lists newest first, so the first run seen names the workflow.
        row = rows.setdefault(path, {
            "workflow_path": path, "workflow_name": run.get("name") or "(unnamed)",
            "last_success_run_id": None, "last_success_at": None, "recent_starts": set(),
        })
        started = parse_ts(run.get("run_started_at"))
        # A push lands when someone merges; only a schedule has a gap the jobs check can judge.
        if started is not None and run.get("event") == "schedule":
            row["recent_starts"].add(started)
        if run.get("conclusion") == "success" and (
            row["last_success_run_id"] is None
            or (started is not None
                and (row["last_success_at"] is None or started > row["last_success_at"]))
        ):
            row["last_success_run_id"] = int(run["id"])
            row["last_success_at"] = started
    for row in rows.values():
        row["recent_starts"] = sorted(row["recent_starts"], reverse=True)[:RECENT_STARTS_KEPT]
    return list(rows.values())


def _gh_request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _fetch_runs_page(repo: str, token: str, page: int) -> list[dict[str, Any]]:
    url = (
        f"https://api.github.com/repos/{repo}/actions/runs"
        f"?status=completed&per_page={PER_PAGE}&page={page}"
    )
    with urllib.request.urlopen(_gh_request(url, token), timeout=API_TIMEOUT_S) as resp:
        payload = json.load(resp)
    return payload.get("workflow_runs", []) or []


def _run_started_any_job(repo: str, token: str, run_id: int) -> bool:
    """Did any job of this run actually START? A cancel-in-progress supersession of a
    still-QUEUED run has an empty jobs list (or jobs with no started_at) — only called for
    the rare cancelled-and-long candidates, so the extra API cost is a handful per poll.
    On API failure, err on the side of recording (True) — a dropped real timeout-kill is
    worse than one noisy row."""
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?per_page=50"
    try:
        with urllib.request.urlopen(_gh_request(url, token), timeout=API_TIMEOUT_S) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001 — network flake must not drop a real failure
        LOG.warning("jobs fetch failed for run %d (%s); recording anyway", run_id, exc)
        return True
    jobs = payload.get("jobs", []) or []
    return any(j.get("started_at") for j in jobs)


def _fetch_workflow_states(repo: str, token: str) -> dict[str, str] | None:
    """Each workflow's Actions state by path (`active`, `disabled_manually`, …); None if unread."""
    states: dict[str, str] = {}
    try:
        for page in range(1, MAX_PAGES + 1):
            url = (f"https://api.github.com/repos/{repo}/actions/workflows"
                   f"?per_page={PER_PAGE}&page={page}")
            with urllib.request.urlopen(_gh_request(url, token), timeout=API_TIMEOUT_S) as resp:
                batch = json.load(resp).get("workflows") or []
            states.update({w["path"]: w["state"] for w in batch if w.get("path") and w.get("state")})
            if len(batch) < PER_PAGE:
                break
    except Exception as exc:  # noqa: BLE001 — a missed listing keeps the stored states
        LOG.warning("workflow states fetch failed (%s); stored states kept", exc)
        return None
    return states


def _on_main(workflow_path: str) -> bool:
    """Is the workflow's file in this checkout (main's: only a run from main writes)?"""
    return (_REPO_ROOT / workflow_path).is_file()


def _refresh_workflow_facts(conn: Any, states: dict[str, str] | None, seen: set[str]) -> int:
    """Set state and on_main on the claimed rows and the rows this poll saw; rows changed."""
    with conn.cursor() as cur:
        cur.execute(_CLAIMED_PATHS_SQL)
        paths = sorted(seen | {row[0] for row in cur.fetchall()})
        cur.execute(_REFRESH_FACTS_SQL, (
            paths,
            [_on_main(p) for p in paths],
            [(states or {}).get(p) for p in paths],
        ))
        return cur.rowcount


# --- the reason line: read WHY a run went red ------------------------------
#
# A red run's row says which workflow failed, not why. This pass pulls the failed job's
# log tail and stores its reason line (scripts/failure_signature.reason_line), which the
# jobs cell shows. A timeout kill with no Python exception still has its `##[error]`
# annotation; a startup_failure ran no job, so it has no log and its reason stays NULL.

MAX_LOG_FETCHES = 25          # Actions API budget guard: one poll, at most this many logs
LOG_TAIL_BYTES = 64 * 1024    # measured job logs run 27KB-172KB; a drain log is far larger
_JOB_FAIL_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled", "startup_failure"})

# Wall-clock ceiling for the whole reason pass. MAX_LOG_FETCHES alone is not a time
# budget: each fetch is up to 4 round trips (jobs list, the 302, then _fetch_blob_tail's
# two ranged reads) at API_TIMEOUT_S=30 each, so 25 of them is a ~50-minute worst case
# inside the canary's poller step, `timeout-minutes: 5`. A killed step is not caught by any try/except.
REASON_PASS_BUDGET_S = 90.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow, so the caller can re-request the target BARE.

    `/actions/jobs/{id}/logs` answers 302 to a SAS-signed Azure blob URL, and CPython's
    redirect handler copies every header — `Authorization: Bearer` included — onto the
    redirected request. That both leaks the GITHUB_TOKEN to a third-party host and does
    not even work: Azure answers 401 when the bearer header is present."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def select_failed_job(jobs: list[dict[str, Any]]) -> int | None:
    """The id of the first failed job, whose log is worth reading (pure, testable)."""
    for job in jobs:
        if job.get("conclusion") in _JOB_FAIL_CONCLUSIONS:
            return int(job["id"])
    return None


def _fetch_failed_job(repo: str, token: str, run_id: int) -> int | None:
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?per_page=50"
    try:
        with urllib.request.urlopen(_gh_request(url, token), timeout=API_TIMEOUT_S) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001 — no log is a NULL reason, not a crash
        LOG.warning("jobs fetch failed for run %d (%s)", run_id, exc)
        return None
    return select_failed_job(payload.get("jobs", []) or [])


def _fetch_blob_tail(url: str, cap_bytes: int) -> str | None:
    """Read the last `cap_bytes` of a signed blob, BARE (no auth header).

    Two requests on purpose: Azure ignores suffix ranges — `Range: bytes=-500` came back
    200 with the whole 27KB body — so the length is learned from a 1-byte closed range
    first, then a real closed range is asked for."""
    total: int | None = None
    try:
        head = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(head, timeout=API_TIMEOUT_S) as resp:
            content_range = resp.headers.get("Content-Range") or ""
            resp.read(1)
        if "/" in content_range:
            total = int(content_range.rsplit("/", 1)[1])
    except Exception as exc:  # noqa: BLE001 — 404 BlobNotFound / expired SAS are routine
        LOG.info("log blob probe failed (%s)", exc)
        return None
    if not total:
        return None
    start = max(0, total - cap_bytes)
    try:
        req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{total - 1}"})
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_S) as resp:
            body = resp.read(cap_bytes + 1024)
    except Exception as exc:  # noqa: BLE001
        LOG.info("log blob read failed (%s)", exc)
        return None
    return body.decode("utf-8", "ignore")


def fetch_job_log(repo: str, token: str, job_id: int, *, cap_bytes: int = LOG_TAIL_BYTES) -> str | None:
    """Plain-text tail of one job's log, or None. Run-level `/runs/{id}/logs` returns a
    ZIP; only the job-level endpoint is text."""
    url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
    try:
        with _NO_REDIRECT_OPENER.open(_gh_request(url, token), timeout=API_TIMEOUT_S) as resp:
            return resp.read(cap_bytes).decode("utf-8", "ignore")
    except urllib.error.HTTPError as err:
        if err.code in (301, 302, 303, 307, 308):
            location = err.headers.get("Location")
            return _fetch_blob_tail(location, cap_bytes) if location else None
        LOG.info("log fetch for job %d returned HTTP %s", job_id, err.code)
        return None
    except Exception as exc:  # noqa: BLE001
        LOG.info("log fetch for job %d failed (%s)", job_id, exc)
        return None


def record_reasons(
    conn: Any, repo: str, token: str, failures: list[dict[str, Any]],
    *, budget_seconds: float = REASON_PASS_BUDGET_S,
) -> dict[str, int]:
    """Store each red's reason line within the fetch and wall-clock budgets; past them it stays NULL."""
    stats = {"stored": 0, "fetched": 0, "unreadable": 0, "skipped": 0, "expired": 0}
    budget = MAX_LOG_FETCHES
    deadline = time.monotonic() + float(budget_seconds)
    for f in failures:
        if time.monotonic() >= deadline:
            stats["expired"] += 1
            continue
        if budget <= 0:
            stats["skipped"] += 1
            continue
        budget -= 1
        job_id = _fetch_failed_job(repo, token, f["run_id"])
        text = fetch_job_log(repo, token, job_id) if job_id is not None else None
        stats["fetched"] += text is not None
        reason = reason_line(text)
        if reason is None:
            stats["unreadable"] += 1
            continue
        with conn.cursor() as cur:
            cur.execute(_STORE_REASON_SQL, (reason, f["run_id"]))
        stats["stored"] += 1
    return stats


def _read_cursor(conn: Any) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT value #>> '{}' FROM app_settings WHERE key = %s", (CURSOR_KEY,))
        row = cur.fetchone()
    return parse_ts(row[0]) if row and row[0] else None


def _write_cursor(conn: Any, ts: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app_settings (key, value, updated_at) "
            "VALUES (%s, to_jsonb(%s::text), now()) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()",
            (CURSOR_KEY, ts.isoformat()),
        )


def _fetch_since_cursor(
    repo: str, token: str, since: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    """Page newest-first until a page reaches older than `since` (full coverage) or the
    page cap. Returns (runs, reached_since)."""
    runs: list[dict[str, Any]] = []
    reached_since = False
    for page in range(1, MAX_PAGES + 1):
        batch = _fetch_runs_page(repo, token, page)
        if not batch:
            reached_since = True
            break
        runs.extend(batch)
        page_oldest = min(
            (parse_ts(r.get("updated_at")) for r in batch if parse_ts(r.get("updated_at"))),
            default=None,
        )
        if len(batch) < PER_PAGE or (page_oldest is not None and page_oldest < since):
            reached_since = True
            break
    return runs, reached_since


def _advance_cursor(
    completions: list[datetime], *, reached_since: bool,
) -> datetime | None:
    """Where the high-water mark goes after a poll. None = leave it where it is.

    Only a poll that reached back past `since` has covered its whole window, and only
    that poll may move the cursor — to the newest completion it saw.

    When the page cap is hit first, the pages fetched are the NEWEST N runs, so every
    completion seen is *newer* than `since` and the uncovered runs are older than all
    of them. The old code advanced to `min(completions)` here, believing it was
    "crawling to oldest-seen so the gap is picked up next poll" — but the next poll
    filters on `completed_at < since`, and the skipped runs are older than the new
    cursor, so they were dropped **permanently**. That is why only 2 of the 6 portals
    that failed on 2026-08-26 were ever recorded. Holding the cursor keeps the window
    open instead: the poll re-scans, and the gap closes as soon as volume drops back
    under the page budget.
    """
    if not reached_since or not completions:
        return None
    return max(completions)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not repo or not token or not db_url:
        print(
            "ERROR: GITHUB_REPOSITORY, GITHUB_TOKEN and SUPABASE_DB_URL must be set.",
            file=sys.stderr,
        )
        return 2
    # The registry's rule: a dispatch from a branch checks that branch out, so its on_main would
    # describe the branch, and its poll would move main's cursor.
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    if ref != "main":
        LOG.info("WORKFLOW_FAILURES not recorded: this run is from %s, not main", ref)
        return 0

    import psycopg

    inserted = 0
    newly_recorded: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    with psycopg.connect(db_url, autocommit=True, prepare_threshold=None) as conn:
        now = datetime.now(timezone.utc)
        cursor_ts = _read_cursor(conn) or (now - timedelta(minutes=BOOTSTRAP_MINUTES))
        since = cursor_ts - timedelta(minutes=CURSOR_OVERLAP_MINUTES)

        runs, reached_since = _fetch_since_cursor(repo, token, since)
        failed = select_failed_runs(
            runs, since=since,
            started_check=lambda run_id: _run_started_any_job(repo, token, run_id))
        # Not windowed: every production run in the pages is history, and the upsert only ever
        # merges (greatest() for the last success, a union for the starts).
        health = select_workflow_health(runs)
        states = _fetch_workflow_states(repo, token)

        with conn.cursor() as cur:
            for h in health:
                cur.execute(_UPSERT_HEALTH_SQL, {
                    "path": h["workflow_path"],
                    "name": h["workflow_name"],
                    "success_at": h["last_success_at"],
                    "success_run_id": h["last_success_run_id"],
                    "starts": h["recent_starts"],
                    "kept": RECENT_STARTS_KEPT,
                })
            for f in failed:
                cur.execute(
                    _INSERT_FAILURE_SQL,
                    (
                        f["run_id"],
                        f["workflow_name"],
                        f["workflow_path"],
                        f["conclusion"],
                        f["run_started_at"],
                        f["html_url"],
                        f["event"],
                        f["head_branch"],
                    ),
                )
                if cur.rowcount:
                    inserted += cur.rowcount
                    newly_recorded.append(f)
        facts_changed = _refresh_workflow_facts(conn, states, {h["workflow_path"] for h in health})

        # The cursor advance goes FIRST, before the reason pass. record_reasons talks to
        # the Actions API and to Azure blob storage inside a step with `timeout-minutes: 5`,
        # and a step kill is not an exception the try/except below can catch — so anything
        # slow there would cost the poller its cursor advance, which is its input-coverage
        # guarantee. Reasons annotate rows already committed above; the cursor is not.
        completions = [c for c in (parse_ts(r.get("updated_at")) for r in runs) if c is not None]
        new_cursor = _advance_cursor(completions, reached_since=reached_since)
        if new_cursor is not None:
            _write_cursor(conn, new_cursor)
        elif completions:
            gap_min = (min(completions) - since).total_seconds() / 60.0
            LOG.error(
                "WORKFLOW_FAILURES page cap (%d pages / %d runs) hit before reaching the "
                "cursor: runs completed between %s and %s were NOT fetched (a %.0f-min "
                "window). Holding the cursor so they stay eligible; raise MAX_PAGES if this "
                "repeats.",
                MAX_PAGES, MAX_PAGES * PER_PAGE, since, min(completions), gap_min,
            )

        # Only NEWLY inserted rows: a re-scanned run inside the cursor overlap had its turn.
        try:
            reasons = record_reasons(conn, repo, token, newly_recorded)
        except Exception as exc:  # noqa: BLE001 — the poller's own job comes first
            LOG.warning("reason pass failed: %r", exc)

    LOG.info(
        "WORKFLOW_FAILURES scanned=%d failed=%d inserted=%d workflows=%d facts_changed=%d "
        "reached_since=%s",
        len(runs), len(failed), inserted, len(health), facts_changed, reached_since,
    )
    if reasons:
        LOG.info("WORKFLOW_REASONS %s", " ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
