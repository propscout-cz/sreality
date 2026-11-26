"""De-risk the auth path: mint a session, hit estate_prices once, report.

Run this FIRST (workflow_dispatch mode=probe) after setting the Seznam
secrets — it proves the login + cookie actually authorize the stats API
before a full ingestion run. Reads Praha's key from `ruian_translation`;
no DB writes.

  python -m scripts.price_stats_probe
"""

from __future__ import annotations

import datetime as dt
import logging

from scraper import price_stats_db as db
from scraper.price_stats_client import AuthExpiredError, PriceStatsClient
from scraper.price_stats_enums import ENTITY_MUNICIPALITY
from scraper.sreality_auth import get_session_cookies

LOG = logging.getLogger(__name__)

_PROBE_DATASET = {"category_main_cb": 1, "distance": 0}  # byty, no extra filters
PRAHA = 554782
PRAHA_KEY_SQL = f"SELECT source_key FROM ({db.FETCH_KEYS_SQL}) k WHERE obec_id = %s"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with db.connect() as conn:
        row = conn.execute(PRAHA_KEY_SQL, (PRAHA,)).fetchone()
    if not row:
        LOG.error("PROBE FAIL: ruian_translation holds no key for Praha (%d)", PRAHA)
        return 2
    LOG.info("Praha -> entity_id=%s", row[0])
    cookies = get_session_cookies(force_login=True)
    LOG.info("minted %d session cookies", len(cookies))
    client = PriceStatsClient(cookies=cookies)

    today = dt.date.today()
    start = f"{today.year - 1:04d}-{today.month:02d}"
    end = f"{today.year:04d}-{today.month:02d}"
    try:
        window = client.fetch_window(
            _PROBE_DATASET, entity_id=int(row[0]),
            entity_type=ENTITY_MUNICIPALITY, category_type_cb=1,
            default_from=start, default_to=end,
        )
    except AuthExpiredError:
        LOG.error("PROBE FAIL: estate_prices returned 401 — the session did "
                  "NOT authorize the stats API. Check the login flow / creds.")
        return 1

    months = window["months"]
    LOG.info("PROBE OK: estate_prices returned %d month(s); latest=%s",
             len(months), months[-1] if months else None)
    LOG.info("aggregates: %s", window["aggregates"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
