"""Saved Stats dashboards and their read-only shares (migration 647), proven on the replayed schema.

Three tenants: A owns a working board and a named dashboard shared with B; C is a bystander. Every
tenant statement runs through `tests/_tenant_scope.scoped` (SET LOCAL ROLE authenticated + the
caller's JWT claims, exactly as api.tenant_pool.tenant_conn scopes a request), and where the API has a
function for it the test calls THAT (api/saved_sets.py, api/routes/stats_dashboards.py,
api/shares.py), so the SQL under test is the SQL that ships. Setup and read-backs run as the owner
login, which RLS never binds; a share is written there too, as the API writes it on the service role.

Skip behaviour: skips without TEST_DATABASE_URL like its siblings; CI's migrations lane sets
DB_RAILS_REQUIRED=1, so a lane that loses its database goes RED instead of green.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any, NamedTuple

import pytest

from tests._tenant_scope import scoped

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"

pytestmark = pytest.mark.skipif(
    not _DB_URL and not _REQUIRED,
    reason="TEST_DATABASE_URL not set — this rail runs in CI's migrations lane",
)


class _Tenant(NamedTuple):
    acc: uuid.UUID
    user: uuid.UUID
    email: str


class _World(NamedTuple):
    working: uuid.UUID
    named: uuid.UUID


@pytest.fixture(scope="module")
def svc() -> Iterator[Any]:
    import psycopg

    conn = psycopg.connect(_DB_URL, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def tenants(svc: Any) -> Iterator[dict[str, _Tenant]]:
    """Three accounts, each with one confirmed auth user; the on-signup trigger is disabled around
    the auth.users insert, as in tests/test_collection_shares_live.py."""
    made: dict[str, _Tenant] = {}
    with svc.cursor() as cur:
        for key in ("a", "b", "c"):
            user = uuid.uuid4()
            cur.execute(
                "INSERT INTO accounts (kind, name) VALUES ('personal', %s) RETURNING id",
                (f"board-{key}-{user.hex[:8]}",),
            )
            made[key] = _Tenant(cur.fetchone()[0], user, f"board-{key}-{user.hex[:8]}@test.local")
        cur.execute("ALTER TABLE auth.users DISABLE TRIGGER USER")
        try:
            for t in made.values():
                cur.execute(
                    "INSERT INTO auth.users (id, email, email_confirmed_at) VALUES (%s, %s, now())",
                    (t.user, t.email),
                )
        finally:
            cur.execute("ALTER TABLE auth.users ENABLE TRIGGER USER")
        for t in made.values():
            cur.execute(
                "INSERT INTO account_members (account_id, user_id, role) VALUES (%s, %s, 'owner')",
                (t.acc, t.user),
            )
    try:
        yield made
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id = ANY(%s)", ([t.acc for t in made.values()],))
            cur.execute("DELETE FROM auth.users WHERE id = ANY(%s)", ([t.user for t in made.values()],))


@pytest.fixture
def world(svc: Any, tenants: dict[str, _Tenant]) -> Iterator[_World]:
    """A's working board (saved through the API's upsert) and A's named dashboard (through the API's
    create), shared with B. Fresh per test."""
    from api import saved_sets
    from api.routes.stats_dashboards import DASHBOARDS, save_working_board, working_board

    a = tenants["a"]
    with scoped(a.user) as conn:
        save_working_board(conn, [{"id": "w1"}], account_id=a.acc)
        working = uuid.UUID(working_board(conn, account_id=a.acc)["id"])
        named = saved_sets.create(conn, DASHBOARDS, {
            "name": "Praha byty", "charts": [{"id": "c1"}],
            "filter_spec": {"filters": {"categoryMain": "byt"}}, "color": "sage",
        }, account_id=a.acc)
    _share(svc, uuid.UUID(named["id"]), tenants["b"])
    try:
        yield _World(working, uuid.UUID(named["id"]))
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM stats_dashboards WHERE account_id = ANY(%s)",
                        ([t.acc for t in tenants.values()],))


def _share(svc: Any, dashboard: uuid.UUID, recipient: _Tenant) -> uuid.UUID:
    """The API's own insert, on the owner login as the API runs it on the service role."""
    from api import shares

    with svc.cursor() as cur:
        cur.execute(shares._INSERT_DASHBOARD_SHARE_SQL, (dashboard, recipient.acc))
        row = cur.fetchone()
    return row[0]


def _rows(t: _Tenant, sql: str, params: tuple[Any, ...] = (), **scope: Any) -> list[tuple[Any, ...]]:
    with scoped(t.user, **scope) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _listed(t: _Tenant, **scope: Any) -> dict[str, dict[str, Any]]:
    from api import saved_sets
    from api.routes.stats_dashboards import DASHBOARDS

    with scoped(t.user, **scope) as conn:
        return {r["id"]: r for r in saved_sets.list_rows(conn, DASHBOARDS)}


def test_the_share_is_stamped_to_the_owner_by_the_trigger(
    svc: Any, tenants: dict[str, _Tenant], world: _World,
) -> None:
    with svc.cursor() as cur:
        cur.execute("SELECT account_id FROM stats_dashboard_shares WHERE dashboard_id = %s", (world.named,))
        assert cur.fetchall() == [(tenants["a"].acc,)]


def test_the_recipient_reads_the_dashboard_with_its_owners_email(
    tenants: dict[str, _Tenant], world: _World,
) -> None:
    a, b = tenants["a"], tenants["b"]
    seen_by_b = _listed(b)[str(world.named)]
    assert seen_by_b["name"] == "Praha byty" and seen_by_b["charts"] == [{"id": "c1"}]
    assert seen_by_b["filter_spec"] == {"filters": {"categoryMain": "byt"}}
    assert seen_by_b["owner_email"] == a.email
    assert seen_by_b["shared_with"] == [{"account_id": str(b.acc), "email": None}]
    seen_by_a = _listed(a)[str(world.named)]
    assert seen_by_a["owner_email"] is None
    assert seen_by_a["shared_with"] == [{"account_id": str(b.acc), "email": b.email}]


def test_a_bystander_reads_nothing_of_the_shared_dashboard(
    tenants: dict[str, _Tenant], world: _World,
) -> None:
    c = tenants["c"]
    assert _listed(c) == {}
    assert _rows(c, "SELECT id FROM stats_dashboards WHERE id = ANY(%s)", ([world.named, world.working],)) == []
    assert _rows(c, "SELECT 1 FROM stats_dashboard_shares WHERE dashboard_id = %s", (world.named,)) == []
    assert _rows(c, "SELECT account_id FROM share_party_emails()") == []


def test_the_recipient_cannot_update_reorder_or_delete_the_dashboard(
    svc: Any, tenants: dict[str, _Tenant], world: _World,
) -> None:
    from api import saved_sets
    from api.routes.stats_dashboards import DASHBOARDS

    b = tenants["b"]
    with scoped(b.user, method="PUT") as conn:
        assert saved_sets.update(conn, DASHBOARDS, str(world.named), {"name": "taken"}) is None
        saved_sets.reorder(conn, DASHBOARDS, [str(world.named)])
        assert saved_sets.delete(conn, DASHBOARDS, str(world.named)) is False
    with svc.cursor() as cur:
        cur.execute("SELECT name, position FROM stats_dashboards WHERE id = %s", (world.named,))
        assert cur.fetchone() == ("Praha byty", 0)


def test_the_working_board_is_never_the_recipients_and_never_a_saved_dashboard(
    tenants: dict[str, _Tenant], world: _World,
) -> None:
    from api.routes.stats_dashboards import working_board

    a, b = tenants["a"], tenants["b"]
    assert _rows(b, "SELECT id FROM stats_dashboards WHERE id = %s", (world.working,)) == []
    assert str(world.working) not in _listed(a), "the working board is listed as a saved dashboard"
    with scoped(b.user) as conn:
        assert working_board(conn, account_id=b.acc) is None
    with scoped(a.user) as conn:
        assert working_board(conn, account_id=a.acc)["charts"] == [{"id": "w1"}]


def test_the_working_board_cannot_be_shared(
    svc: Any, tenants: dict[str, _Tenant], world: _World,
) -> None:
    import psycopg

    with pytest.raises(psycopg.errors.RaiseException, match="the working board is never shared"):
        _share(svc, world.working, tenants["b"])


def test_a_second_save_replaces_the_working_board_in_place(
    svc: Any, tenants: dict[str, _Tenant], world: _World,
) -> None:
    from api.routes.stats_dashboards import save_working_board

    a = tenants["a"]
    with scoped(a.user, method="PUT") as conn:
        save_working_board(conn, [{"id": "w2"}], account_id=a.acc)
    with svc.cursor() as cur:
        cur.execute("SELECT id, charts FROM stats_dashboards WHERE account_id = %s AND name IS NULL", (a.acc,))
        assert cur.fetchall() == [(world.working, [{"id": "w2"}])]


def test_no_browser_role_can_create_a_share(tenants: dict[str, _Tenant], world: _World) -> None:
    import psycopg

    for who, to in (("a", "c"), ("c", "a")):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _rows(tenants[who], "INSERT INTO stats_dashboard_shares (dashboard_id, recipient_account_id) "
                                "VALUES (%s, %s)", (world.named, tenants[to].acc), method="POST")


def test_the_share_rows_hide_while_acting(tenants: dict[str, _Tenant], world: _World) -> None:
    b = tenants["b"]
    assert _listed(b, act_as=uuid.uuid4()) == {}
    assert _rows(b, "SELECT 1 FROM stats_dashboard_shares", act_as=uuid.uuid4()) == []


def test_a_bystander_cannot_end_the_share_and_the_recipient_can_leave(
    tenants: dict[str, _Tenant], world: _World,
) -> None:
    from fastapi import HTTPException

    from api import shares

    b, c = tenants["b"], tenants["c"]
    with scoped(c.user, method="DELETE") as conn, pytest.raises(HTTPException) as exc:
        shares.revoke_dashboard(conn, str(world.named), str(b.acc))
    assert exc.value.status_code == 404
    with scoped(b.user, method="DELETE") as conn:
        assert shares.revoke_dashboard(conn, str(world.named), str(b.acc)) == {"deleted": True}
    assert str(world.named) not in _listed(b)


def test_the_owner_can_revoke_and_deleting_the_dashboard_ends_its_shares(
    svc: Any, tenants: dict[str, _Tenant], world: _World,
) -> None:
    from api import saved_sets, shares
    from api.routes.stats_dashboards import DASHBOARDS

    a, b, c = tenants["a"], tenants["b"], tenants["c"]
    with scoped(a.user, method="DELETE") as conn:
        assert shares.revoke_dashboard(conn, str(world.named), str(b.acc)) == {"deleted": True}
    assert _listed(b) == {}
    _share(svc, world.named, c)
    assert str(world.named) in _listed(c)
    with scoped(a.user, method="DELETE") as conn:
        assert saved_sets.delete(conn, DASHBOARDS, str(world.named)) is True
    with svc.cursor() as cur:
        cur.execute("SELECT count(*) FROM stats_dashboard_shares WHERE dashboard_id = %s", (world.named,))
        assert cur.fetchone()[0] == 0
