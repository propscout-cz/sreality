"""The tool registry against the replayed schema (#2062): a tenant tool runs in the caller's own RLS transaction
and sees only their account's rows; every tool read is READ ONLY at `authenticated`'s 8 s (#2063); a market tool
places a property we hold at its location ad and returns the toolkit's own envelope; search_properties selects, under
the user's login, exactly the properties the Watchdog matches for the same filters (#2063); the dossier is the property
page's read under the user's login and the brokers the page's, at most twenty people a call, each call recorded
(#2063). Runs in CI's migrations job (`TEST_DATABASE_URL`)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from api import tenant_pool
from api.capabilities import registry, subject
from api.capabilities.entry import Caller, Context, Effect, Entry, Tenancy
from api.capabilities.errors import ToolError, ToolErrorCode
from api.filter_args import Args
from api.notifications import WatchdogFilterSpec, _build_match_clauses
from api.property_search import PAGE_MAX
from tests._live_property import REQUIRED_DB, _advert, _property, _recompute, _snapshot, _tag, db_url
from tests._tenant_scope import tool_call
from tests.test_location_ad_readers_live import POINT, RING, _placed
from toolkit import ComparableFilters, TargetSpec, find_comparables_relaxed
from toolkit.browse_read_model import sync_browse_list

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


class _Bare(Args):
    pass  # a probe takes nothing (a docstring would reach the schema)


def _probe(monkeypatch: pytest.MonkeyPatch, tenancy: Tenancy, handler: Callable[[Context, _Bare], Any]) -> None:
    monkeypatch.setattr(registry, "ENTRIES", {"probe": Entry(name="probe", description="probe", args=_Bare,
                                                             handler=handler, tenancy=tenancy, effect=Effect.READ)})


def _call_as_a(svc: psycopg.Connection, users: dict[str, Any]) -> dict[str, Any]:
    return registry.call(Caller(market=svc, claims=_claims(users["a"])), "probe", {})


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
    def whoami(ctx: Context, args: _Bare) -> dict[str, Any]:
        with ctx.conn.cursor() as cur:
            cur.execute("SELECT current_user, current_setting('request.jwt.claims', true)::jsonb ->> 'sub', "
                        "current_setting('request.headers', true)")
            return {"data": list(cur.fetchone())}
    _probe(monkeypatch, Tenancy.TENANT, whoami)
    role, sub, headers = _call_as_a(svc, users)["data"]
    assert (role, sub) == ("authenticated", str(users["a"]))
    assert not headers   # never acting as anyone


def test_a_tenant_tool_without_a_signed_in_caller_is_forbidden(svc: psycopg.Connection) -> None:
    with pytest.raises(ToolError) as info:
        registry.call(Caller(market=svc), "list_pipeline_stages", {})
    assert info.value.code is ToolErrorCode.FORBIDDEN


# --- every tool read is bounded the way PostgREST bounds a signed-in GET -----------------------------------------

def _session(conn: psycopg.Connection) -> tuple[str, bool]:
    """(transaction_read_only, whether statement_timeout is still the session's own) on an autocommit connection."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_setting('transaction_read_only'), "
                    "(SELECT setting = reset_val FROM pg_settings WHERE name = 'statement_timeout')")
        return cur.fetchone()


TENANCIES = pytest.mark.parametrize("tenancy", [Tenancy.MARKET, Tenancy.TENANT])


@TENANCIES
@pytest.mark.usefixtures("tenant_pool_db")
def test_a_tool_reads_read_only_at_authenticateds_8s_and_leaves_the_service_connection_as_it_was(
        svc: psycopg.Connection, users: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tenancy: Tenancy) -> None:
    def bound(ctx: Context, args: _Bare) -> dict[str, Any]:
        with ctx.conn.cursor() as cur:
            cur.execute("SELECT current_setting('transaction_read_only'), current_setting('statement_timeout')")
            return {"data": list(cur.fetchone())}
    _probe(monkeypatch, tenancy, bound)
    assert _session(svc) == ("off", True)
    assert _call_as_a(svc, users)["data"] == ["on", "8s"]
    assert _session(svc) == ("off", True)


@TENANCIES
@pytest.mark.parametrize("swallowed", [False, True])
@pytest.mark.usefixtures("tenant_pool_db")
def test_a_read_past_its_time_limit_is_a_retryable_busy_even_when_the_handler_swallows_the_cancel(
        svc: psycopg.Connection, users: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tenancy: Tenancy,
        swallowed: bool) -> None:
    monkeypatch.setattr(tenant_pool, "READ_BOUND", ("SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '50ms'"))

    def sleeper(ctx: Context, args: _Bare) -> dict[str, Any]:
        try:
            with ctx.conn.cursor() as cur:
                cur.execute("SELECT pg_sleep(2)")
        except psycopg.errors.QueryCanceled:
            if not swallowed:   # rent_map's reference swallows it and would answer "no reference"
                raise
        return {"data": []}
    _probe(monkeypatch, tenancy, sleeper)
    started = time.monotonic()
    with pytest.raises(ToolError) as info:
        _call_as_a(svc, users)
    assert (info.value.code, info.value.retryable) == (ToolErrorCode.BUSY, True)
    assert time.monotonic() - started < 1.5
    assert _session(svc) == ("off", True)


@TENANCIES
@pytest.mark.usefixtures("tenant_pool_db")
def test_a_read_tool_that_writes_fails_and_writes_nothing(
        svc: psycopg.Connection, users: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tenancy: Tenancy) -> None:
    """RLS would let A add a tag of A's own, and the service role anything: READ ONLY refuses both."""
    name = f"tools-ro-{uuid.uuid4().hex[:12]}"

    def writer(ctx: Context, args: _Bare) -> dict[str, Any]:
        with ctx.conn.cursor() as cur:
            cur.execute("INSERT INTO tags (account_id, name, color) VALUES (%s, %s, 'slate')", (users["a_acc"], name))
        return {"data": []}
    _probe(monkeypatch, tenancy, writer)
    with pytest.raises(ToolError) as info:
        _call_as_a(svc, users)
    assert info.value.code is ToolErrorCode.INTERNAL   # a READ tool that writes is a defect, not the caller's
    with svc.cursor() as cur:
        cur.execute("SELECT count(*) FROM tags WHERE name = %s", (name,))
        assert cur.fetchone()[0] == 0


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


# --- search: Browse's list under the user's login, the Watchdog's matches for the same spec ----------------------

_TOWN_A, _TOWN_B = 990_001, 990_002
_BOX = {"west": 12.30, "south": 48.90, "east": 12.40, "north": 49.00}
# name: the property's ads (the first canonical), where it stands, and how many days ago it was first seen. Every ad
# is a byt for sale of 70 m² unless it says otherwise; `unplaced` has no location, `unsettled` is not yet published.
_SEEDS: dict[str, dict[str, Any]] = {
    "p1": {"town": _TOWN_A, "days": 2, "ads": [dict(source="idnes", price=4_000_000, disposition="2+kk", area_m2=55,
           has_balcony=True, furnished="ano", building_type="cihla", energy_rating="B", ownership="osobni")]},
    "p2": {"town": _TOWN_A, "days": 20, "ads": [dict(source="sreality", price=7_000_000, disposition="3+kk",
           area_m2=80, has_balcony=False, furnished="ne", building_type="panel", energy_rating="C")]},
    "p3": {"town": _TOWN_B, "days": 40, "ads": [dict(source="bazos", price=None, active=False, disposition="2+kk",
           area_m2=60, building_type="skelet")]},
    "p4": {"town": _TOWN_B, "days": 25, "ads": [
        dict(source="idnes", price=2_500_000, disposition="1+kk", area_m2=30, furnished="castecne"),
        dict(source="sreality", price=2_600_000, active=False, disposition="1+kk", area_m2=30)]},
    "p5": {"town": _TOWN_A, "days": 60, "ads": [dict(source="remax", price=9_000_000, category_main="dum",
           subtype="rodinny_dum", area_m2=150, estate_area=600)]},
    "p6": {"town": _TOWN_B, "days": 3, "ads": [dict(source="idnes", price=20_000, category_type="pronajem",
           disposition="2+kk", area_m2=50)]},
    "p7": {"town": None, "days": 2, "ads": [dict(source="sreality", price=4_000_000, disposition="2+kk")]},
    "p8": {"town": _TOWN_A, "days": 0, "unsettled": True, "ads": [dict(source="idnes", disposition="2+kk")]},
    "p9": {"town": _TOWN_A, "days": 5, "ads": [dict(source="mmreality", price=12_000_000, disposition="4+kk",
           area_m2=100, has_lift=True)]},
    "p10": {"town": _TOWN_B, "days": 8, "ads": [dict(source="sreality", price=3_000_000, disposition="2+kk",
            area_m2=45, ownership="druzstevni")]},
}
_SERVED = {n for n, seed in _SEEDS.items() if seed["town"] and not seed.get("unsettled")}


def _seed_search(cur: Any) -> dict[str, int]:
    """The seeds, published into Browse's read model; p2's price fell from 8.0 to 7.0 M Kč an hour ago."""
    ids: dict[str, int] = {}
    now = datetime.now(timezone.utc)
    for i, (name, seed) in enumerate(_SEEDS.items()):
        if seed.get("unsettled"):
            cur.execute("INSERT INTO properties DEFAULT VALUES RETURNING id")
            pid = int(cur.fetchone()[0])
        else:
            pid = _property(cur)
        ads = []
        for facts in seed["ads"]:
            active = facts.get("active", True)
            ad = _advert(cur, pid, **facts, first_seen_at=now - timedelta(days=seed["days"]),
                         last_seen_at=now if active else now - timedelta(days=seed["days"] - 1))
            ads.append(ad)
            if seed["town"]:
                _placed(cur, ad, (12.31 + i * 0.008, 48.95), "address_point", 10, town_code=seed["town"])
        if name == "p2":
            _snapshot(cur, ads[0], 8_000_000, 48)
            _snapshot(cur, ads[0], 7_000_000, 1)
        _recompute(cur, pid)
        ids[name] = pid
    sync_browse_list(cur.connection, ids.values())
    return ids


@pytest.fixture()
def seeded(cur: Any) -> dict[str, int]:
    return _seed_search(cur)


def _searched(cur: Any, user: uuid.UUID, args: dict[str, Any], ids: dict[str, int]) -> set[str]:
    """Every page of search_properties as `user`, within the seeds' box, by seed name."""
    names = {pid: n for n, pid in ids.items()}
    found: set[str] = set()
    offset = 0
    while True:
        out = tool_call(cur, user, "search_properties", {
            "bounds": _BOX, "category_type": "prodej", **args, "limit": PAGE_MAX, "offset": offset})
        found |= {names[r["property_id"]] for r in out["data"] if r["property_id"] in names}
        offset += len(out["data"])
        if not out["data"] or offset >= out["metadata"]["result_count"]:
            return found


def _watched(cur: Any, spec: dict[str, Any], ids: dict[str, int]) -> set[str]:
    """What the Watchdog matches over properties_public for the same filters, among the seeds, by seed name."""
    where, params = _build_match_clauses(WatchdogFilterSpec(**{"category_type": "prodej", **spec}))
    cur.execute(f"SELECT l.property_id FROM properties_public l WHERE {' AND '.join(where)} "
                "AND l.property_id = ANY(%(seeds)s)", {**params, "seeds": list(ids.values())})
    names = {pid: n for n, pid in ids.items()}
    return {names[r[0]] for r in cur.fetchall()}


def _city_rule(cur: Any) -> list[dict[str, Any]]:
    """Town A as a curated city whose made-up index is 8: the rule `>= 6` selects it, and only it."""
    index = f"lp_{uuid.uuid4().hex[:8]}"
    cur.execute("INSERT INTO admin_boundaries (id, level, name, geom) VALUES (%s, 'obec', 'Lp A', "
                "ST_Multi(ST_MakeEnvelope(12.2, 48.8, 12.5, 49.1, 4326))::geography)", (_TOWN_A,))
    cur.execute("INSERT INTO curated_cities (name, kraj_name, centroid, admin_boundary_id) VALUES "
                "(%s, 'Lp', ST_SetSRID(ST_MakePoint(12.35, 48.95), 4326)::geography, %s) RETURNING id",
                (index, _TOWN_A))
    city = cur.fetchone()[0]
    cur.execute("INSERT INTO city_index_revisions (source_filename, row_count, raw_rows) VALUES ('lp.csv', 1, "
                "'[]'::jsonb) RETURNING source_revision")
    cur.execute("INSERT INTO city_index_values (city_id, source_revision, index_name, value) VALUES (%s, %s, %s, 8)",
                (city, cur.fetchone()[0], index))
    return [{"index_name": index, "op": ">=", "value": 6}]


# One per sql_kind, each shared hook and the portal rule's sides, the map box and the place chips.
_PARITY: list[tuple[str, dict[str, Any]]] = [
    ("eq", {"has_balcony": True}),
    ("eq-deal", {"category_type": None}),
    ("any-one", {"dispositions": ["2+kk"]}),
    ("any", {"dispositions": ["2+kk", "3+kk"]}),
    ("any-category", {"category_main_in": ["dum"]}),
    ("gte", {"min_area_m2": 70}),
    ("lte", {"max_area_m2": 50}),
    ("gte-measure", {"min_price_per_m2": 80_000}),
    ("lte-plot", {"max_estate_area": 1000}),
    ("enum-one", {"furnished": ["ano"]}),
    ("enum-unknown", {"furnished": ["ne", "__unknown__"]}),
    ("enum-many", {"ownership": ["druzstevni", "osobni"]}),
    ("price", {"min_price_czk": 5_000_000}),
    ("price-or-none", {"max_price_czk": 3_000_000, "include_no_price": True}),
    ("material", {"building_material": ["panel"]}),
    ("energy", {"energy_rating_match": ["B"]}),
    ("price-changes", {"price_change_count_min": 1, "price_change_window_days": 30}),
    ("price-fell", {"total_price_change_pct": -5}),
    ("portal", {"portals": ["sreality"]}),
    ("portal-excluded", {"portals_excluded": ["idnes"]}),
    ("portal-excluded-live", {"portals_excluded": ["sreality"], "portals_excluded_active_only": True}),
    ("box", {"bounds": {"west": 12.30, "south": 48.90, "east": 12.325, "north": 49.00}}),
    ("place", {"districts": [{"name": "Lp B", "level": "obec", "id": _TOWN_B}]}),
    ("place-excluded", {"districts": [{"name": "Lp A", "level": "obec", "id": _TOWN_A, "excluded": True}]}),
    ("city-rule", {"city_index_rules": None}),
]


@pytest.mark.parametrize("spec", [s for _, s in _PARITY], ids=[n for n, _ in _PARITY])
def test_search_selects_what_the_watchdog_matches_for_the_same_filters(
        cur: Any, seeded: dict[str, int], users: dict[str, Any], spec: dict[str, Any]) -> None:
    if "city_index_rules" in spec:
        spec = {"city_index_rules": _city_rule(cur)}
    watched = _watched(cur, spec, seeded)
    # It discriminates: the filter changes the Watchdog's own matches (the deal type widens them).
    assert watched and watched != _watched(cur, {}, seeded), (spec, watched)
    assert _searched(cur, users["a"], {"show_dismissed": True, **spec}, seeded) == watched


@pytest.mark.parametrize("args, expected", [
    ({}, _SERVED - {"p6"}),
    ({"status": "active"}, _SERVED - {"p3", "p6"}),
    ({"status": "inactive"}, {"p3"}),
    # Under `active` a portal asks for an ACTIVE ad there: p4's sreality ad ended.
    ({"status": "active", "portals": ["sreality"]}, {"p2", "p10"}),
    ({"status": "inactive", "portals": ["bazos"]}, {"p3"}),
    ({"recently_added_days": 3}, {"p1"}),
    ({"recently_changed_days": 1}, {"p2"}),
    ({"tom_days_min": 15, "tom_days_max": 30}, {"p2", "p4"}),
], ids=["any", "active", "inactive", "active-portal", "inactive-portal", "added", "changed", "tom"])
def test_status_recency_and_time_on_market_select_browses_rows(
        cur: Any, seeded: dict[str, int], users: dict[str, Any], args: dict[str, Any], expected: set[str]) -> None:
    assert _searched(cur, users["a"], args, seeded) == expected


def test_observed_tom_reads_the_published_flag(cur: Any, seeded: dict[str, int], users: dict[str, Any]) -> None:
    cur.execute("UPDATE browse_list SET tom_observed = (property_id <> %s) WHERE property_id = ANY(%s)",
                (seeded["p1"], list(seeded.values())))
    assert _searched(cur, users["a"], {"tom_observed_only": True}, seeded) == _SERVED - {"p1", "p6"}


def test_the_users_own_lenses_are_judged_under_their_login(
        cur: Any, seeded: dict[str, int], users: dict[str, Any]) -> None:
    """A's dismissal hides p9 from A only; A's tag and A's estimate select for A, B's for B."""
    cur.execute("INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)",
                (users["a_acc"], seeded["p9"]))
    tags = {who: _tag(cur, users[f"{who}_acc"]) for who in ("a", "b")}
    cur.execute("INSERT INTO property_tags (account_id, property_id, tag_id) VALUES (%s, %s, %s), (%s, %s, %s)",
                (users["a_acc"], seeded["p1"], tags["a"], users["b_acc"], seeded["p2"], tags["b"]))
    for who, name in (("a", "p1"), ("b", "p2")):
        cur.execute("SELECT id FROM listings WHERE property_id = %s", (seeded[name],))
        cur.execute("INSERT INTO estimation_runs (account_id, source, mode, status, input_spec, input_listing_id) "
                    "VALUES (%s, 'api', 'deterministic', 'success', '{}'::jsonb, %s)",
                    (users[f"{who}_acc"], cur.fetchone()[0]))
    everyone = _SERVED - {"p6"}
    assert _searched(cur, users["a"], {}, seeded) == everyone - {"p9"}
    assert _searched(cur, users["a"], {"show_dismissed": True}, seeded) == everyone
    assert _searched(cur, users["b"], {}, seeded) == everyone
    assert _searched(cur, users["a"], {"tags": {"in": [tags["a"]]}}, seeded) == {"p1"}
    assert _searched(cur, users["a"], {"tags": {"set": "out"}}, seeded) == everyone - {"p1", "p9"}
    # Browse's normal form (setScope.normalizeScope): the 'out' side ignores the lists, an id on both is out.
    assert _searched(cur, users["a"], {"tags": {"set": "out", "in": [tags["a"]]}}, seeded) == everyone - {"p1", "p9"}
    assert _searched(cur, users["a"], {"tags": {"in": [tags["a"]], "out": [tags["a"]]}}, seeded) == everyone - {
        "p1", "p9"}
    assert _searched(cur, users["b"], {"tags": {"in": [tags["a"]]}}, seeded) == set()
    assert _searched(cur, users["a"], {"with_estimates": True}, seeded) == {"p1"}
    assert _searched(cur, users["b"], {"with_estimates": True}, seeded) == {"p2"}


def test_one_portal_orders_by_its_own_newest_ad_so_a_property_advertised_there_again_rises(
        cur: Any, seeded: dict[str, int], users: dict[str, Any]) -> None:
    """Q49 b (queries.ts effectiveSort): p2, first seen 20 days ago on sreality, gets an idnes ad today. With idnes
    alone it leads newest first though p1 was first seen later; with no portal its own first sighting still orders."""
    now, i = datetime.now(timezone.utc), list(_SEEDS).index("p2")
    ad = _advert(cur, seeded["p2"], source="idnes", price=7_000_000, disposition="3+kk", area_m2=80,
                 first_seen_at=now, last_seen_at=now)
    _placed(cur, ad, (12.31 + i * 0.008, 48.95), "address_point", 10, town_code=_TOWN_A)
    _recompute(cur, seeded["p2"])
    sync_browse_list(cur.connection, [seeded["p2"]])
    names = {pid: n for n, pid in seeded.items()}

    def order(args: dict[str, Any]) -> list[str]:
        out = tool_call(cur, users["a"], "search_properties", {"bounds": _BOX, "category_type": "prodej", **args})
        return [names[r["property_id"]] for r in out["data"] if r["property_id"] in names]
    assert order({"portals": ["idnes"]}) == ["p2", "p1", "p4"]
    assert order({"portals": ["idnes"], "sort": "first_seen_at"}) == ["p4", "p1", "p2"]
    assert order({}) == ["p1", "p9", "p10", "p2", "p4", "p3", "p5"]


def test_a_page_is_browses_cards_in_its_order_with_the_total(
        cur: Any, seeded: dict[str, int], users: dict[str, Any]) -> None:
    out = tool_call(cur, users["a"], "search_properties",
                    {"bounds": _BOX, "category_type": "prodej", "sort": "-price_czk", "limit": 3})
    prices = [r["price_czk"] for r in out["data"]]
    assert prices == sorted(prices, reverse=True) and prices[0] == 12_000_000
    assert out["metadata"]["result_count"] == len(_SERVED - {"p6"})
    row = out["data"][0]
    assert (row["property_id"], row["price_per_m2_basis"]) == (seeded["p9"], "sale_capital_czk_m2")
    assert out["metadata"]["price_per_m2_basis"] == "sale_capital_czk_m2"
    assert "building_condition_level" not in row and "home_obec_pop" not in row


# --- the property page's reads: its dossier under the user's login, its brokers as it shows them -----------------

def _run(cur: Any, account: uuid.UUID, ad: int) -> int:
    cur.execute("INSERT INTO estimation_runs (account_id, source, mode, status, input_spec, input_listing_id) "
                "VALUES (%s, 'api', 'deterministic', 'success', '{}'::jsonb, %s) RETURNING id", (account, ad))
    return int(cur.fetchone()[0])


def test_the_dossier_is_the_property_pages_read_under_the_users_login(cur: Any, users: dict[str, Any]) -> None:
    """A merged-away id follows its survivor; the place is the location ad's; an ad's price points are where its price
    moved (a content change at the same price is no point); each user sees only their own runs on its ads."""
    from toolkit.measures import SALE_CAPITAL_CZK_M2
    pid, ring = _held_property(cur)
    cur.execute("SELECT id FROM listings WHERE property_id = %s AND id <> %s", (pid, ring))
    point = cur.fetchone()[0]
    for price, hours in ((8_000_000, 48), (8_000_000, 24), (7_500_000, 1)):
        _snapshot(cur, ring, price, hours)
    gone = _property(cur)
    cur.execute("UPDATE properties SET status = 'merged_away', merged_into = %s WHERE id = %s", (pid, gone))
    runs = {"a": _run(cur, users["a_acc"], ring), "b": _run(cur, users["b_acc"], point)}
    # Production's PostGIS lives in `extensions`, where `authenticated` may run it (the page reads lat/lng so); the
    # replay's lives in `public`, where migration 299 revoked PUBLIC's EXECUTE. The grant rolls back with the test.
    cur.execute("GRANT EXECUTE ON FUNCTION st_x(geometry), st_y(geometry) TO authenticated")
    out = tool_call(cur, users["a"], "get_property_dossier", {"property_ids": [gone, 2**62]})
    [row] = out["data"]
    assert (out["metadata"]["resolved"], out["metadata"]["missing"]) == ({gone: pid}, [2**62])
    assert (row["property_id"], row["listing_id"]) == (pid, ring)
    assert (row["lng"], row["lat"]) == pytest.approx(POINT) and row["display_label"]
    ads = {ad["listing_id"]: ad for ad in row["ads"]}
    assert (ads[ring]["canonical"], ads[point]["canonical"], ads[point]["is_active"]) == (True, False, False)
    assert (ads[ring]["category_type"], ads[ring]["price_per_m2_basis"]) == ("prodej", SALE_CAPITAL_CZK_M2)
    assert [p["price_czk"] for p in ads[ring]["prices"]] == [8_000_000, 7_500_000] and ads[point]["prices"] == []
    assert [r["id"] for r in row["estimates"]] == [runs["a"]]
    theirs = tool_call(cur, users["b"], "get_property_dossier", {"property_ids": [pid]})["data"][0]["estimates"]
    assert [r["id"] for r in theirs] == [runs["b"]]


def _broker(cur: Any, n: int) -> tuple[int, int]:
    """A broker person with a phone and an e-mail, and their one portal identity."""
    cur.execute("INSERT INTO brokers (display_name, primary_email, primary_phone) VALUES (%s, %s, %s) RETURNING id",
                (f"lp broker {n}", f"lp-{n}@rk.test", f"+420 700 000 {n:03d}"))
    broker = int(cur.fetchone()[0])
    cur.execute("INSERT INTO broker_identities (source, source_broker_id_native, broker_id) "
                "VALUES ('sreality', %s, %s) RETURNING id", (f"lp-{uuid.uuid4()}", broker))
    return broker, int(cur.fetchone()[0])


def test_the_brokers_are_the_pages_until_the_twenty_first_person_and_the_read_is_recorded(cur: Any) -> None:
    """Asked: an ended property (its ended ad's broker, flagged), one whose canonical ad is its second live ad (that
    ad's broker first, then the other live ads' person carrying both their ads; the ended ad's broker never), and one
    with twenty live ads by twenty people. The call stops at the twenty-first person."""
    now = datetime.now(timezone.utc)
    people = [_broker(cur, n) for n in range(24)]
    ended = _property(cur)
    gone = _advert(cur, ended, source="sreality", active=False, broker_identity_id=people[0][1])
    two = _property(cur)
    a1, a2, a3 = (_advert(cur, two, source="sreality", broker_identity_id=people[n][1],
                          first_seen_at=now - timedelta(days=d)) for n, d in ((1, 3), (2, 2), (1, 1)))
    _advert(cur, two, source="idnes", active=False, broker_identity_id=people[3][1])
    many = _property(cur)
    oldest = [_advert(cur, many, source="idnes", broker_identity_id=people[n][1],
                      first_seen_at=now - timedelta(hours=n)) for n in range(4, 24)][-1]
    for pid in (ended, two, many):
        _recompute(cur, pid)
    cur.execute("UPDATE properties SET repr_listing_ref_id = CASE id WHEN %s THEN %s ELSE %s END WHERE id IN (%s, %s)",
                (two, a2, oldest, two, many))

    records: list[tuple[Any, ...]] = []
    caller = Caller(market=cur.connection, record=lambda *r: records.append(r))
    with pytest.raises(ToolError) as info:
        registry.call(caller, "get_property_brokers", {"property_ids": list(range(1, 22))})
    assert info.value.code is ToolErrorCode.INVALID_ARGUMENTS
    out = registry.call(caller, "get_property_brokers", {"property_ids": [ended, two, many]})
    broker = {b: n for n, (b, _) in enumerate(people)}
    rows = [(r["property_id"], broker[r["broker_id"]], [a["listing_id"] for a in r["ads"]], r["from_inactive"])
            for r in out["data"]]
    # `many`'s ads oldest first, its canonical the oldest: person 23, then 22, and on.
    assert rows[:3] == [(ended, 0, [gone], True), (two, 2, [a2], False), (two, 1, [a1, a3], False)]
    assert [n for p, n, _, _ in rows[3:]] == list(range(23, 6, -1)) and {p for p, *_ in rows[3:]} == {many}
    meta = out["metadata"]
    assert (meta["brokers"], meta["unlisted_property_ids"], meta["result_count"], meta["pii_masked"]) == (
        {"kept": 20, "total": 23}, [many], 23, False)
    first = out["data"][0]
    assert (first["primary_email"], first["primary_phone"]) == ("lp-0@rk.test", "+420 700 000 000")
    assert [r[0] for r in records] == ["get_property_brokers"] * 2 and records[1] == (
        "get_property_brokers", {"property_ids": [ended, two, many]}, out)
