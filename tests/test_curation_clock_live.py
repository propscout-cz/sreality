"""The curation clock (migration 696, docs/design/curation-sync.md § 3.2-3.4 and § 9) on the replayed schema.

Five tenants born through the real signup trigger: A owns curation, B holds a READ share of A's pipeline, C an EDIT
share, D none, E is the party the share and cascade cases grant to and take back from. The clock moves at COMMIT
(deferred constraint triggers), so unlike the rollback-always lanes these cases COMMIT, each write in its own
transaction as the API's tenant pool runs it (`tests/_tenant_scope.scoped`: SET LOCAL ROLE authenticated + the claims,
header and method GUCs); the module's accounts and properties are deleted at the end. Counters are compared as
deltas: an account born after 696 gains a family's row on its first bump. Skips without TEST_DATABASE_URL; the
migrations lane sets DB_RAILS_REQUIRED=1, so a lane that loses its database goes red instead of green.
"""

from __future__ import annotations

import contextlib
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple

import psycopg
import pytest

from api import curation
from api import schemas as s
from scripts.checks import platform as plat
from scripts.verify_pipeline import THRESHOLDS
from tests._admin_gate_registry import ADMIN_ONLY_RELATIONS
from tests._live_property import REQUIRED_DB, _advert, _property, _recompute, db_url, signups
from tests._tenant_registry import CLOCKED_TABLES
from tests._tenant_scope import scoped
from tests.migration_defs import MIGRATIONS

pytestmark = REQUIRED_DB

SEEDED_FAMILIES = ("board", "dismissals", "collections", "tags", "notes", "links", "shares")   # 696's seed
FAMILIES = (*SEEDED_FAMILIES, "notifications")                                                 # + 715, unseeded
EDIT_FAMILIES = ("board", "dismissals", "collections", "tags", "notes", "links")
Moved = dict[tuple[uuid.UUID, str], int]


class _Tenant(NamedTuple):
    acc: uuid.UUID
    user: uuid.UUID


class _World(NamedTuple):
    a: _Tenant
    b: _Tenant
    c: _Tenant
    d: _Tenant
    e: _Tenant
    props: list[int]   # bare properties, each case taking its own
    entry: int         # A's entry stage
    coll: int          # A's collection
    tag: int           # A's tag
    note: int          # A's note on props[0], carrying a file
    card: int          # the property of A's card
    watchdog: uuid.UUID   # A's watchdog (715: notifications)
    event: uuid.UUID      # A's unseen event on it


@pytest.fixture(scope="module")
def svc() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(db_url(), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def _one(svc: psycopg.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    with svc.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


@pytest.fixture(scope="module")
def world(svc: psycopg.Connection) -> Iterator[_World]:
    """A's curation reaches every family (so A holds all eight clock rows), committed, as the owner login."""
    with svc.cursor() as cur:
        cur.execute("INSERT INTO properties (settled_at) SELECT now() FROM generate_series(1, 20) RETURNING id")
        props = [int(r[0]) for r in cur.fetchall()]
    try:
        with signups(svc, "clock", "a", "b", "c", "d", "e") as made:
            t = {k: _Tenant(made[f"{k}_acc"], made[k]) for k in "abcde"}
            a = t["a"].acc
            entry = _one(svc, "SELECT id FROM pipeline_stages WHERE account_id = %s AND is_entry", (a,))
            coll = _one(svc, "INSERT INTO collections (account_id, name) VALUES (%s, %s) RETURNING id",
                        (a, f"clock-{uuid.uuid4().hex[:10]}"))
            tag = _one(svc, "INSERT INTO tags (account_id, name, color) VALUES (%s, %s, 'slate') RETURNING id",
                       (a, f"clock-{uuid.uuid4().hex[:10]}"))
            note = _one(svc, "INSERT INTO property_notes (account_id, property_id, body) VALUES (%s, %s, 'clock') "
                             "RETURNING id", (a, props[0]))
            with svc.cursor() as cur:
                cur.execute(_ATTACH, (note, a, props[0]))
                cur.execute("INSERT INTO property_pipeline (account_id, property_id, stage_id) VALUES (%s, %s, %s)",
                            (a, props[1], entry))
                cur.execute("INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)", (a, props[2]))
                cur.execute("INSERT INTO property_artifact_links (account_id, property_id, url) "
                            "VALUES (%s, %s, 'https://example.test/clock')", (a, props[3]))
                cur.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) "
                            "VALUES (%s, %s, 'read'), (%s, %s, 'edit')", (a, t["b"].acc, a, t["c"].acc))
            watchdog = _one(svc, "INSERT INTO notification_subscriptions (account_id, name, filter_spec) "
                                 "VALUES (%s, %s, '{}') RETURNING id", (a, f"clock-{uuid.uuid4().hex[:10]}"))
            event = _one(svc, _EVENT + " RETURNING id", (watchdog, a, "new", props[18], None))
            yield _World(t["a"], t["b"], t["c"], t["d"], t["e"], props[4:18], entry, coll, tag, note, props[1],
                         watchdog, event)
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM properties WHERE id = ANY(%s)", (props,))


_ATTACH = ("INSERT INTO property_attachments (note_id, account_id, property_id, storage_key, filename, mime_type, "
           "byte_size, sha256_hex) VALUES (%s, %s, %s, 'k', 'a.pdf', 'application/pdf', 1, "
           "md5(random()::text) || md5(random()::text))")
# A watchdog's event, as the producer writes it (rule 16; 610's trigger fills account_id from the watchdog too):
# (watchdog, account, kind, property, event_ref).
_EVENT = ("INSERT INTO notification_dispatches (subscription_id, account_id, source_kind, change_kind, property_id, "
          "event_ref) VALUES (%s, %s, 'watchdog', %s, %s, %s)")


def _accounts(w: _World) -> list[uuid.UUID]:
    return [w.a.acc, w.b.acc, w.c.acc, w.d.acc, w.e.acc]


def _clock(svc: psycopg.Connection, accounts: list[uuid.UUID]) -> Moved:
    with svc.cursor() as cur:
        cur.execute("SELECT account_id, family, counter FROM curation_clock WHERE account_id = ANY(%s)", (accounts,))
        return {(acc, fam): int(n) for acc, fam, n in cur.fetchall()}


def _moved(before: Moved, after: Moved) -> Moved:
    return {k: after.get(k, 0) - before.get(k, 0) for k in before.keys() | after.keys()
            if after.get(k, 0) != before.get(k, 0)}


@contextmanager
def _watch(svc: psycopg.Connection, accounts: list[uuid.UUID]) -> Iterator[Moved]:
    """Yields a dict filled, on exit, with how far each (account, family) of `accounts` moved inside the block."""
    moved: Moved = {}
    before = _clock(svc, accounts)
    yield moved
    moved.update(_moved(before, _clock(svc, accounts)))


def _as(sub: uuid.UUID, sql: str, params: tuple[Any, ...] = (), *, act_as: uuid.UUID | str | None = None,
        method: str = "POST") -> int:
    """One statement in its own committed tenant transaction; its rowcount."""
    with scoped(sub, act_as=act_as, method=method) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


# --- 1. a tenant-shaped write on each clocked table bumps exactly its (account, family), once --------------------

def _write(w: _World, svc: psycopg.Connection, table: str) -> tuple[uuid.UUID, str, tuple[Any, ...]]:
    """(the writer, its statement, the parameters) for `table`; the share tables are left by their recipient E, and
    an event is marked seen (the one write a tenant has on it; the producers insert as service_role)."""
    a, nonce = w.a.acc, f"clock-{uuid.uuid4().hex[:10]}"
    if table == "notification_dispatches":
        return w.a.user, "UPDATE notification_dispatches SET seen_at = now() WHERE id = %s AND seen_at IS NULL", (w.event,)
    if table == "pipeline_shares":
        with svc.cursor() as cur:
            cur.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'read')",
                        (a, w.e.acc))
        return w.e.user, "DELETE FROM pipeline_shares WHERE account_id = %s", (a,)
    if table == "collection_shares":
        with svc.cursor() as cur:
            cur.execute("INSERT INTO collection_shares (collection_id, recipient_account_id) VALUES (%s, %s)",
                        (w.coll, w.e.acc))
        return w.e.user, "DELETE FROM collection_shares WHERE collection_id = %s", (w.coll,)
    p = w.props
    return w.a.user, *{
        "property_pipeline": ("INSERT INTO property_pipeline (account_id, property_id, stage_id) VALUES (%s, %s, %s)",
                              (a, p[0], w.entry)),
        "pipeline_stages": ("INSERT INTO pipeline_stages (account_id, key, label, position) VALUES (%s, %s, 'c', 99)",
                            (a, nonce)),
        "property_dismissals": ("INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)", (a, p[1])),
        "collections": ("INSERT INTO collections (account_id, name) VALUES (%s, %s)", (a, nonce)),
        "collection_properties": ("INSERT INTO collection_properties (collection_id, property_id) VALUES (%s, %s)",
                                  (w.coll, p[2])),
        "tags": ("INSERT INTO tags (account_id, name, color) VALUES (%s, %s, 'slate')", (a, nonce)),
        "property_tags": ("INSERT INTO property_tags (property_id, tag_id) VALUES (%s, %s)", (p[3], w.tag)),
        "property_notes": ("INSERT INTO property_notes (account_id, property_id, body) VALUES (%s, %s, %s)",
                           (a, p[4], nonce)),
        "property_attachments": (_ATTACH, (w.note, a, _note_property(svc, w.note))),
        "property_artifact_links": ("INSERT INTO property_artifact_links (account_id, property_id, url) "
                                    "VALUES (%s, %s, 'https://example.test/x')", (a, p[5])),
        "notification_subscriptions": ("INSERT INTO notification_subscriptions (account_id, name, filter_spec) "
                                       "VALUES (%s, %s, '{}')", (a, nonce)),
    }[table]


def _note_property(svc: psycopg.Connection, note: int) -> int:
    return int(_one(svc, "SELECT property_id FROM property_notes WHERE id = %s", (note,)))


@pytest.mark.parametrize("table", sorted(CLOCKED_TABLES))
def test_a_write_on_each_clocked_table_bumps_its_family_once(svc: psycopg.Connection, world: _World,
                                                             table: str) -> None:
    family = CLOCKED_TABLES[table]
    sub, sql, params = _write(world, svc, table)
    with _watch(svc, _accounts(world)) as moved:
        assert _as(sub, sql, params) == 1
    parties = [world.a.acc, world.e.acc] if family == "shares" else [world.a.acc]
    assert moved == {(acc, family): 1 for acc in parties}


def test_a_seen_event_a_new_event_and_a_retired_watchdog_each_bump_notifications_once_and_no_share_follows_it(
        svc: psycopg.Connection, world: _World) -> None:
    """The operator's ask (Q1b re-ruled 2026-11-01): marking an event seen anywhere, an event a producer writes, and a
    watchdog retired (its events leave VISIBLE_SQL) each move the account's notifications counter once; a read or
    edit share follows none of it (own-only), and a service-role bulk insert bumps the account once."""
    a = world.a
    with _watch(svc, _accounts(world)) as moved:
        _one(svc, _EVENT + " RETURNING id", (world.watchdog, a.acc, "price_drop", world.props[13], "snap-1"))
    assert moved == {(a.acc, "notifications"): 1}, "a producer's event"
    with _watch(svc, _accounts(world)) as moved:
        assert _as(a.user, "UPDATE notification_dispatches SET seen_at = now() WHERE subscription_id = %s "
                           "AND seen_at IS NULL AND change_kind = 'price_drop'", (world.watchdog,)) == 1
    assert moved == {(a.acc, "notifications"): 1}, "marked seen"
    with _watch(svc, _accounts(world)) as moved:
        with svc.cursor() as cur:
            cur.execute("INSERT INTO notification_dispatches (subscription_id, account_id, source_kind, change_kind, "
                        "property_id, event_ref) SELECT %s, %s, 'watchdog', 'price_rise', %s, 'snap-' || (100 + g) "
                        "FROM generate_series(1, 50) g", (world.watchdog, a.acc, world.props[13]))
    assert moved == {(a.acc, "notifications"): 1}, "a bulk insert bumps the account once"
    with _watch(svc, _accounts(world)) as moved:
        assert _as(a.user, "UPDATE notification_subscriptions SET retired_at = now() WHERE id = %s",
                   (world.watchdog,)) == 1
    assert moved == {(a.acc, "notifications"): 1}, "retired: its events leave the bell"
    assert "notifications" in {f for acc, f, _m in _view(a.user) if acc == a.acc}
    for who in (world.b, world.c):
        assert not any(f == "notifications" for acc, f, _m in _view(who.user) if acc == a.acc), "own-only"


def test_another_connection_sees_no_bump_before_the_commit(svc: psycopg.Connection, world: _World) -> None:
    accounts = _accounts(world)
    before = _clock(svc, accounts)
    with scoped(world.a.user, method="PATCH") as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE property_pipeline SET board_position = board_position + 1 "
                        "WHERE account_id = %s AND property_id = %s", (world.a.acc, world.card))
            assert cur.rowcount == 1
        assert _clock(svc, accounts) == before, "the bump is visible before the writer committed"
    assert _moved(before, _clock(svc, accounts)) == {(world.a.acc, "board"): 1}


# --- 2-3. nothing moves without a committed change; a bulk write moves once ---------------------------------------

def test_a_rollback_a_savepoint_rollback_a_zero_row_statement_and_a_no_op_update_bump_nothing(
    svc: psycopg.Connection, world: _World,
) -> None:
    insert = "INSERT INTO tags (account_id, name, color) VALUES (%s, %s, 'slate')"
    with _watch(svc, _accounts(world)) as moved:
        conn = psycopg.connect(db_url())
        try:
            conn.execute(insert, (world.a.acc, f"rb-{uuid.uuid4().hex[:8]}"))
            conn.rollback()
            with conn.transaction():
                try:
                    with conn.transaction():  # a SAVEPOINT
                        conn.execute(insert, (world.a.acc, f"sp-{uuid.uuid4().hex[:8]}"))
                        raise RuntimeError("roll the savepoint back")
                except RuntimeError:
                    pass
            conn.commit()
        finally:
            conn.close()
        assert _as(world.a.user, "DELETE FROM tags WHERE id = -1") == 0
        assert _as(world.a.user, "UPDATE tags SET name = name, color = color WHERE id = %s", (world.tag,)) == 1
    assert moved == {}


def test_a_500_row_insert_bumps_collections_once(svc: psycopg.Connection, world: _World) -> None:
    with svc.cursor() as cur:
        cur.execute("INSERT INTO properties (settled_at) SELECT now() FROM generate_series(1, 500) RETURNING id")
        bulk = [int(r[0]) for r in cur.fetchall()]
    try:
        with _watch(svc, _accounts(world)) as moved:
            n = _as(world.a.user, "INSERT INTO collection_properties (collection_id, property_id) "
                                  "SELECT %s, unnest(%s::bigint[])", (world.coll, bulk))
        assert n == 500 and moved == {(world.a.acc, "collections"): 1}
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM collection_properties WHERE collection_id = %s AND property_id = ANY(%s)",
                        (world.coll, bulk))
            cur.execute("DELETE FROM properties WHERE id = ANY(%s)", (bulk,))


# --- 4-6. every writer: a delegate, the service role, the owner login, cascades ---------------------------------

def test_a_delegates_write_bumps_the_owner(svc: psycopg.Connection, world: _World) -> None:
    with _watch(svc, _accounts(world)) as moved:
        assert _as(world.c.user, "INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)",
                   (world.a.acc, world.props[6]), act_as=world.a.acc) == 1
    assert moved == {(world.a.acc, "dismissals"): 1}, "an edit share's write is the owner's change"


def test_an_autocommit_service_role_write_bumps(svc: psycopg.Connection, world: _World) -> None:
    from scraper import db

    conn = db.connect(db_url())
    try:
        with _watch(svc, _accounts(world)) as moved:
            conn.execute("UPDATE property_notes SET body = body || '.' WHERE id = %s", (world.note,))
    finally:
        conn.close()
    assert moved == {(world.a.acc, "notes"): 1}


def test_a_service_role_merge_bumps_both_accounts(world: _World) -> None:
    """merge_property_set over a property carrying A's note and D's dismissal, inside one transaction rolled back at
    the end (a merge leaves records this lane cannot delete): the seeds' bumps are fired and the dedupe GUC cleared
    first, so what moves is the merge's own carrier writes, as at a real merge's commit."""
    from toolkit.property_identity import merge_property_set

    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            s, r = _property(cur), _property(cur)
            _advert(cur, s, source="sreality")
            _advert(cur, r, source="idnes")
            _recompute(cur, s)
            _recompute(cur, r)
            cur.execute("INSERT INTO property_notes (account_id, property_id, body) VALUES (%s, %s, 'on r')",
                        (world.a.acc, r))
            cur.execute("INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)", (world.d.acc, r))
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cur.execute("SET CONSTRAINTS ALL DEFERRED")
            cur.execute("SELECT set_config('curation_clock.bumped', '', true)")
            before = _clock(conn, [world.a.acc, world.d.acc])
            merge_property_set(conn, [s, r], source="operator", reason="manual_subset",
                               decided_by="ci-operator@replay.local")
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            moved = _moved(before, _clock(conn, [world.a.acc, world.d.acc]))
    finally:
        conn.rollback()
        conn.close()
    assert moved == {(world.a.acc, "notes"): 1, (world.d.acc, "dismissals"): 1}


_FIRED = ("SELECT coalesce((SELECT calls FROM pg_stat_xact_user_functions "
          "WHERE funcid = 'public.bump_curation_clock()'::regprocedure), 0)")


def _fired(sub: uuid.UUID | None, sql: str, params: tuple[Any, ...]) -> tuple[int, int]:
    """One committed statement, as tenant `sub` or (None) the service role: (its rowcount, how often
    bump_curation_clock() fired for it). A cascaded child that names its parent's (account, family) is folded into the
    parent's bump by the dedupe, so only the firing count shows the child's trigger ran. track_functions is set as the
    superuser before the tenant scope; the deferred firings are run at SET CONSTRAINTS ALL IMMEDIATE and counted."""
    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL track_functions = 'pl'")
            with scoped(sub, method="DELETE", cur=cur) if sub else contextlib.nullcontext():
                cur.execute(sql, params)
                n = cur.rowcount
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cur.execute(_FIRED)
            fired = int(cur.fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    return n, fired


def test_cascades_and_the_files_follow_trigger_bump(svc: psycopg.Connection, world: _World) -> None:
    a, e = world.a.acc, world.e.acc
    doomed = _one(svc, "INSERT INTO collections (account_id, name) VALUES (%s, %s) RETURNING id",
                  (a, f"doomed-{uuid.uuid4().hex[:8]}"))
    doomed_tag = _one(svc, "INSERT INTO tags (account_id, name, color) VALUES (%s, %s, 'slate') RETURNING id",
                      (a, f"doomed-{uuid.uuid4().hex[:8]}"))
    with svc.cursor() as cur:
        cur.execute("INSERT INTO collection_properties (collection_id, property_id) VALUES (%s, %s)",
                    (doomed, world.props[7]))
        cur.execute("INSERT INTO collection_shares (collection_id, recipient_account_id) VALUES (%s, %s)", (doomed, e))
        cur.execute("INSERT INTO property_tags (property_id, tag_id) VALUES (%s, %s)", (world.props[7], doomed_tag))
    with _watch(svc, _accounts(world)) as moved:
        assert _fired(world.a.user, "DELETE FROM collections WHERE id = %s", (doomed,)) == (1, 3), (
            "the collection, its one member and its one share each fire the clock")
    assert moved == {(a, "collections"): 1, (a, "shares"): 1, (e, "shares"): 1}, "610's share cascade bumps both"
    with _watch(svc, _accounts(world)) as moved:
        assert _fired(world.a.user, "DELETE FROM tags WHERE id = %s", (doomed_tag,)) == (1, 2), (
            "the tag and its one membership each fire the clock")
    assert moved == {(a, "tags"): 1}
    assert _one(svc, "SELECT count(*) FROM property_tags WHERE tag_id = %s", (doomed_tag,)) == 0
    # A note changes property only on the service role (the merge carriers; a tenant holds no UPDATE on files).
    files = _one(svc, "SELECT count(*) FROM property_attachments WHERE note_id = %s", (world.note,))
    with _watch(svc, _accounts(world)) as moved:
        assert _fired(None, "UPDATE property_notes SET property_id = %s WHERE id = %s",
                      (world.props[8], world.note)) == (1, 1 + files), f"the note and each of its {files} files fire"
    assert files >= 1 and moved == {(a, "notes"): 1}
    assert _one(svc, "SELECT property_id FROM property_attachments WHERE note_id = %s LIMIT 1",
                (world.note,)) == world.props[8], "660's files-follow trigger moved the note's files"


def test_a_property_delete_bumps_every_cascaded_family_and_an_account_move_bumps_both_accounts(
    svc: psycopg.Connection, world: _World,
) -> None:
    """No parent table of any family is written here: every bump comes from a cascade, or from the OLD and NEW arms
    of one UPDATE."""
    a, d = world.a.acc, world.d.acc
    gone, moving = world.props[12], world.props[13]
    with svc.cursor() as cur:
        cur.execute("INSERT INTO collection_properties (collection_id, property_id) VALUES (%s, %s)", (world.coll, gone))
        cur.execute("INSERT INTO property_tags (property_id, tag_id) VALUES (%s, %s)", (gone, world.tag))
        cur.execute("INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)", (d, gone))
        cur.execute("INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)", (a, moving))
    with _watch(svc, _accounts(world)) as moved:
        assert _fired(None, "DELETE FROM properties WHERE id = %s", (gone,)) == (1, 3)
    assert moved == {(a, "collections"): 1, (a, "tags"): 1, (d, "dismissals"): 1}
    with _watch(svc, _accounts(world)) as moved:
        with svc.cursor() as cur:
            cur.execute("UPDATE property_dismissals SET account_id = %s WHERE account_id = %s AND property_id = %s",
                        (d, a, moving))
            assert cur.rowcount == 1
    assert moved == {(a, "dismissals"): 1, (d, "dismissals"): 1}, "the row left A and reached D"


# --- 7-8. shares, signup, account deletion ----------------------------------------------------------------------

def _view(sub: uuid.UUID, *, act_as: str | uuid.UUID | None = None) -> set[tuple[uuid.UUID, str, bool]]:
    with scoped(sub, act_as=act_as, method="GET") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT account_id, family, mine FROM curation_clock_public")
            return set(cur.fetchall())


def _base(sub: uuid.UUID, *, act_as: str | uuid.UUID | None = None) -> set[tuple[uuid.UUID, str]]:
    with scoped(sub, act_as=act_as, method="GET") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT account_id, family FROM curation_clock")
            return set(cur.fetchall())


def _audience(sub: uuid.UUID, *, act_as: str | uuid.UUID | None = None) -> set[tuple[uuid.UUID, bool, bool]]:
    with scoped(sub, act_as=act_as, method="GET") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT account_id, mine, edit FROM curation_clock_audience()")
            return set(cur.fetchall())


def _families(svc: psycopg.Connection, acc: uuid.UUID) -> set[str]:
    with svc.cursor() as cur:
        cur.execute("SELECT family FROM curation_clock WHERE account_id = %s", (acc,))
        return {r[0] for r in cur.fetchall()}


def test_a_grant_a_downgrade_and_a_revoke_bump_both_parties_and_the_next_read_follows(
    svc: psycopg.Connection, world: _World,
) -> None:
    a, e = world.a.acc, world.e.acc
    assert _families(svc, a) == set(FAMILIES), "the world gave A every family"
    with _watch(svc, _accounts(world)) as moved:
        with svc.cursor() as cur:
            cur.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'edit')",
                        (a, e))
    assert moved == {(a, "shares"): 1, (e, "shares"): 1}
    assert {(f, m) for acc, f, m in _view(world.e.user) if acc == a} == {(f, False) for f in EDIT_FAMILIES}
    with _watch(svc, _accounts(world)) as moved:
        with svc.cursor() as cur:
            cur.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'read') "
                        "ON CONFLICT (account_id, recipient_account_id) DO UPDATE SET mode = excluded.mode", (a, e))
    assert moved == {(a, "shares"): 1, (e, "shares"): 1}
    assert {(f, m) for acc, f, m in _view(world.e.user) if acc == a} == {("board", False)}, (
        "after the downgrade the recipient's next read loses the five edit families")
    with _watch(svc, _accounts(world)) as moved:
        assert _as(world.a.user, "DELETE FROM pipeline_shares WHERE recipient_account_id = %s", (e,)) == 1
    assert moved == {(a, "shares"): 1, (e, "shares"): 1}
    assert not {acc for acc, _f, _m in _view(world.e.user)} - {e}, "a revoked recipient follows nothing of A"


def test_signup_bumps_board_and_collections_and_an_account_holding_curation_can_be_deleted(
    svc: psycopg.Connection, world: _World,
) -> None:
    d = world.d.acc
    with signups(svc, "clock-born", "n") as made:
        n = made["n_acc"]
        assert _clock(svc, [n]) == {(n, "board"): 1, (n, "collections"): 1}, "the signup's board and collection"
        with svc.cursor() as cur:
            cur.execute("INSERT INTO property_notes (account_id, property_id, body) VALUES (%s, %s, 'n')",
                        (n, world.props[9]))
            cur.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'edit')",
                        (n, d))
        before = _clock(svc, [d])
    assert _one(svc, "SELECT count(*) FROM accounts WHERE id = %s", (n,)) == 0, "the account was deleted"
    assert _clock(svc, [n]) == {}, "its counters went with it"
    assert _moved(before, _clock(svc, [d])) == {(d, "shares"): 1}, "the cascaded share bumped the other party"


# --- 9. a commit-time failure leaves no change -----------------------------------------------------------------

def test_a_bump_that_cannot_lock_its_counter_fails_the_commit_and_the_change_is_absent(
    svc: psycopg.Connection, world: _World,
) -> None:
    """The case W0 surfaces as a 5xx: the bump runs at COMMIT, so a lock it cannot take fails the commit itself."""
    a = world.a.acc
    position = _one(svc, "SELECT board_position FROM property_pipeline WHERE account_id = %s AND property_id = %s",
                    (a, world.card))
    locker, writer = psycopg.connect(db_url()), psycopg.connect(db_url())
    try:
        locker.execute("SELECT 1 FROM curation_clock WHERE account_id = %s AND family = 'board' FOR UPDATE", (a,))
        before = _clock(svc, [a])
        with writer.cursor() as cur:
            cur.execute("SET lock_timeout = '300ms'")
            with scoped(world.a.user, method="PATCH", cur=cur):
                cur.execute("UPDATE property_pipeline SET board_position = 4242 WHERE account_id = %s "
                            "AND property_id = %s", (a, world.card))
                assert cur.rowcount == 1
        with pytest.raises(psycopg.errors.LockNotAvailable):
            writer.commit()
    finally:
        locker.rollback()
        locker.close()
        writer.close()
    assert _one(svc, "SELECT board_position FROM property_pipeline WHERE account_id = %s AND property_id = %s",
                (a, world.card)) == position, "the card change committed although its bump failed"
    assert _clock(svc, [a]) == before


# --- 10. who sees which counters ------------------------------------------------------------------------------

def test_each_caller_follows_exactly_its_audience(svc: psycopg.Connection, world: _World) -> None:
    a = world.a.acc
    owner = {(a, f) for f in _families(svc, a)}
    allowed = {world.a: set(), world.b: {"board"}, world.c: set(EDIT_FAMILIES), world.d: set()}
    for who, follows in allowed.items():
        own = {(who.acc, f) for f in _families(svc, who.acc)}
        theirs = {(acc, f) for acc, f in owner if f in follows} if who != world.a else set()
        assert _view(who.user) == {(acc, f, True) for acc, f in own} | {(acc, f, False) for acc, f in theirs}, who
        assert _base(who.user) == own | theirs, f"the base table scopes exactly like the view: {who}"
    assert _audience(world.a.user) == {(a, True, True)}
    assert _audience(world.b.user) == {(world.b.acc, True, True), (a, False, False)}
    assert _audience(world.c.user) == {(world.c.acc, True, True), (a, False, True)}
    assert _audience(world.d.user) == {(world.d.acc, True, True)}
    assert not any(f == "shares" for acc, f, _m in _view(world.c.user) if acc == a), "never the owner's shares"


@pytest.mark.parametrize(("caller", "header"), [("b", "owner"), ("c", "owner"), ("d", "owner"), ("c", "garbage")])
def test_a_clock_read_sent_with_x_act_as_sees_own_rows_only(svc: psycopg.Connection, world: _World,
                                                           caller: str, header: str) -> None:
    """The clock read never acts: the share rows hide while acting, so a stray header fails closed to own rows."""
    who: _Tenant = getattr(world, caller)
    act_as = world.a.acc if header == "owner" else "not-a-uuid"
    own = {(who.acc, f) for f in _families(svc, who.acc)}
    assert _view(who.user, act_as=act_as) == {(acc, f, True) for acc, f in own}
    assert _base(who.user, act_as=act_as) == own
    assert _audience(who.user, act_as=act_as) == {(who.acc, True, True)}


def test_a_caller_in_several_accounts_follows_each_as_own_and_a_share_between_them_as_none(svc: psycopg.Connection,
                                                                                            world: _World) -> None:
    """C joins B's account too: C's read holds B's rows as own (mine) beside C's and A's edit share, and a share from
    B to C is no share row (709's `s.account_id not in me`), so B's families are followed whole, not as a board."""
    a, b, c = world.a, world.b, world.c
    try:
        with svc.cursor() as cur:
            cur.execute("INSERT INTO account_members (account_id, user_id, role) VALUES (%s, %s, 'member')",
                        (b.acc, c.user))
            cur.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) "
                        "VALUES (%s, %s, 'read')", (b.acc, c.acc))
        assert _audience(c.user) == {(c.acc, True, True), (b.acc, True, True), (a.acc, False, True)}
        rows = _view(c.user)
        assert {acc for acc, _f, _m in rows} == {a.acc, b.acc, c.acc}, rows
        assert all(mine for acc, _f, mine in rows if acc in (b.acc, c.acc)), rows
        assert {f for acc, f, _m in rows if acc == b.acc} == _families(svc, b.acc), rows  # whole, not a board
        assert {"board", "shares"} <= _families(svc, b.acc), "B's signup and the share above bumped these"
        assert {f for acc, f, _m in rows if acc == a.acc} == set(EDIT_FAMILIES), rows
        assert _view(c.user, act_as=a.acc) == {(acc, f, True) for acc, f, _m in rows if acc != a.acc}, "follow, never act"
    finally:
        with svc.cursor() as cur:
            cur.execute("DELETE FROM pipeline_shares WHERE account_id = %s AND recipient_account_id = %s", (b.acc, c.acc))
            cur.execute("DELETE FROM account_members WHERE account_id = %s AND user_id = %s", (b.acc, c.user))


def test_a_collection_patch_clears_a_description_on_null_or_blank_and_keeps_it_when_absent(
        svc: psycopg.Connection, world: _World) -> None:
    """#2208, on the real SQL and through the tenant's row policy: each kind of patch, and each clearing bumps once."""
    def patch(**fields: Any) -> str | None:
        with scoped(world.a.user, method="PATCH") as conn:
            return curation.update_collection(conn, world.coll, s.UpdateCollectionIn(**fields))["description"]

    def stored() -> str | None:
        return _one(svc, "SELECT description FROM collections WHERE id = %s", (world.coll,))

    assert patch(description="kept") == "kept" == stored()
    assert patch(monitoring_enabled=False) == "kept" == stored(), "left out keeps it"
    with _watch(svc, _accounts(world)) as moved:
        assert patch(description=None) is None and stored() is None, "an explicit null clears it, alone"
    assert moved == {(world.a.acc, "collections"): 1}
    patch(description="again")
    assert patch(description="") is None and stored() is None, "blank is NULL, never ''"


def test_anon_reads_no_clock(svc: psycopg.Connection) -> None:
    for sql in ("SELECT 1 FROM curation_clock_public", "SELECT 1 FROM curation_clock",
                "SELECT 1 FROM curation_clock_audience()"):
        conn = psycopg.connect(db_url())
        try:
            conn.execute("SET ROLE anon")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
        finally:
            conn.close()


def test_the_service_role_reads_the_table_whole_and_the_view_is_the_browsers(svc: psycopg.Connection,
                                                                            world: _World) -> None:
    accounts = _accounts(world)
    owner = _one(svc, "SELECT count(*) FROM curation_clock WHERE account_id = ANY(%s)", (accounts,))
    conn = psycopg.connect(db_url())
    try:
        conn.execute("SET ROLE service_role")
        seen = conn.execute("SELECT count(*) FROM curation_clock WHERE account_id = ANY(%s)", (accounts,)).fetchone()
        assert seen is not None and seen[0] == owner > 0, "the service role reads every account's counters (701)"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM curation_clock_public")
    finally:
        conn.close()


def test_no_browser_role_writes_the_clock(world: _World) -> None:
    a = world.a.acc
    for sql, params in (("INSERT INTO curation_clock (account_id, family) VALUES (%s, 'tags')", (a,)),
                        ("UPDATE curation_clock SET counter = 0 WHERE account_id = %s", (a,)),
                        ("DELETE FROM curation_clock WHERE account_id = %s", (a,))):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as(world.a.user, sql, params)


# --- 11. one clock read evaluates the audience twice and keeps the primary key (708, F16 #2308) ------------------

_READ = "SELECT account_id, family, counter, mine, age_ms FROM curation_clock_public"


def _plan_nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every node of a JSON plan, its SubPlans and InitPlans included (they sit in `Plans` too)."""
    yield plan
    for child in plan.get("Plans", []):
        yield from _plan_nodes(child)


def _explain_json(cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    """The read's EXPLAIN (ANALYZE, BUFFERS) document in the caller's scope: its `Plan` and `Planning`."""
    cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {_READ}")
    out = cur.fetchone()[0]
    return (json.loads(out) if isinstance(out, str) else out)[0]


def _analyzed_read(cur: psycopg.Cursor[Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The read's analyzed plan in the caller's scope: (its top node, every audience Function Scan in it)."""
    top = _explain_json(cur)["Plan"]
    return top, [n for n in _plan_nodes(top) if n.get("Function Name") == "curation_clock_audience"]


def _two_thousand_accounts(cur: psycopg.Cursor[Any], families: tuple[str, ...] = ()) -> None:
    """2,000 accounts inside the caller's transaction, analyzed: each with a tag (its counter bumped through the
    trigger) or, given `families`, a counter for each of them written straight into the clock."""
    made = ("WITH made AS (INSERT INTO accounts (kind, name) SELECT 'personal', 'clock-scale-' || g "
            "FROM generate_series(1, 2000) g RETURNING id) ")
    if families:
        cur.execute(made + "INSERT INTO curation_clock (account_id, family) SELECT m.id, f "
                    "FROM made m, unnest(%s::text[]) f ON CONFLICT DO NOTHING", (list(families),))
    else:
        cur.execute(made + "INSERT INTO tags (account_id, name, color) SELECT id, 'scale', 'slate' FROM made")
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
    cur.execute("ANALYZE curation_clock")


def test_one_clock_read_evaluates_the_audience_twice_and_maps_mine(world: _World) -> None:
    """696 evaluated it four times per read (three policy arms and the view's join: 4.9 ms mean in production against
    gate #2203's 2.5 ms). 708's policy is one pair lookup, so a read evaluates it twice, each once: in the policy and in
    the view's join, which maps `mine` (ruling 2026-11-01)."""
    a, c = world.a.acc, world.c.acc
    with scoped(world.c.user) as conn:
        with conn.cursor() as cur:
            top, audience = _analyzed_read(cur)
            cur.execute(_READ)
            rows = {(acc, fam): mine for acc, fam, _n, mine, _age in cur.fetchall()}
    assert len(audience) == 2, f"two audience evaluations, the policy's and the view's join: {top}"
    assert all(n["Actual Loops"] == 1 for n in audience), f"each ran once, not per row: {top}"
    assert {acc for acc, _f in rows} == {a, c}, rows
    assert all(mine for (acc, _f), mine in rows.items() if acc == c), f"own rows are mine: {rows}"
    assert not any(mine for (acc, _f), mine in rows.items() if acc == a), f"the shared owner's are not: {rows}"
    assert {f for acc, f in rows if acc == a} == set(EDIT_FAMILIES), rows


def test_the_clock_read_probes_the_primary_key_at_scale(world: _World) -> None:
    """2,000 accounts with a counter each (through the trigger), analyzed inside a transaction rolled back after. The
    view's join probes curation_clock_pkey per followed account, so the read does not grow with the clock; a policy's
    subquery never becomes a join, so without that join the read scans the whole clock (charter § 3.8, § 12 F16)."""
    a, c = world.a.acc, world.c.acc
    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            _two_thousand_accounts(cur)
            cur.execute("SELECT count(*) FROM curation_clock")
            rows = cur.fetchone()[0]
            with scoped(world.c.user, cur=cur):
                top, audience = _analyzed_read(cur)
                cur.execute(_READ)
                accounts = {r[0] for r in cur.fetchall()}
    finally:
        conn.rollback()
        conn.close()
    clock = [n for n in _plan_nodes(top) if n.get("Relation Name") == "curation_clock"
             or n.get("Index Name") == "curation_clock_pkey"]
    assert rows > 2000, rows
    assert any(n.get("Index Name") == "curation_clock_pkey" for n in clock), top
    assert not any(n["Node Type"] == "Seq Scan" for n in clock), f"no pass over the {rows} clock rows: {top}"
    assert len(audience) == 2 and all(n["Actual Loops"] == 1 for n in audience), top
    assert accounts == {a, c}, accounts


def _post(migration: str, tag: str = "post") -> str:
    sql = (MIGRATIONS / migration).read_text(encoding="utf-8")
    m = re.search(rf"^do \${tag}\$.*?^end \${tag}\$;", sql, re.DOTALL | re.MULTILINE)
    assert m, f"{migration} lost its ${tag}$ block"
    return m.group(0)


def _post_708() -> str:
    return _post("708_curation_clock_read_one_audience.sql")


def _audience_of(migration: str) -> str:
    """The CREATE OR REPLACE of curation_clock_audience() in `migration`, as written there."""
    sql = (MIGRATIONS / migration).read_text(encoding="utf-8")
    m = re.search(r"^create or replace function public\.curation_clock_audience\(\).*?\$\$;", sql,
                  re.DOTALL | re.MULTILINE)
    assert m, f"{migration} lost its audience"
    return m.group(0)


def test_708s_post_refuses_a_clock_without_row_security_or_without_the_views_join() -> None:
    """708's $post$ passes on the schema as 708 left it: the lane's, with 696's invoker audience back (709 made it
    definer, which 708's $post$ refuses by design). It refuses the clock with row security off (the view's join keeps
    the accounts, the policy alone a share's families) and the view rewritten without its join on the audience (708 as
    first built: the join is what keeps curation_clock_pkey at scale)."""
    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            cur.execute(_audience_of("696_curation_clock.sql"))
            cur.execute(_post_708())
            cur.execute("SAVEPOINT bite")
            cur.execute("ALTER TABLE public.curation_clock DISABLE ROW LEVEL SECURITY")
            with pytest.raises(psycopg.errors.RaiseException, match="row security is off on curation_clock"):
                cur.execute(_post_708())
            cur.execute("ROLLBACK TO SAVEPOINT bite")
            cur.execute("CREATE OR REPLACE VIEW public.curation_clock_public WITH (security_invoker = true) AS "
                        "SELECT c.account_id, c.family, c.counter, "
                        "c.account_id IN (SELECT public.current_account_ids()) AS mine, "
                        "greatest(0, extract(epoch FROM statement_timestamp() - c.changed_at) * 1000)::bigint "
                        "AS age_ms FROM public.curation_clock c")
            with pytest.raises(psycopg.errors.RaiseException, match="joins the audience once"):
                cur.execute(_post_708())
    finally:
        conn.rollback()
        conn.close()


# --- 11b. the audience is planned once per backend and calls current_account_ids() once (709, F17 #2311) ---------

_AUDIENCE_709 = "709_curation_clock_audience_plan.sql"
_CALLS = ("SELECT p.proname, pg_stat_get_xact_function_calls(p.oid) FROM pg_proc p "
          "WHERE p.pronamespace = 'public'::regnamespace "
          "AND p.proname IN ('curation_clock_audience', 'current_account_ids', 'acting')")


def _warm_read(cur: psycopg.Cursor[Any]) -> tuple[int, dict[str, int]]:
    """One read on a backend that has served three before it, under track_functions = 'all': (its shared buffers,
    planning included; how often it called the audience, current_account_ids() and acting())."""
    for _ in range(3):
        cur.execute(_READ)
        cur.fetchall()
    cur.execute(_CALLS)
    before = {name: int(n or 0) for name, n in cur.fetchall()}
    doc = _explain_json(cur)
    cur.execute(_CALLS)
    calls = {name: int(n or 0) - before.get(name, 0) for name, n in cur.fetchall()}
    blocks = sum(part.get(f"Shared {kind} Blocks", 0) for part in (doc["Plan"], doc.get("Planning", {}))
                 for kind in ("Hit", "Read"))
    return blocks, calls


def _rows_on_this_backend(cur: psycopg.Cursor[Any]) -> tuple[set[tuple[uuid.UUID, str, bool]],
                                                            set[tuple[uuid.UUID, bool, bool]]]:
    """What the caller in scope reads on this cursor's backend: the clock's (account, family, mine) and its audience."""
    cur.execute(_READ)
    rows = {(acc, fam, mine) for acc, fam, _n, mine, _age in cur.fetchall()}
    cur.execute("SELECT account_id, mine, edit FROM curation_clock_audience()")
    return rows, set(cur.fetchall())


# 708's SQL audience was planned at every call and ran current_account_ids() five times per evaluation (three in its
# body, two in pipeline_shares' policy): 2.63 ms mean in production against gate #2203's 2.5 ms. 709's is plpgsql
# (planned once per backend), definer (no pipeline_shares policy) and calls it once, in a CTE, so a warm read calls it
# twice, once per evaluation; that count holds the shape (the next test shows 708's breaking it). 40 shared buffers
# is the ticket's ceiling against a gross regression, not against 708, whose warm read fits it (11-18 in § 3.8, ~20
# here; 709 ~7): the next test shows it catching a read that scans a full clock. At the lane's size the read scans the
# clock's few pages, so the dead rows earlier cases rolled back (2,000 at a time) are vacuumed first: on a lane
# database reused across runs they alone took 709's read to 40. ONE backend serves every caller in turn, so the plan
# the owner's first read prepared answers the others, each as a fresh connection would (case 10).
def test_a_warm_clock_read_calls_current_account_ids_once_per_evaluation_within_40_buffers(svc: psycopg.Connection,
                                                                                            world: _World) -> None:
    """Warm, each caller reads its own rows with two audience, two current_account_ids() and two acting() calls, ≤ 40 buffers."""
    a, b, c = world.a, world.b, world.c
    seen: dict[tuple[str, str], tuple[int, dict[str, int]]] = {}
    read: dict[tuple[str, str], tuple[set[tuple[uuid.UUID, str, bool]], set[tuple[uuid.UUID, bool, bool]]]] = {}
    callers = (("owner", a, None), ("edit recipient", c, None), ("read recipient", b, None),
               ("edit recipient, x-act-as owner", c, a.acc))
    svc.execute("VACUUM curation_clock")
    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL track_functions = 'all'")
            cur.execute("SELECT account_id, family FROM curation_clock WHERE account_id = ANY(%s)",
                        ([a.acc, b.acc, c.acc],))
            held: dict[uuid.UUID, set[str]] = {}
            for acc, fam in cur.fetchall():
                held.setdefault(acc, set()).add(fam)
            for size in ("lane", "2,000 accounts"):
                if size != "lane":
                    _two_thousand_accounts(cur)
                for who, caller, act_as in callers:
                    with scoped(caller.user, act_as=act_as, cur=cur):
                        if act_as is None and who != "read recipient":
                            seen[(size, who)] = _warm_read(cur)
                        read[(size, who)] = _rows_on_this_backend(cur)
    finally:
        conn.rollback()
        conn.close()
    assert len(seen) == 4 and len(read) == 8, (seen, read)

    def own(t: _Tenant) -> set[tuple[uuid.UUID, str, bool]]:
        return {(t.acc, f, True) for f in held.get(t.acc, set())}

    expected = {
        "owner": (own(a), {(a.acc, True, True)}),
        "edit recipient": (own(c) | {(a.acc, f, False) for f in held[a.acc] if f in EDIT_FAMILIES},
                           {(c.acc, True, True), (a.acc, False, True)}),
        "read recipient": (own(b) | {(a.acc, "board", False)}, {(b.acc, True, True), (a.acc, False, False)}),
        "edit recipient, x-act-as owner": (own(c), {(c.acc, True, True)}),
    }
    assert held[a.acc] == set(FAMILIES), held
    for (size, who), got in read.items():
        assert got == expected[who], (size, who, got)
    for key, (blocks, calls) in seen.items():
        assert blocks <= 40, f"{key}: a warm read read {blocks} shared buffers"
        assert calls == {"curation_clock_audience": 2, "current_account_ids": 2, "acting": 2}, (key, calls)


def test_the_warm_read_rail_bites_on_708s_audience_and_on_a_read_that_scans_a_full_clock(svc: psycopg.Connection,
                                                                                         world: _World) -> None:
    """The rail above, shown to fail on what it guards. 696's SQL invoker audience put back (708's read: 708 changed
    the policy, not the function) calls current_account_ids() more than once per evaluation. With index and bitmap
    scans off over 2,000 accounts × 7 families (the shape of § 3.8's 2,010-account copy), the read scans the clock
    and breaks the 40-buffer ceiling while its calls stay two. Its 14,000 rolled-back rows are vacuumed after."""
    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL track_functions = 'all'")
            cur.execute("SAVEPOINT bite")
            cur.execute(_audience_of("696_curation_clock.sql"))
            with scoped(world.c.user, cur=cur):
                _blocks, calls = _warm_read(cur)
            assert calls["curation_clock_audience"] == 2 and calls["current_account_ids"] > 2, calls
            cur.execute("ROLLBACK TO SAVEPOINT bite")
            _two_thousand_accounts(cur, FAMILIES)
            cur.execute("SET LOCAL enable_indexscan = off")
            cur.execute("SET LOCAL enable_bitmapscan = off")
            with scoped(world.c.user, cur=cur):
                blocks, calls = _warm_read(cur)
    finally:
        conn.rollback()
        conn.close()
        svc.execute("VACUUM curation_clock")
    assert blocks > 40, f"a read that scans 14,000 clock rows read only {blocks} shared buffers"
    assert calls == {"curation_clock_audience": 2, "current_account_ids": 2, "acting": 2}, calls


def test_709s_post_refuses_an_invoker_audience_one_following_while_acting_and_a_policy_bound_owner() -> None:
    """709's $pre$ and $post$ pass on the lane's schema. $post$ refuses the audience back as invoker and a definer body
    that no longer hides share rows while acting (pipeline_shares' policy did that for the invoker), neutralises that
    filter or follows the caller's own accounts as an owner (its WHERE is held whole); both refuse an owner that
    neither owns pipeline_shares nor bypasses row security, under whom a recipient would follow nobody."""
    fn = _audience_of(_AUDIENCE_709)
    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            cur.execute(_post(_AUDIENCE_709, "pre"))
            cur.execute(_post(_AUDIENCE_709))
            for broken, match in (
                (fn.replace("stable security definer", "stable"), "is plpgsql, stable, definer"),
                (fn.replace("\n     and not public.acting()", ""), "hides share rows while acting"),
                (fn.replace("and not public.acting()", "and (not public.acting() or true)"), "while acting"),
                (fn.replace("\n     and s.account_id not in (select me.id from me)", ""), "never their own accounts"),
            ):
                assert broken != fn, match
                cur.execute("SAVEPOINT bite")
                cur.execute(broken)
                with pytest.raises(psycopg.errors.RaiseException, match=match):
                    cur.execute(_post(_AUDIENCE_709))
                cur.execute("ROLLBACK TO SAVEPOINT bite")
            cur.execute("ALTER FUNCTION public.curation_clock_audience() OWNER TO anon")
            for tag in ("pre", "post"):
                cur.execute("SAVEPOINT bite")
                with pytest.raises(psycopg.errors.RaiseException, match="held by pipeline_shares"):
                    cur.execute(_post(_AUDIENCE_709, tag))
                cur.execute("ROLLBACK TO SAVEPOINT bite")
    finally:
        conn.rollback()
        conn.close()


# --- 12-13. the health check's reads and the catalog -------------------------------------------------------------

def test_the_board_bumps_read_finds_every_card_change_bumped_and_bites_on_an_unbumped_event(
    svc: psycopg.Connection, world: _World,
) -> None:
    a = world.a.acc
    with scoped(world.a.user, method="POST") as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO property_pipeline (account_id, property_id, stage_id) VALUES (%s, %s, %s)",
                        (a, world.props[10], world.entry))
            cur.execute("INSERT INTO property_pipeline_events (account_id, property_id, to_stage_id, reason) "
                        "VALUES (%s, %s, %s, 'operator')", (a, world.props[10], world.entry))
    out = plat.check_curation_sync(svc, THRESHOLDS)
    cells = {c["subject"]: c for c in out["details"]["cells"]}
    assert (out["status"], cells["triggers"]["value"], cells["board_bumps"]["value"]) == ("ok", 14, 0), out
    assert cells["board_bumps"]["facts"]["events_24h"] >= 1
    conn = psycopg.connect(db_url())
    try:
        with conn.cursor() as cur:
            # An event with no card change in its transaction: nothing bumps board after it.
            cur.execute("INSERT INTO property_pipeline_events (account_id, property_id, to_stage_id, reason) "
                        "VALUES (%s, %s, %s, 'operator')", (a, world.props[11], world.entry))
            cur.execute(plat._CURATION_BOARD_BUMPS_SQL)
            missed, accounts, _first, _events = cur.fetchone()
    finally:
        conn.rollback()
        conn.close()
    assert (missed, accounts) == (1, 1)


def test_the_catalog_holds_the_clock_as_built(svc: psycopg.Connection) -> None:
    with svc.cursor() as cur:
        cur.execute("SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE t.tgfoid = 'public.bump_curation_clock()'::regprocedure AND NOT t.tgisinternal "
                    "AND t.tgconstraint <> 0 AND t.tgdeferrable AND t.tginitdeferred AND t.tgenabled = 'O'")
        triggers = cur.fetchall()
        cur.execute("SELECT p.proname, p.prosecdef, p.proconfig FROM pg_proc p WHERE p.pronamespace = 'public'::"
                    "regnamespace AND p.proname IN ('bump_curation_clock', 'curation_clock_audience')")
        procs = {name: (definer, config) for name, definer, config in cur.fetchall()}
        cur.execute("SELECT l.lanname, p.prorows FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang "
                    "WHERE p.oid = 'public.curation_clock_audience()'::regprocedure")
        audience = cur.fetchone()
        # The live sweep's candidate test (tests/test_tenant_isolation_live.py), for the one object its UNGATED entry
        # waives there by name: the waiver is incidental, so a body drifting into admin-only data still fails here.
        cur.execute("SELECT t FROM unnest(%s::text[]) t, pg_proc p "
                    "WHERE p.oid = 'public.curation_clock_audience()'::regprocedure "
                    "AND p.prosrc ~ ('\\m' || t || '\\M')", (list(ADMIN_ONLY_RELATIONS),))
        admin_reads = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT rel, r, p, has_table_privilege(r, 'public.' || rel, p) "
                    "FROM unnest(array['curation_clock', 'curation_clock_public']) rel, "
                    "unnest(array['anon', 'authenticated', 'service_role']) r, "
                    "unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p")
        held = {(rel, r, p) for rel, r, p, has in cur.fetchall() if has}
        cur.execute("SELECT reloptions FROM pg_class WHERE oid = 'public.curation_clock_public'::regclass")
        options = cur.fetchone()[0]
        cur.execute("SELECT a.id, array_agg(c.family ORDER BY c.family) FROM accounts a "
                    "LEFT JOIN curation_clock c ON c.account_id = a.id "
                    "WHERE a.id = '00000000-0000-0000-0000-000000000000' OR a.id IN ("
                    "  SELECT m.account_id FROM account_members m JOIN auth.users u ON u.id = m.user_id "
                    "   WHERE u.email = 'ci-operator@replay.local') GROUP BY a.id")
        seeded = cur.fetchall()
    assert {(rel, name) for rel, name, _ in triggers} == {(t, f"{t}_curation_clock") for t in CLOCKED_TABLES}
    assert {rel: d.split("bump_curation_clock('")[1].split("'")[0] for rel, _n, d in triggers} == CLOCKED_TABLES
    assert procs["bump_curation_clock"] == (True, ['search_path=""']), "the bump is definer on a fixed path"
    assert procs["curation_clock_audience"] == (True, ['search_path=""']), "the audience is definer on a fixed path"
    assert audience == ("plpgsql", 4), "the audience is plpgsql, planned once per backend, ROWS 4 (709)"
    assert admin_reads == [], f"the definer audience reads admin-only data: {admin_reads}"
    assert {(rel, r, p) for rel, r, p in held if p != "SELECT"} == set(), (
        "nobody but bump_curation_clock() writes the clock or its view")
    assert {(rel, r) for rel, r, p in held if p == "SELECT"} == {
        ("curation_clock", "authenticated"), ("curation_clock", "service_role"),
        ("curation_clock_public", "authenticated"),
    }, "the browser reads through its policy, the service role reads the table (701), anon reads nothing"
    assert "security_invoker=true" in options
    assert seeded and all(families == sorted(SEEDED_FAMILIES) for _acc, families in seeded), (
        f"every account that predates the clock was seeded with 696's seven families (715's is unseeded): {seeded}")
