"""The broker board (api/broker_board.py) on the replayed schema: Browse's cohort over `browse_list`, counted by broker
through the book under the portal rule on both sides, each speed arm reaching its broker, and the benchmark untouched
by the output filters. Every seed rolls back with the test; the book and Browse's rows are written by the real
maintenance pass (`properties_changed`)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from api.broker_board import MIXED_CATEGORY, OUTCOME_SELECTED, BoardArgs, board
from scripts.recompute_property_stats import properties_changed
from tests._live_property import REQUIRED_DB, _advert, _property
from tests.test_location_ad_readers_live import _placed
from toolkit import brokers
from toolkit.measures import wilson_lower

pytestmark = REQUIRED_DB

_TOWN = 999_101  # the seeds' own obec: the board reads only them
_PLACE = [{"name": "Lp board", "level": "obec", "id": _TOWN}]
# Followed for every horizon up to 30 days from 2026-10-16 on (E0 + 1 day + N + M), whatever day CI runs.
_S = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
_D = timedelta(days=1)
_BENCHMARK = ("market", "unknown", "cohort_properties", "attributed_properties", "unattributed_share")
# Divides by zero only in a bounded read, READ ONLY at 8 s (tenant_pool.READ_BOUND). Not `SELECT 1 / 0 WHERE ...`:
# the planner folds a constant `1 / 0` and raises whatever the WHERE says.
_BOUND_PROBE = ("SELECT 1 / (current_setting('transaction_read_only') <> 'on' "
                "OR current_setting('statement_timeout') <> '8s')::int")


@pytest.fixture()
def seeded(cur: Any) -> dict[str, int]:
    """Broker A (with a firm) alone on six sreality properties (five exits, one still on the market); B on sreality (a
    zombie, a recent one) and co-listing x and y with C, B's idnes ad on y ended; C's exit on idnes and a rental; W's
    exit on web, the indexless portal; z on bazos, which publishes no broker."""
    now = datetime.now(timezone.utc)
    cur.execute("INSERT INTO firms (canonical_domain, display_name) VALUES (%s, 'A reality') RETURNING id",
                (f"lp-{uuid.uuid4().hex[:8]}.cz",))
    firm = cur.fetchone()[0]
    who = {}
    for name in ("A", "B", "C", "W"):
        cur.execute("INSERT INTO brokers (display_name, primary_email, primary_firm_id) VALUES (%s, %s, %s) "
                    "RETURNING id", (name, f"{name.lower()}@lp.test", firm if name == "A" else None))
        who[name] = cur.fetchone()[0]
    identities: dict[tuple[str, str], int] = {}

    def identity(name: str, source: str) -> int:
        if (name, source) not in identities:
            cur.execute("INSERT INTO broker_identities (source, source_broker_id_native, broker_id) VALUES (%s, %s, %s) "
                        "RETURNING id", (source, f"lp-{uuid.uuid4()}", who[name]))
            identities[name, source] = int(cur.fetchone()[0])
        return identities[name, source]

    exit_ = dict(first=_S, active=False, last=_S + 5 * _D)
    recent = dict(first=now - 3 * _D, active=True, last=now)
    seeds: dict[str, list[tuple[str | None, str, dict[str, Any]]]] = {
        **{f"a{i}": [("A", "sreality", exit_)] for i in range(1, 6)},
        "a6": [("A", "sreality", dict(first=_S, active=True, last=now))],
        "b1": [("B", "sreality", dict(first=_S, active=True, last=_S + 10 * _D))],
        "b2": [("B", "sreality", recent)],
        "x": [("B", "sreality", recent), ("C", "idnes", recent)],
        "y": [("B", "sreality", recent), ("B", "idnes", dict(first=now - 3 * _D, active=False, last=now - _D)),
              ("C", "idnes", recent)],
        "c1": [("C", "idnes", exit_)],
        "w1": [("W", "web", exit_)],
        "z": [(None, "bazos", recent)],
        "r": [("C", "idnes", {**recent, "category_type": "pronajem", "price": 20_000})],
    }
    pids = []
    for i, ads in enumerate(seeds.values()):
        pids.append(_property(cur))
        for broker, source, ad in ads:
            facts = dict(ad)
            lid = _advert(cur, pids[-1], source=source, active=facts.pop("active"), first_seen_at=facts.pop("first"),
                          last_seen_at=facts.pop("last"),
                          broker_identity_id=identity(broker, source) if broker else None, **facts)
            _placed(cur, lid, (15.01 + i * 0.001, 49.51), "address_point", 10, town_code=_TOWN)
    properties_changed(cur.connection, pids)
    cur.execute("SELECT bool_and(tom_observed) FROM broker_book WHERE property_id = ANY(%s)", (pids,))
    assert cur.fetchone()[0] is True, "every seeded spell must be observed for its exit to be followed"
    return {**who, "firm": firm}


def _board(cur: Any, **args: Any) -> dict[str, Any]:
    return board(cur.connection, BoardArgs.model_validate({"category_type": "prodej", "districts": _PLACE, **args}),
                 contacts=True)


def _by(out: dict[str, Any], seeded: dict[str, int]) -> dict[str, dict[str, Any]]:
    names = {seeded[n]: n for n in "ABCW"}
    return {names[r["broker_id"]]: r for r in out["data"]}


@pytest.mark.parametrize("args, counts, cohort", [
    ({}, {"A": 6, "B": 4, "C": 3, "W": 1}, (13, 12)),
    # Under active, a broker counts a property with an ad still running there (B's ended idnes ad is not its only one).
    ({"status": "active"}, {"A": 1, "B": 4, "C": 2}, (6, 5)),
    # MS19 on the broker's side too: broker + portal = one ad.
    ({"status": "active", "portals": ["idnes"]}, {"C": 2}, (2, 2)),
    ({"portals": ["idnes"]}, {"B": 1, "C": 3}, (3, 3)),
    # Browse's broker scope: every broker on that broker's properties.
    ({"broker_id": "C"}, {"B": 2, "C": 3}, (3, 3)),
    ({"broker_id": "C", "status": "active", "portals": ["idnes"]}, {"C": 2}, (2, 2)),
    # The output filters narrow the rows, never the benchmark.
    ({"firm_ids": "firm"}, {"A": 6}, None),
    ({"min_properties": 4}, {"A": 6, "B": 4}, None),
    ({"limit": 1}, {"A": 6}, None),
])
def test_volume_counts_each_brokers_properties_in_the_cohort(
        cur: Any, seeded: dict[str, int], args: dict[str, Any], counts: dict[str, int], cohort: Any) -> None:
    resolved = {k: ([seeded[v]] if k == "firm_ids" else seeded[v]) if k in ("broker_id", "firm_ids") else v
                for k, v in args.items()}
    out = _board(cur, **resolved)
    assert {n: r["properties"] for n, r in _by(out, seeded).items()} == counts
    if cohort:
        assert (out["metadata"]["cohort_properties"], out["metadata"]["attributed_properties"]) == cohort
    else:
        every = _board(cur)["metadata"]
        assert {k: out["metadata"][k] for k in _BENCHMARK} == {k: every[k] for k in _BENCHMARK}


def test_every_speed_arm_reaches_its_broker(cur: Any, seeded: dict[str, int]) -> None:
    """A: five exits and one still on the market (n 6, ranked); B: one zombie (unknown), the rest too recent to
    follow; C: one exit (n 1, under MIN_N); W: an exit on the indexless portal, which answers nothing."""
    out = _board(cur, horizon_days=14)
    rows = _by(out, seeded)
    assert {n: (r["n"], r["exited"], r["unknown"]) for n, r in rows.items()} == {
        "A": (6, 5, 0), "B": (0, 0, 1), "C": (1, 1, 0), "W": (0, 0, 0)}
    assert (rows["A"]["share"], rows["A"]["rank"]) == (5 / 6, wilson_lower(5, 6))
    assert all(rows[n]["share"] is None and rows[n]["rank"] is None for n in "BCW")
    meta = out["metadata"]
    assert (meta["market"], meta["unknown"], meta["speed_refused"]) == ({"n": 7, "exited": 6, "share": 6 / 7}, 1, None)
    assert (meta["unattributed_share"], meta["uncovered_portals"]) == (1 / 13, ["bazos"])
    assert (rows["A"]["display_name"], rows["A"]["firm_name"], rows["A"]["primary_email"], rows["C"]["firm_id"]) == (
        "A", "A reality", "a@lp.test", None)
    assert [r["broker_id"] for r in _board(cur, sort="speed")["data"]] == [seeded[n] for n in "ABCW"]


@pytest.mark.parametrize("args, refused, c_count", [
    ({"status": "inactive"}, OUTCOME_SELECTED, 1),
    ({"last_seen_max_days": 400}, OUTCOME_SELECTED, 3),
    ({"category_type": None}, MIXED_CATEGORY, 4),
])
def test_speed_is_refused_on_an_outcome_filter_or_a_mixed_cohort_and_volume_still_answers(
        cur: Any, seeded: dict[str, int], args: dict[str, Any], refused: str, c_count: int) -> None:
    out = _board(cur, **args)
    assert (out["metadata"]["speed_refused"], out["metadata"]["speed_refused_by"]) == (refused, [*args])
    assert out["metadata"]["market"]["share"] is None
    assert all(r["share"] is None and r["rank"] is None for r in out["data"])
    assert _by(out, seeded)["C"]["properties"] == c_count


def test_a_brokers_own_numbers_are_the_boards_over_every_category(cur: Any, seeded: dict[str, int]) -> None:
    """Ruling 4: the dossier, search and Outreach read the broker's book, which over every category is the unfiltered
    board's count, any status and active (every seed is served); by region (a property without one counts only in the
    totals) and by category; a firm counts the brokers its filter selects."""
    cur.execute("INSERT INTO admin_boundaries (id, level, name, geom) VALUES (999102, 'kraj', 'Lp kraj', "
                "ST_Multi(ST_MakeEnvelope(15, 49.5, 15.1, 49.6, 4326))::geography)")
    cur.execute("SELECT property_id FROM broker_book WHERE broker_id = %s", (seeded["C"],))
    c_pids = [r[0] for r in cur.fetchall()]
    cur.execute("UPDATE listing_location SET kraj_kod = 999102 WHERE listing_id IN "
                "(SELECT id FROM listings WHERE property_id = ANY(%s))", (c_pids,))
    properties_changed(cur.connection, c_pids)
    board_counts = {(n, s): r["properties"] for s in ("any", "active")
                    for n, r in _by(_board(cur, category_type=None, status=s), seeded).items()}
    for name in "AC":
        dossier = brokers.get_broker(cur.connection, seeded[name])["data"]
        assert (dossier["broker"]["properties"], dossier["broker"]["active_properties"]) == (
            board_counts[name, "any"], board_counts[name, "active"])
    c = brokers.get_broker(cur.connection, seeded["C"])["data"]
    assert c["region_shares"] == [{"geo_id": 999102, "name": "Lp kraj", "properties": 4, "active_properties": 3}]
    assert c["categories"] == [
        {"category_main": "byt", "category_type": "prodej", "properties": 3, "active_properties": 2},
        {"category_main": "byt", "category_type": "pronajem", "properties": 1, "active_properties": 1}]
    assert brokers.get_broker(cur.connection, seeded["A"])["data"]["region_shares"] == []
    cur.execute("SELECT canonical_domain FROM firms WHERE id = %s", (seeded["firm"],))
    firm = brokers.firm_options(cur.connection, q=cur.fetchone()[0])["data"]
    assert [(f["firm_id"], f["broker_count"]) for f in firm] == [(seeded["firm"], 1)]


def test_the_read_is_bounded_read_only_at_8s(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """bound_read in the board's own transaction: its statement runs READ ONLY at 8 s, and a write in its place fails
    and leaves nothing."""
    from api import broker_board

    monkeypatch.setattr(broker_board, "_statement", lambda cohort: _BOUND_PROBE)
    with pytest.raises(psycopg.errors.DivisionByZero):
        _board(cur)
    monkeypatch.setattr(broker_board, "_statement",
                        lambda cohort: "INSERT INTO brokers (display_name) VALUES ('lp-read-only') RETURNING id")
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        _board(cur)
    cur.execute("SELECT count(*) FROM brokers WHERE display_name = 'lp-read-only'")
    assert cur.fetchone() == (0,)


def test_a_brokers_own_numbers_are_bounded_reads_too(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """READ ONLY at 8 s, as the board: the routes' connection (tenant_pool.read_conn: search, the dossier, the firm
    chips, the merge review's list) and Outreach's draft facts."""
    from api import outreach, tenant_pool

    reads = tenant_pool.read_conn(cur.connection)
    with pytest.raises(psycopg.errors.DivisionByZero):
        next(reads).execute(_BOUND_PROBE)
    reads.close()
    cur.execute("INSERT INTO brokers (display_name) VALUES ('lp-facts') RETURNING id")
    broker_id = cur.fetchone()[0]
    monkeypatch.setattr(outreach, "footprint", lambda c, _: c.execute(_BOUND_PROBE))
    with pytest.raises(psycopg.errors.DivisionByZero):
        outreach._broker_context(cur.connection, broker_id)
