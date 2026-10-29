"""Shared setup for the live property-identity tests: the `REQUIRED_DB` guard, `db_url()`, and seed
helpers for properties, adverts, accounts and the operator's property-anchored state. Every seed lands
inside the test's transaction (the rollback-always `cur` fixture in tests/conftest.py) and rolls back;
only `signups` commits, and deletes what it made."""

from __future__ import annotations

import contextlib
import itertools
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"

# The migrations lane sets DB_RAILS_REQUIRED=1, so a lane that loses its URL runs the tests
# and `db_url()` fails them instead of reporting a green skip.
REQUIRED_DB = pytest.mark.skipif(
    not DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job",
)

OP = "ci-operator@replay.local"
_SREALITY_IDS = itertools.count(9_650_000_001)


def db_url() -> str:
    if not DB_URL:
        pytest.fail(
            "DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is "
            "misconfigured and these tests would otherwise have skipped green."
        )
    return DB_URL


def _property(cur: Any) -> int:
    """Settled at birth: a seed is a published property, so browse_projection serves it (migration 669)."""
    cur.execute("INSERT INTO properties (settled_at) VALUES (now()) RETURNING id")
    return int(cur.fetchone()[0])


def _advert(cur: Any, pid: int, *, source: str, price: int | None = 5_000_000, active: bool = True,
            condition: str | None = None, levels: tuple[Any, Any] = (None, None), **facts: Any) -> int:
    """A byt for sale, 70 m², unless `facts` (more `listings` columns) say otherwise."""
    row = {"sreality_id": next(_SREALITY_IDS) if source == "sreality" else None, "source": source,
           "source_id_native": f"lp-{uuid.uuid4()}", "category_main": "byt", "category_type": "prodej",
           "price_czk": price, "area_m2": 70, "is_active": active, "property_id": pid, "condition": condition,
           "building_condition_level": levels[0], "apartment_condition_level": levels[1], **facts}
    cur.execute(
        f"INSERT INTO listings (raw_json, {', '.join(row)}) VALUES ('{{}}'::jsonb, {', '.join(['%s'] * len(row))}) "
        "RETURNING id", list(row.values()),
    )
    return int(cur.fetchone()[0])


def _snapshot(cur: Any, listing_id: int, price: int | None, hours_ago: float) -> int:
    cur.execute(
        "INSERT INTO listing_snapshots (listing_id, scraped_at, price_czk, content_hash, raw_json) "
        "VALUES (%s, now() - make_interval(secs => %s), %s, %s, '{}'::jsonb) RETURNING id",
        (listing_id, hours_ago * 3600, price, f"lp-{uuid.uuid4()}"),
    )
    return int(cur.fetchone()[0])


def _recompute(cur: Any, pid: int) -> None:
    from scripts.recompute_property_stats import _RECOMPUTE_SCOPED_SQL

    cur.execute(_RECOMPUTE_SCOPED_SQL, {"ids": [pid]})


def _account(cur: Any) -> uuid.UUID:
    """A bare tenant: no auth user, no membership, no seeded stages or collections."""
    cur.execute(
        "INSERT INTO accounts (kind, name) VALUES ('personal', %s) RETURNING id",
        (f"lp-{uuid.uuid4().hex[:8]}",),
    )
    return cur.fetchone()[0]


def _stage(cur: Any, acc: uuid.UUID, *, position: int, terminal: bool = False) -> int:
    cur.execute(
        "INSERT INTO pipeline_stages (account_id, key, label, position, is_terminal) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (acc, f"lp-{uuid.uuid4().hex[:12]}", f"lp {position}", position, terminal),
    )
    return int(cur.fetchone()[0])


def _collection(cur: Any, acc: uuid.UUID) -> int:
    cur.execute(
        "INSERT INTO collections (account_id, name) VALUES (%s, %s) RETURNING id",
        (acc, f"lp-{uuid.uuid4().hex[:12]}"),
    )
    return int(cur.fetchone()[0])


def _tag(cur: Any, acc: uuid.UUID) -> int:
    # `color` is a named-palette CHECK, not a hex string.
    cur.execute(
        "INSERT INTO tags (account_id, name, color) VALUES (%s, %s, 'slate') RETURNING id",
        (acc, f"lp-{uuid.uuid4().hex[:12]}"),
    )
    return int(cur.fetchone()[0])


@contextlib.contextmanager
def signups(svc: Any, prefix: str, *keys: str) -> Iterator[dict[str, Any]]:
    """One user per key signed up through the real trigger (migration 612: the account, its owner membership, the
    default board, the monitoring collection and the trial), committed on the autocommit `svc` so a connection of
    its own sees them: `key` is the user, `key_acc` their account. Deleted on exit."""
    users = {key: uuid.uuid4() for key in keys}
    with svc.cursor() as cur:
        for key, user in users.items():
            cur.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                        (user, f"{prefix}-{key}-{user.hex[:8]}@test.local"))
        cur.execute("SELECT user_id, account_id FROM account_members WHERE user_id = ANY(%s)", (list(users.values()),))
        account = dict(cur.fetchall())
    try:
        yield {**users, **{f"{key}_acc": account[user] for key, user in users.items()}}
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id = ANY(%s)", (list(account.values()),))
            cur.execute("DELETE FROM auth.users WHERE id = ANY(%s)", (list(users.values()),))
