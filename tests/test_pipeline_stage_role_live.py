"""The stage role (migration 734), against the replayed schema.

A board's viewing stage is its meaning, not its name: a signup's default board marks its
"3. For Visit"; at most one stage per account holds the role, never a closed one and never an
archived one; the API moves the role between stages in one transaction, and a card added at a
named stage writes one ledger event there. Gated on TEST_DATABASE_URL like the other live lanes.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests._tenant_scope import scoped

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="TEST_DATABASE_URL not set — live stage-role checks run only in the CI DB job",
)


@pytest.fixture(scope="module")
def svc() -> "Iterator[Any]":
    import psycopg

    conn = psycopg.connect(_DB_URL, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def tenant(svc: Any) -> "Iterator[dict[str, Any]]":
    """A user signed up through the auth.users trigger, with the default board it seeds."""
    user = uuid.uuid4()
    with svc.cursor() as cur:
        cur.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s)", (user, f"role-{user.hex[:8]}@test.local"))
        cur.execute("SELECT account_id FROM account_members WHERE user_id = %s", (user,))
        acc = cur.fetchone()[0]
        cur.execute("SELECT key, id FROM pipeline_stages WHERE account_id = %s", (acc,))
        stages = dict(cur.fetchall())
    try:
        yield {"user": user, "acc": acc, "stages": stages}
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id = %s", (acc,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (user,))


def _roles(svc: Any, acc: uuid.UUID) -> dict[str, str | None]:
    with svc.cursor() as cur:
        cur.execute("SELECT key, role FROM pipeline_stages WHERE account_id = %s", (acc,))
        return dict(cur.fetchall())


def test_the_default_board_marks_its_visit_stage(svc: Any, tenant: dict[str, Any]) -> None:
    roles = _roles(svc, tenant["acc"])
    assert {k for k, r in roles.items() if r} == {"3_for_visit"}
    assert roles["3_for_visit"] == "viewing"
    with scoped(tenant["user"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT key FROM pipeline_stages_public WHERE role = 'viewing'")
        assert cur.fetchall() == [("3_for_visit",)]


def test_the_schema_holds_one_open_live_viewing_stage(svc: Any, tenant: dict[str, Any]) -> None:
    import psycopg

    st = tenant["stages"]
    with svc.cursor() as cur:
        with pytest.raises(psycopg.errors.UniqueViolation), svc.transaction():
            cur.execute("UPDATE pipeline_stages SET role = 'viewing' WHERE id = %s", (st["2_for_call"],))
        with pytest.raises(psycopg.errors.CheckViolation), svc.transaction():
            cur.execute("UPDATE pipeline_stages SET is_terminal = true WHERE id = %s", (st["3_for_visit"],))
        with pytest.raises(psycopg.errors.CheckViolation), svc.transaction():
            cur.execute("UPDATE pipeline_stages SET archived_at = now() WHERE id = %s", (st["3_for_visit"],))
        with pytest.raises(psycopg.errors.CheckViolation), svc.transaction():
            cur.execute("UPDATE pipeline_stages SET role = 'offer' WHERE id = %s", (st["2_for_call"],))


def test_the_api_moves_the_role_and_adds_a_card_at_it(svc: Any, tenant: dict[str, Any]) -> None:
    from api import pipeline
    from api import schemas as s

    st, acc = tenant["stages"], tenant["acc"]
    with scoped(tenant["user"], method="PATCH") as conn:
        out = pipeline.update_stage(conn, st["2_for_call"], s.UpdateStageIn(role="viewing"), account_id=acc)
    assert out["role"] == "viewing"
    roles = _roles(svc, acc)
    assert roles["2_for_call"] == "viewing" and roles["3_for_visit"] is None

    with svc.cursor() as cur:
        cur.execute("INSERT INTO properties (settled_at) VALUES (now()) RETURNING id")
        prop = cur.fetchone()[0]
    with scoped(tenant["user"], method="POST") as conn:
        card = pipeline.add_card(
            conn, s.AddPipelineCardIn(property_id=prop, stage_id=st["2_for_call"]), account_id=acc,
        )
    assert card["added"] is True and card["stage_id"] == st["2_for_call"]
    with svc.cursor() as cur:
        cur.execute(
            "SELECT from_stage_id, to_stage_id FROM property_pipeline_events WHERE account_id = %s AND property_id = %s",
            (acc, card["property_id"]),
        )
        assert cur.fetchall() == [(None, st["2_for_call"])]

    with scoped(tenant["user"], method="DELETE") as conn:
        pipeline.remove_card(conn, card["property_id"], account_id=acc)
        pipeline.archive_stage(conn, st["2_for_call"], account_id=acc)
    assert _roles(svc, acc)["2_for_call"] is None
    with svc.cursor() as cur:
        cur.execute("DELETE FROM property_pipeline_events WHERE property_id = %s", (prop,))
        cur.execute("DELETE FROM properties WHERE id = %s", (prop,))
