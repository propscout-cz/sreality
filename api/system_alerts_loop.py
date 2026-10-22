"""The API's system-alerts loop: the one teller runs the health ladder every minute."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from api import dependencies as deps
from toolkit import system_alerts

LOG = logging.getLogger(__name__)

# A constant, not a setting: the ladder's own rungs decide when to tell and a pass over
# unchanged state is a no-op, so the tick only bounds how late a notice can be.
SYSTEM_ALERTS_INTERVAL_SECONDS = 60


def _reconcile_once() -> None:
    """One ladder pass on its own service-role connection; a failure is logged, never raised."""
    try:
        with deps.open_background_conn() as conn:
            counts = system_alerts.reconcile(conn)
        if any(counts.values()):
            LOG.info("system alerts: %s", counts)
    except Exception:  # noqa: BLE001 - a DB outage or a bad pass must not end the loop
        LOG.exception("system alerts pass failed")


async def system_alerts_loop(stop_event: asyncio.Event) -> None:
    """Reconcile at once, then every SYSTEM_ALERTS_INTERVAL_SECONDS, until stop_event is set."""
    LOG.info("system alerts loop starting")
    while not stop_event.is_set():
        await asyncio.to_thread(_reconcile_once)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=SYSTEM_ALERTS_INTERVAL_SECONDS)
    LOG.info("system alerts loop stopped")
