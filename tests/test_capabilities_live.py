"""The tool registry against the replayed schema (#2062): a tenant tool runs in the caller's own RLS transaction
and sees only their account's rows; a market tool places a property we hold at its location ad and returns the
toolkit's own envelope. Runs in CI's migrations job (`TEST_DATABASE_URL`)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from api.capabilities import registry, subject
from api.capabilities.entry import Caller, Context, Effect, Entry, Tenancy
from api.capabilities.errors import ToolError, ToolErrorCode
from api.capabilities.tenant import NoArgs
from tests._live_property import REQUIRED_DB, _advert, _property, _recompute, db_url
from tests.test_location_ad_readers_live import POINT, RING, _placed
from toolkit import ComparableFilters, TargetSpec, find_comparables_relaxed

pytestmark = REQUIRED_DB


# --- tenant: the caller's own rows, in their own transaction -----------------------------------------------------

@pytest.fixture(scope="module")
def svc() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(db_url(), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def users(svc: psycopg.Connection) -> Iterator[dict[str, uuid.UUID]]:
    """Two signups through the real trigger (migration 612), each born with its default board."""
    a, b = uuid.uuid4(), uuid.uuid4()
    with svc.cursor() as cur:
        cur.execute("INSERT INTO auth.users (id, email) VALUES (%s, %s), (%s, %s)",
                    (a, f"tools-a-{a.hex[:8]}@test.local", b, f"tools-b-{b.hex[:8]}@test.local"))
        cur.execute("SELECT user_id, account_id FROM account_members WHERE user_id IN (%s, %s)", (a, b))
        account = dict(cur.fetchall())
    try:
        yield {"a": a, "b": b, "a_acc": account[a], "b_acc": account[b]}
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id IN (%s, %s)", (account[a], account[b]))
            cur.execute("DELETE FROM auth.users WHERE id IN (%s, %s)", (a, b))


@pytest.fixture()
def tenant_pool_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_POOL_DB_URL", db_url())


def _claims(user: uuid.UUID) -> dict[str, str]:
    return {"sub": str(user), "role": "authenticated"}


def _stage_ids(svc: psycopg.Connection, account: uuid.UUID) -> set[int]:
    with svc.cursor() as cur:
        cur.execute("SELECT id FROM pipeline_stages WHERE account_id = %s AND archived_at IS NULL", (account,))
        return {int(r[0]) for r in cur.fetchall()}


@pytest.mark.usefixtures("tenant_pool_db")
def test_a_tenant_tool_returns_exactly_the_callers_own_rows(svc: psycopg.Connection, users: dict[str, Any]) -> None:
    for me, mine, theirs in (("a", "a_acc", "b_acc"), ("b", "b_acc", "a_acc")):
        out = registry.call(Caller(market=svc, claims=_claims(users[me])), "list_pipeline_stages", {})
        seen = {stage["id"] for stage in out["data"]}
        assert seen and seen == _stage_ids(svc, users[mine])
        assert not seen & _stage_ids(svc, users[theirs])


@pytest.mark.usefixtures("tenant_pool_db")
def test_a_tenant_tool_runs_as_authenticated_under_the_callers_claims(
        svc: psycopg.Connection, users: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def whoami(ctx: Context, args: NoArgs) -> dict[str, Any]:
        with ctx.conn.cursor() as cur:
            cur.execute("SELECT current_user, current_setting('request.jwt.claims', true)::jsonb ->> 'sub', "
                        "current_setting('request.headers', true)")
            return {"data": list(cur.fetchone())}
    probe = Entry(name="whoami", description="probe", args=NoArgs, handler=whoami,
                  tenancy=Tenancy.TENANT, effect=Effect.READ)
    monkeypatch.setattr(registry, "ENTRIES", {"whoami": probe})
    role, sub, headers = registry.call(Caller(market=svc, claims=_claims(users["a"])), "whoami", {})["data"]
    assert (role, sub) == ("authenticated", str(users["a"]))
    assert not headers   # never acting as anyone


def test_a_tenant_tool_without_a_signed_in_caller_is_forbidden(svc: psycopg.Connection) -> None:
    with pytest.raises(ToolError) as info:
        registry.call(Caller(market=svc), "list_pipeline_stages", {})
    assert info.value.code is ToolErrorCode.FORBIDDEN


# --- market: a property we hold, placed at its location ad -------------------------------------------------------

def _held_property(cur: Any) -> tuple[int, int]:
    """A property whose active canonical ad sits on a street ring and whose inactive ad holds the address point."""
    pid = _property(cur)
    ring = _advert(cur, pid, source="idnes", price=8_000_000)
    point = _advert(cur, pid, source="idnes", price=7_000_000, active=False)
    _placed(cur, ring, RING, "street", 300)
    _placed(cur, point, POINT, "address_point", 10, cp="850", co="43")
    _recompute(cur, pid)
    return pid, ring


def _neighbour(cur: Any) -> int:
    pid = _property(cur)
    ad = _advert(cur, pid, source="sreality", price=6_500_000)
    _placed(cur, ad, (POINT[0] + 0.001, POINT[1]), "address_point", 10, cp="851", co="45")
    _recompute(cur, pid)
    return ad


def test_a_property_stands_for_its_canonical_ad_at_its_location_ad(cur: Any) -> None:
    pid, ring = _held_property(cur)
    args = registry.validate("find_comparables_relaxed", {"property_id": pid})[1]
    target, values = subject.place(cur.connection, args)
    assert subject.held_ad(cur.connection, args) == ring
    assert (target.lng, target.lat) == pytest.approx(POINT) and target.exclude_listing_ids == [ring]
    assert (values["category_main"], values["category_type"]) == ("byt", "prodej")


def test_a_merged_away_property_follows_its_survivor_and_an_unknown_one_is_not_found(cur: Any) -> None:
    pid, ring = _held_property(cur)
    gone = _property(cur)
    cur.execute("UPDATE properties SET status = 'merged_away', merged_into = %s WHERE id = %s", (pid, gone))
    args = registry.validate("compute_reference_rent", {"property_id": gone})[1]
    assert subject.held_ad(cur.connection, args) == ring
    with pytest.raises(ToolError) as info:
        registry.call(Caller(market=cur.connection), "compute_reference_rent", {"property_id": 2**62})
    assert info.value.code is ToolErrorCode.NOT_FOUND


def test_a_market_tool_returns_the_toolkits_envelope_without_the_subjects_own_property(
        cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import toolkit
    monkeypatch.setattr(toolkit, "_now_iso", lambda: "2026-10-28T00:00:00+00:00")
    pid, ring = _held_property(cur)
    neighbour = _neighbour(cur)
    out = registry.call(Caller(market=cur.connection), "find_comparables_relaxed",
                        {"property_id": pid, "radius_m": 1000, "min_results": 1})
    direct = find_comparables_relaxed(
        cur.connection, TargetSpec(lat=POINT[1], lng=POINT[0], area_m2=70.0, exclude_listing_ids=[ring]),
        ComparableFilters(category_main="byt", category_type="prodej"), min_results=1)
    assert out == direct
    ids = {row["listing_id"] for row in out["data"]["listings"]}
    assert neighbour in ids and ring not in ids
