"""The `notifications` alarm (notifications push W4, PROGRAM.md § 3.9), on the worker: does every push leave?

Four cells over the push ledger (migration 654):
  push/stall         Problem: a pending or failed delivery due for over 10 min (the lane is not claiming).
  push/recall        Problem: a VISIBLE unseen event of the last day whose account has a live device registered
                     before it, and no delivery row for that pair 5 min after the event (fan-out lost a push).
  push/failures      Problem: 3 or more deliveries failed for good in 24 h (Apple's reason words in the message).
  push/unconfigured  Watch: the push lane is braked (no VAPID_PRIVATE_KEY on the worker) while a live device waits.
While the lane is braked, recall is not judged: the brake is the cell that says so. The three-strike rule (iOS drops
an origin's subscriptions after silent pushes) has no server signal; it is prevented in sw.js, never measured here.
The statements live beside the pass they judge (api/push_delivery.py) and are imported when the check runs: the
worker has the api extra, while GitHub's canary imports this registry with the base dependencies alone.
"""

from __future__ import annotations

from typing import Any

from scripts.checks.clock import fetchone
from scripts.checks.common import BEAT_FRESH_S, WORKER, ago, as_utc, cell, now, result

# The push lane's heartbeat entry: what its newest pass recorded, and when the worker last beat.
_PUSH_LANE_SQL = """
select beat_at, details -> 'push' -> 'last' from worker_heartbeats where worker = %(worker)s
"""


def _braked(conn: Any, at: Any) -> tuple[bool | None, str | None]:
    """Whether the push lane's newest pass found no VAPID key; None (and why) when the heartbeat cannot say."""
    row = fetchone(conn, _PUSH_LANE_SQL, {"worker": WORKER})
    if row is None:
        return None, "no realtime-worker heartbeat on record"
    beat_at, last = as_utc(row[0]), row[1]
    if beat_at is None or (at - beat_at).total_seconds() > BEAT_FRESH_S:
        return None, "the worker's heartbeat is stale; the watcher's worker cell judges that"
    if not isinstance(last, dict):
        return None, "the push lane has not completed a pass in this worker"
    return last.get("unconfigured") is True, None


def check_notifications(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Is every visible event pushed to the devices registered before it, and does the ledger drain?"""
    from api import push_delivery as pd

    at = now(conn)
    stall_min = int(thresholds["notifications_stall_min"])
    grace_min = int(thresholds["notifications_recall_grace_min"])
    fail_at = int(thresholds["notifications_failures_fail"])
    braked, unread = _braked(conn, at)
    devices = int(fetchone(conn, pd.LIVE_DEVICES_SQL)[0] or 0)
    not_measured: dict[str, str] = {}
    cells: list[dict[str, Any]] = []

    stalled, oldest = fetchone(conn, pd.STALL_SQL, {"stall_min": stall_min})
    stalled = int(stalled or 0)
    cells.append(cell(
        "push/stall", "fail" if stalled else "ok",
        f"{stalled} deliveries due for over {stall_min} min (the oldest {ago(float(oldest))})" if stalled
        else f"no delivery due for over {stall_min} min", value=stalled, unit="rows",
        facts={"devices_live": devices}))

    if braked:
        cells.append(cell("push/recall", "ok", "not judged while the lane is braked (push/unconfigured)",
                          facts={"braked": True}))
    else:
        missing, first = fetchone(conn, pd.RECALL_SQL, {"grace_min": grace_min})
        missing = int(missing or 0)
        cells.append(cell(
            "push/recall", "fail" if missing else "ok",
            f"{missing} (event, device) pairs have no delivery {grace_min} min after the event (the oldest "
            f"{ago(float(first))})" if missing
            else f"every visible event reached its devices' ledger within {grace_min} min",
            value=missing, unit="pairs"))

    failed, reasons = fetchone(conn, pd.FAILURES_SQL)
    failed = int(failed or 0)
    why = f" ({', '.join(sorted(reasons))})" if reasons else ""
    cells.append(cell(
        "push/failures", "fail" if failed >= fail_at else "ok",
        f"{failed} pushes failed for good in 24 h{why}", value=failed, unit="rows",
        facts={"fail_at": fail_at, "reasons": sorted(reasons or [])}))

    if braked is None:
        not_measured["unconfigured"] = unread or "the push lane's state is unknown"
    elif braked and devices:
        cells.append(cell("push/unconfigured", "warn",
                          f"push is braked (no VAPID_PRIVATE_KEY on the worker) while {devices} devices are "
                          "registered: their alerts reach the bell only", value=devices, unit="devices"))
    else:
        cells.append(cell("push/unconfigured", "ok",
                          "braked, and no device is registered" if braked else "configured",
                          value=devices, unit="devices"))
    return result("notifications", cells, not_measured)
