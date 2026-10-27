"""Curated membership is judged under the caller's RLS (migration 629), on the replayed schema."""

# Two tenants and seven properties, each listed in `browse_list` (minimum columns, as
# tests/test_stats_pivot_live.py seeds it): card (A's pipeline card + A's PRIVATE collection),
# shared (A's collection SHARED with B, migration 610), t12 / t1 / t2 (A's tags), plain, gone
# (dismissed by A, migration 536). Every read runs under `scoped` (the tenant_conn shape) through
# BOTH the SRF (`browse_list_visible`) and the stats RPC, whose `filtered` must match, so the two
# clause blocks are proven to agree. Seeding runs as the owner login inside one rolled-back
# transaction; the migrations lane sets DB_RAILS_REQUIRED=1, so a lost TEST_DATABASE_URL goes red.

from __future__ import annotations

import itertools
import json
import uuid
from typing import Any, NamedTuple

import pytest

from tests._live_property import REQUIRED_DB, _collection, _stage, _tag
from tests._tenant_scope import scoped

pytestmark = REQUIRED_DB

_IDS = itertools.count(9_730_000_001)
_SPEC = json.dumps({"rows": [], "cols": [], "measure": "area"})
_TYPES = {"hide_dismissed": "boolean"} | {
    f"{s}_{side}": ("text" if side == "set" else "bigint[]")
    for s in ("tags", "collections", "pipeline") for side in ("set", "in", "out")
}


class _Tenant(NamedTuple):
    acc: uuid.UUID
    user: uuid.UUID


class _World(NamedTuple):
    a: _Tenant
    b: _Tenant
    props: dict[str, int]
    stage: int
    private: int
    shared: int
    t1: int
    t2: int

    @property
    def everything(self) -> set[int]:
        return set(self.props.values())

    def ids(self, *names: str) -> set[int]:
        return {self.props[n] for n in names}


def _tenant(cur: Any) -> _Tenant:
    user = uuid.uuid4()
    cur.execute(
        "INSERT INTO accounts (kind, name) VALUES ('personal', %s) RETURNING id",
        (f"scope-{user.hex[:8]}",),
    )
    acc = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO auth.users (id, email, email_confirmed_at) VALUES (%s, %s, now())",
        (user, f"scope-{user.hex[:8]}@test.local"),
    )
    cur.execute(
        "INSERT INTO account_members (account_id, user_id, role) VALUES (%s, %s, 'owner')",
        (acc, user),
    )
    return _Tenant(acc, user)


def _listed(cur: Any) -> int:
    cur.execute("INSERT INTO properties DEFAULT VALUES RETURNING id")
    pid = int(cur.fetchone()[0])
    cur.execute(
        "INSERT INTO browse_list (property_id, listing_id, is_active, category_main, "
        "  category_type, area_m2, price_czk, price_per_m2_basis, all_sources, active_sources) "
        "VALUES (%s, %s, true, 'byt', 'prodej', 50, 5000000, 'sale_capital_czk_m2', "
        "  array['sreality'], array['sreality'])",
        (pid, next(_IDS)),
    )
    return pid


@pytest.fixture
def world(cur: Any) -> _World:
    # The on-signup trigger would mint its own accounts; disabled around the auth.users
    # inserts as in tests/test_tenant_isolation_live.py (the transaction rolls it back too).
    cur.execute("ALTER TABLE auth.users DISABLE TRIGGER USER")
    a, b = _tenant(cur), _tenant(cur)
    cur.execute("ALTER TABLE auth.users ENABLE TRIGGER USER")
    props = {n: _listed(cur) for n in ("card", "shared", "t12", "t1", "t2", "plain", "gone")}
    stage = _stage(cur, a.acc, position=1)
    private, shared = _collection(cur, a.acc), _collection(cur, a.acc)
    t1, t2 = _tag(cur, a.acc), _tag(cur, a.acc)
    cur.execute(
        "INSERT INTO property_pipeline (account_id, property_id, stage_id) VALUES (%s, %s, %s)",
        (a.acc, props["card"], stage),
    )
    for coll, pid in ((private, props["card"]), (shared, props["shared"])):
        cur.execute(
            "INSERT INTO collection_properties (collection_id, property_id) VALUES (%s, %s)",
            (coll, pid),
        )
    cur.execute(
        "INSERT INTO collection_shares (collection_id, recipient_account_id) VALUES (%s, %s)",
        (shared, b.acc),
    )
    for name, tag in (("t12", t1), ("t12", t2), ("t1", t1), ("t2", t2)):
        cur.execute(
            "INSERT INTO property_tags (account_id, property_id, tag_id) VALUES (%s, %s, %s)",
            (a.acc, props[name], tag),
        )
    cur.execute(
        "INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)",
        (a.acc, props["gone"]),
    )
    return _World(a, b, props, stage, private, shared, t1, t2)


def _named(args: dict[str, Any]) -> str:
    return "".join(f", {k} => %({k})s::{_TYPES[k]}" for k in args)


def _visible(cur: Any, w: _World, t: _Tenant, **args: Any) -> set[int]:
    """The cohort as `t` reads it, through the SRF; the stats RPC must count the same rows.
    `hide_dismissed` is passed to both (the SRF defaults it on, the RPC off)."""
    args = {"hide_dismissed": True} | args
    params = args | {"universe": sorted(w.everything), "spec": _SPEC}
    with scoped(t.user, cur=cur):
        cur.execute(
            f"SELECT property_id FROM public.browse_list_visible({_named(args)[2:]}) "
            "WHERE property_id = ANY(%(universe)s::bigint[])",
            params,
        )
        rows = {int(r[0]) for r in cur.fetchall()}
        cur.execute(
            "SELECT public.browse_stats_properties(property_ids_filter => %(universe)s::bigint[], "
            f"spec => %(spec)s::jsonb{_named(args)})",
            params,
        )
        stats = cur.fetchone()[0]
    assert stats["filtered"] == len(rows), (args, stats, rows)
    return rows


def test_a_card_and_a_collection_hide_the_property_from_their_owner_only(cur: Any, world: _World) -> None:
    w = world
    assert _visible(cur, w, w.a) == w.everything - w.ids("gone")
    assert w.props["card"] not in _visible(cur, w, w.a, pipeline_set="out")
    assert _visible(cur, w, w.a, pipeline_set="in") == w.ids("card")
    assert _visible(cur, w, w.a, pipeline_in=[w.stage]) == w.ids("card")
    assert w.props["card"] not in _visible(cur, w, w.a, pipeline_out=[w.stage])
    assert w.props["card"] not in _visible(cur, w, w.a, collections_set="out")
    # B holds no card and cannot read A's private collection: the same property stays.
    assert w.props["card"] in _visible(cur, w, w.b, pipeline_set="out")
    assert w.props["card"] in _visible(cur, w, w.b, collections_set="out")
    assert _visible(cur, w, w.b, pipeline_set="in") == set()
    assert _visible(cur, w, w.b, pipeline_in=[w.stage]) == set()
    assert _visible(cur, w, w.b, collections_in=[w.private]) == set()


def test_a_shared_collection_counts_for_the_recipient(cur: Any, world: _World) -> None:
    """Operator ruling 3: "any collection" spans own + shared with me."""
    w = world
    assert _visible(cur, w, w.b, collections_set="out") == w.everything - w.ids("shared")
    assert _visible(cur, w, w.b, collections_set="in") == w.ids("shared")
    assert _visible(cur, w, w.b, collections_in=[w.shared]) == w.ids("shared")
    assert w.props["shared"] not in _visible(cur, w, w.b, collections_out=[w.shared])
    assert _visible(cur, w, w.a, collections_set="in") == w.ids("card", "shared")


def test_tags_in_is_every_tag_and_tags_out_is_any_tag(cur: Any, world: _World) -> None:
    w = world
    assert _visible(cur, w, w.a, tags_in=[w.t1, w.t2]) == w.ids("t12")
    assert _visible(cur, w, w.a, tags_in=[w.t1, w.t2, w.t1]) == w.ids("t12")
    assert _visible(cur, w, w.a, tags_in=[w.t1]) == w.ids("t12", "t1")
    untagged = w.everything - w.ids("t12", "t1", "t2", "gone")
    assert _visible(cur, w, w.a, tags_out=[w.t1, w.t2]) == untagged
    assert _visible(cur, w, w.a, tags_set="out") == untagged
    assert _visible(cur, w, w.a, tags_set="in") == w.ids("t12", "t1", "t2")
    # A's tags are dark to B: B's "has the tag" is empty and "has none" keeps everything.
    assert _visible(cur, w, w.b, tags_in=[w.t1]) == set()
    assert _visible(cur, w, w.b, tags_set="out") == w.everything


@pytest.mark.parametrize("side", [f"{s}_{d}" for s in ("tags", "collections", "pipeline")
                                  for d in ("in", "out")])
def test_an_empty_element_list_constrains_nothing(cur: Any, world: _World, side: str) -> None:
    w = world
    assert _visible(cur, w, w.a, **{side: []}) == w.everything - w.ids("gone")


def test_hide_dismissed_is_the_default_and_per_account(cur: Any, world: _World) -> None:
    w = world
    with scoped(w.a.user, cur=cur):
        cur.execute(
            "SELECT property_id FROM public.browse_list_visible() WHERE property_id = ANY(%s)",
            (sorted(w.everything),),
        )
        default = {int(r[0]) for r in cur.fetchall()}
    assert default == w.everything - w.ids("gone")
    assert _visible(cur, w, w.a, hide_dismissed=False) == w.everything
    assert _visible(cur, w, w.b) == w.everything
