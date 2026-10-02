"""The carrier list, `toolkit.property_carriers` (rules 15, 18, 22), offline: the FK census over
`migrations/*.sql` (every column that references `properties` is carried or named in
`NOT_CARRIED`), the order the list must keep, the protocol every carrier meets, and a strict
cursor that runs each REAL carrier and fails on any statement it did not declare or any param it
did not supply. What each carrier does to rows is executed in tests/test_property_carriers_live.py
(with the live column census); the writers' use of the seam is tests/test_property_merge_set.py
and tests/test_detach_listing.py."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests import sql_corpus
from toolkit import pipeline_identity as pi
from toolkit import property_carriers as carriers
from toolkit.property_carriers import (
    CARRIER_SQL,
    NOT_CARRIED,
    PROPERTY_CARRIERS,
    Carrier,
    DetachStep,
    Hop,
    MergeStep,
    carried_columns,
)

_MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
G, G2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
_HOW_TO_FIX = (
    "carry it (a SET/APPEND table keyed on property_id = one `CurationTable(...)` line before "
    "`Pipeline()`; any other shape = one class meeting `Carrier`), or name it in `NOT_CARRIED` "
    "with the reason a merge leaves it — toolkit/property_carriers.py")


def _n(sql: str) -> str:
    return " ".join(sql.split())


# --- the census ---------------------------------------------------------------------------

_REF = r"references\s+(?:public\.)?properties\b(?:\s*\(\s*id\s*\))?"
_REFERS = re.compile(_REF, re.I)
_CREATE = re.compile(
    r"create\s+(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?([\w.]+)\s*\((.*)\)\s*$",
    re.S | re.I)
_ALTER = re.compile(r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?([\w.]+)\s+(.*)$",
                    re.S | re.I)
# A table constraint, in CREATE TABLE or after ALTER ... ADD: one column only (a composite key
# is not read, so it fails the census below rather than passing it).
_TABLE_FK = re.compile(r"(?:constraint\s+\w+\s+)?foreign\s+key\s*\(\s*(\w+)\s*\)\s*" + _REF,
                       re.I)
_COLUMN_FK = re.compile(
    r"(?!(?:constraint|foreign|primary|unique|check|exclude|like)\b)(\w+)\s+.*?" + _REF,
    re.I | re.S)
_ADD = re.compile(r"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?(.*)$", re.I | re.S)
_DROP_COLUMN = re.compile(r"drop\s+(?:column\s+)?(?:if\s+exists\s+)?(?!constraint\b)(\w+)",
                          re.I)
_DROP_TABLE = re.compile(
    r"drop\s+table\s+(?:if\s+exists\s+)?([\w.,\s]+?)(?:\s+cascade|\s+restrict)?\s*$", re.I | re.S)


def _table(name: str) -> str:
    return name.lower().removeprefix("public.")


def _elements(body: str) -> list[str]:
    """A CREATE TABLE body or an ALTER action list, split at its top-level commas (a type,
    default or key list holds its own)."""
    out, depth, quoted, start = [], 0, False, 0
    for i, ch in enumerate(body):
        if ch == "'":
            quoted = not quoted
        elif quoted:
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(body[start:i].strip())
            start = i + 1
    out.append(body[start:].strip())
    return out


def _fk_column(element: str) -> str | None:
    """The column a column definition or a table constraint points at `properties`, if any."""
    if m := _TABLE_FK.match(element) or _COLUMN_FK.match(element):
        return m.group(1).lower()
    return None


def foreign_keys_to_properties(root: Path = _MIGRATIONS) -> dict[tuple[str, str], str]:
    """(table, column) -> the migration that added it, for every column still referencing
    `properties` after replaying `migrations/*.sql` in order (510 skipped, as the replay does:
    migrations.yml). A reference it cannot attribute to one column raises, naming the
    migration, so a shape the parser does not model fails the census instead of passing it."""
    fks: dict[tuple[str, str], str] = {}
    unread: list[str] = []
    for path in sorted(root.glob("*.sql")):
        if path.name.startswith("510_"):
            continue
        for statement in re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8")).split(";"):
            s = statement.strip()
            if m := _CREATE.match(s):
                table, elements = _table(m.group(1)), _elements(m.group(2))
            elif m := _ALTER.match(s):
                table, elements = _table(m.group(1)), []
                for action in _elements(m.group(2)):
                    if d := _DROP_COLUMN.match(action):
                        fks.pop((table, d.group(1).lower()), None)
                    elif a := _ADD.match(action):
                        elements.append(a.group(1))
            elif m := _DROP_TABLE.match(s):
                for dropped in (_table(t.strip()) for t in m.group(1).split(",")):
                    for key in [k for k in fks if k[0] == dropped]:
                        del fks[key]
                continue
            else:
                elements, table = [], ""
            found = [c for c in map(_fk_column, elements) if c]
            for col in found:
                fks[(table, col)] = path.name
            if len(_REFERS.findall(s)) > len(found):
                unread.append(f"{path.name}: {' '.join(s.split())[:120]}")
    if unread:
        raise ValueError("the census cannot read these references to properties; teach "
                         "foreign_keys_to_properties the shape:\n  " + "\n  ".join(unread))
    return fks


def test_every_foreign_key_to_properties_is_carried_or_named():
    fks = foreign_keys_to_properties()
    assert ("listings", "property_id") in fks and ("property_tags", "property_id") in fks, (
        "the migration walk found nothing it must: the census parser is broken")
    unclassified = sorted(set(fks) - carried_columns() - NOT_CARRIED.keys())
    assert not unclassified, (
        "a column references properties(id) but no carrier keeps it true across a merge and "
        "NOT_CARRIED does not say why a merge leaves it:\n"
        + "\n".join(f"  {t}.{c} ({fks[(t, c)]})" for t, c in unclassified)
        + f"\nFix: {_HOW_TO_FIX}.")


def test_the_census_sees_a_new_reference_and_a_dropped_table(tmp_path):
    (tmp_path / "001_a.sql").write_text(
        "create table watch_list (\n  id bigint,\n  property_id bigint not null\n"
        "    references properties(id) on delete cascade -- why\n);\n"
        "create table gone (x bigint references public.properties (id));\n"
        "alter table listings add column if not exists twin_id bigint references properties(id);")
    (tmp_path / "002_b.sql").write_text("drop table if exists gone, other cascade;")
    assert foreign_keys_to_properties(tmp_path) == {("watch_list", "property_id"): "001_a.sql",
                                            ("listings", "twin_id"): "001_a.sql"}



@pytest.mark.parametrize("sql, column", [
    pytest.param("create table w (id bigint, property_id bigint references properties);",
                 "property_id", id="references-without-(id)"),
    pytest.param("alter table w add property_id bigint references public.properties(id);",
                 "property_id", id="alter-add-without-COLUMN"),
    pytest.param("alter table w add constraint w_fk foreign key (property_id) "
                 "references properties(id) on delete cascade;",
                 "property_id", id="alter-add-constraint-foreign-key"),
    pytest.param("alter table w add foreign key (twin_id) references properties;",
                 "twin_id", id="alter-add-unnamed-foreign-key"),
    pytest.param("create table w (id bigint, twin_id bigint,\n"
                 "  constraint w_fk foreign key (twin_id) references properties (id));",
                 "twin_id", id="create-table-constraint-one-line"),
    pytest.param("create table w (\n  id bigint,\n  twin_id bigint not null,\n"
                 "  constraint w_fk\n    foreign key (twin_id)\n    references properties(id)\n);",
                 "twin_id", id="create-table-constraint-multi-line"),
    pytest.param("create table w (id bigint, price numeric(12, 2), twin_id bigint,\n"
                 "  foreign key (twin_id) references properties);",
                 "twin_id", id="create-table-unnamed-foreign-key"),
])
def test_the_census_reads_every_foreign_key_shape(tmp_path, sql, column):
    (tmp_path / "001_a.sql").write_text(sql)
    assert foreign_keys_to_properties(tmp_path) == {("w", column): "001_a.sql"}


@pytest.mark.parametrize("sql", [
    pytest.param("alter table w add constraint w_fk foreign key (account_id, property_id) "
                 "references properties (account_id, id);", id="composite-key"),
    pytest.param("do $$ begin alter table w add column property_id bigint "
                 "references properties(id); end $$;", id="inside-a-do-block"),
])
def test_a_reference_the_census_cannot_read_fails_it(tmp_path, sql):
    (tmp_path / "001_a.sql").write_text(sql)
    with pytest.raises(ValueError, match="001_a.sql"):
        foreign_keys_to_properties(tmp_path)

# --- the list -----------------------------------------------------------------------------


def _names() -> list[str]:
    return [c.name for c in PROPERTY_CARRIERS]


def test_the_asset_link_runs_first():
    assert _names()[0] == "asset_link"


def test_dismissals_run_after_the_pipeline():
    """The dismissal lift reads the live card the pipeline carry just put on the survivor."""
    assert _names().index("pipeline") < _names().index("dismissals")


def test_every_carrier_meets_the_protocol():
    assert len(set(_names())) == len(_names()), "two carriers share a name"
    for c in PROPERTY_CARRIERS:
        assert isinstance(c, Carrier), c
        assert c.columns and c.sql, f"{c.name} declares no columns or no SQL"
    assert not carried_columns() & NOT_CARRIED.keys(), "a column both carried and not carried"
    for key, why in NOT_CARRIED.items():
        assert why.strip(), f"NOT_CARRIED[{key}] needs its reason"


def test_the_status_log_stays_with_its_own_property():
    """Migration 559: a survivor holding two properties' activity logs charts false gaps."""
    assert ("property_status_events", "property_id") in NOT_CARRIED
    assert not any(t == "property_status_events" for t, _c in carried_columns())


def test_no_carrier_deletes_history():
    """The one sanctioned delete is a SET table's collision collapse (and the pipeline's
    current-state card, snapshotted to its ledger first); never a ledger or log row."""
    history = ("property_dismissals", "property_pipeline_events", "asset_membership_events",
               "property_merge_events", "property_status_events", "properties")
    for statement in CARRIER_SQL:
        deleted = re.findall(r"\bDELETE\s+FROM\s+(\w+)", statement, re.I)
        assert not set(deleted) & set(history), f"a carrier deletes history: {_n(statement)[:120]}"


# --- every real carrier, through a cursor that refuses what it did not declare -------------

_NAMED = re.compile(r"%\((\w+)\)s")


class _StrictCur:
    def __init__(self, carrier: Carrier, fetch: tuple | None = None) -> None:
        self.carrier, self.fetch = carrier, fetch
        self.declared = {_n(s) for s in carrier.sql}
        self.ran: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        s = _n(sql)
        assert s in self.declared, f"{self.carrier.name} ran SQL it does not declare: {s[:140]}"
        named = set(_NAMED.findall(s))
        if named:
            assert isinstance(params, dict) and named <= params.keys(), (
                f"{self.carrier.name}: {sorted(named - set(params or {}))} not supplied")
        else:
            assert s.count("%s") == len(params or ()), f"{self.carrier.name}: param count"
        self.ran.append((s, params))

    def fetchone(self) -> tuple | None:
        return self.fetch

    def fetchall(self) -> list[tuple]:
        return []


def _walk(carrier: Carrier, *, fetch: tuple | None = (41,), source: str = "operator",
          undo: tuple[Hop, ...] = (Hop(1, G, 10, 20),), left: int = 10) -> _StrictCur:
    cur = _StrictCur(carrier, fetch)
    carrier.on_merge(cur, MergeStep(10, 20, G, source))  # type: ignore[arg-type]
    carrier.on_detach(cur, DetachStep(20, left, undo, source))  # type: ignore[arg-type]
    return cur


@pytest.mark.parametrize("carrier", PROPERTY_CARRIERS, ids=lambda c: c.name)
def test_every_carrier_runs_only_its_declared_sql_with_its_params_supplied(carrier):
    # One fetched row serves both readers: the asset link's retired asset (41), and the
    # pipeline's last retiring merge, whose survivor (10) is the property left: one hop.
    cur = _walk(carrier, fetch=(41, 10))
    assert {s for s, _p in cur.ran} == {_n(s) for s in carrier.sql}, (
        f"{carrier.name} declares SQL it never runs")


def test_the_asset_link_reads_the_retired_link_and_logs_the_engine_as_auto():
    linked = _walk(carriers.AssetLink(), source="autodedup")
    carry, restore = linked.ran[1][1], linked.ran[2][1]
    assert carry == {"survivor": 10, "retired": 20, "asset": 41, "reason": f"merge {G}",
                     "source": "auto"}
    assert restore == {"restored": 20, "merge": f"merge {G}", "merges": [f"merge {G}"],
                       "path": [10], "detach": f"detach {G}", "source": "auto"}
    unlinked = _walk(carriers.AssetLink(), fetch=(None,), source="autodedup")
    assert [s for s, _p in unlinked.ran][:1] == [_n(carriers.AssetLink.RETIRED_ASSET_SQL)]
    assert len(unlinked.ran) == 2, "no link on the retired property: nothing to carry"
    assert _walk(carriers.AssetLink()).ran[1][1]["source"] == "operator"


def test_the_dispatch_collapse_hands_its_sends_to_the_kept_twin_first():
    """A send on a collapsed row would be nulled (ON DELETE SET NULL), which channel_sends_check
    refuses: the resend runs before the DELETE and pairs each retired row with exactly the twin
    the DELETE collapses it onto (the same keys, NULL-safe), so the two cannot drift apart."""
    dispatches = next(c for c in PROPERTY_CARRIERS if c.name == "notification_dispatches")
    assert isinstance(dispatches, carriers.Dispatches)
    lock, resend, collapse, move = (_n(s) for s in dispatches.sql)
    assert [s for s, _p in _walk(dispatches).ran] == [
        _n(carriers.Dispatches.LOCK_SQL), _n(carriers.Dispatches.RESEND_SQL), collapse, move]
    assert collapse.startswith("DELETE FROM notification_dispatches")
    assert move.startswith("UPDATE notification_dispatches SET property_id")
    twin = re.compile(r"s\.(\w+) IS NOT DISTINCT FROM r\.\1\b")
    assert twin.findall(resend) == twin.findall(collapse) == list(dispatches.keys)
    assert "s.property_id = %(survivor)s" in resend and "r.property_id = %(retired)s" in resend
    assert {"subscription_id", "collection_id"} <= set(dispatches.keys), (
        "the pairing is account-partitioned only through account-owned keys")


def test_the_retired_dispatches_are_locked_in_their_own_statement_before_the_resend():
    """An outbox claim's foreign key check holds FOR KEY SHARE on the dispatch; FOR UPDATE waits
    it out and holds off the next one. A separate statement, not a CTE of the resend, so the
    resend's fresh READ COMMITTED snapshot sees a send that claim committed."""
    lock = _n(carriers.Dispatches.LOCK_SQL)
    assert lock.startswith("SELECT ") and lock.endswith(" FOR UPDATE")
    assert "FROM notification_dispatches WHERE property_id = %(retired)s" in lock
    assert "FOR UPDATE" not in _n(carriers.Dispatches.RESEND_SQL)


def test_curation_dispatches_and_dismissals_give_nothing_back_on_a_detach():
    """Rule 18 best-effort: these rows stay on the property the advert left."""
    for carrier in PROPERTY_CARRIERS:
        if carrier.name in ("asset_link", "pipeline"):
            continue
        cur = _StrictCur(carrier)
        carrier.on_detach(cur, DetachStep(20, 10, (Hop(1, G, 10, 20),), "operator"))
        assert cur.ran == [], carrier.name


_PIPELINE = next(c for c in PROPERTY_CARRIERS if c.name == "pipeline")


def test_the_pipeline_merge_snapshots_card_ids_and_moves_the_winning_row():
    """Both sides' cards are snapshotted with their card_id; where the retired card wins, its ROW
    moves onto the survivor (card_id, stage and dates its own) and the survivor's is parked."""
    cur = _StrictCur(_PIPELINE)
    _PIPELINE.on_merge(cur, MergeStep(10, 20, G, "operator"))
    assert [s for s, _p in cur.ran] == [_n(s) for s in (
        pi._SNAPSHOT_SQL, pi._MOVE_IF_EMPTY_SQL, pi._RETIRED_WINS_SQL, pi._DROP_RETIRED_SQL)]
    assert [p for _s, p in cur.ran] == [{"r": 20, "s": 10, "g": G}] * 4
    snapshot, wins = _n(pi._SNAPSHOT_SQL), _n(pi._RETIRED_WINS_SQL)
    assert "note_snapshot, card_id)" in snapshot and "note, card_id FROM" in snapshot
    assert wins.startswith("WITH beaten AS ( DELETE FROM property_pipeline s")
    assert "SET property_id = %(s)s" in wins and "stage_id =" not in wins.split("UPDATE")[1], (
        "the winning row moves; nothing is copied onto the survivor's row")
    assert "note =" not in wins.split("UPDATE")[1], "the winning deal keeps its own note"


_KEYED = (pi._RETIRED_BY_SQL, pi._LOCK_OWN_SQL, pi._SEND_HOME_SQL)
_TAIL = (pi._RESTORE_SQL, pi._LOCK_STRAYS_SQL, pi._STRAYS_HOME_SQL, pi._REFILL_SQL,
         pi._LIFT_DISMISSALS_SQL)


def test_a_one_hop_detach_sends_home_by_card_id_and_falls_back_for_a_pre_582_merge():
    detach = _walk(_PIPELINE, fetch=(G, 10)).ran[4:]
    assert [s for s, _p in detach] == [_n(s) for s in (
        *_KEYED, pi._MOVE_ABSORBED_SQL, *_TAIL)]
    params = {"g": G, "r": 20, "s": 10}
    assert detach[0][1] == {"r": 20}
    assert [p for _s, p in detach[1:6]] == [params] * 5
    assert detach[6][1] == {**params, "cards": []}
    assert detach[7][1] == {**params, "left_accounts": [], "left_ids": []}
    assert detach[8][1] == {"ids": [20]}


def test_a_chained_detach_moves_nothing_by_inference():
    """Off a later survivor the pre-582 one-hop move never runs. With no ledger row the keyed
    path falls back to `undo[0]`; which later send-home outranks it is read off the ledger's
    snapshot order, never off the advert's path."""
    cur = _StrictCur(_PIPELINE)
    _PIPELINE.on_detach(cur, DetachStep(20, 5, (Hop(1, G, 10, 20), Hop(2, G2, 5, 10)),
                                        "operator"))
    assert [s for s, _p in cur.ran] == [_n(s) for s in (*_KEYED, *_TAIL)]
    assert cur.ran[2][1] == {"g": G, "r": 20, "s": 5}


def test_the_deal_comes_home_from_the_merge_that_last_retired_the_property():
    """An advert whose only live ledger row is an older merge (G) the property has since come
    back from reactivates it through G; its deal is the one it held at the merge that LAST
    retired it (G2, read off the ledger), and every keyed statement and log names G2."""
    cur = _StrictCur(_PIPELINE, fetch=(G2, 5))
    _PIPELINE.on_detach(cur, DetachStep(20, 5, (Hop(1, G, 5, 20),), "operator"))
    assert cur.ran[0] == (_n(pi._RETIRED_BY_SQL), {"r": 20})
    assert {p["g"] for _s, p in cur.ran[1:] if "g" in p} == {G2}
    by = _n(pi._RETIRED_BY_SQL)
    assert "WHERE retired_property_id = %(r)s ORDER BY id DESC LIMIT 1" in by


def test_the_pre_582_fallback_reads_only_unkeyed_snapshots_and_the_keyed_path_only_keyed():
    for fallback in (pi._RESTORE_SQL, pi._MOVE_ABSORBED_SQL):
        assert "e.card_id IS NULL" in _n(fallback)
    for keyed in (pi._SEND_HOME_SQL, pi._REFILL_SQL):
        assert "card_id IS NOT NULL" in _n(keyed)


def test_the_pre_582_fallback_moves_the_absorbed_card_back_and_never_deletes_one():
    """By inference, so never a DELETE, and a card the ledger names as another deal since 582
    (an operator add, or a snapshot on another property) stays where it is."""
    move = _n(pi._MOVE_ABSORBED_SQL)
    assert "DELETE" not in move.upper()
    assert "UPDATE property_pipeline pp SET property_id = %(r)s::bigint" in move
    assert "(a.reason = 'operator' AND a.from_stage_id IS NULL)" in move
    assert "(a.reason = 'merge_absorb' AND a.property_id <> %(s)s::bigint)" in move


def test_a_stray_is_a_live_card_born_in_the_lineage_and_follows_it_or_parks():
    """A card belongs to the family of the property it was born on (its first logged event): one
    born in R's lineage that sits off R follows R home, onto R when R is empty for its account,
    else parked; each leave is logged, so its own property's detach can re-insert it."""
    strays, home = _n(pi._LOCK_STRAYS_SQL), _n(pi._STRAYS_HOME_SQL)
    assert "JOIN property_pipeline_events born ON born.property_id = fam.h" in strays
    assert "o.id < born.id" in strays, "the card's FIRST logged event names where it was born"
    assert "(born.reason = 'operator' AND born.from_stage_id IS NULL) OR NOT EXISTS" in strays, (
        "an add is a birth; a first snapshot is one only where no pre-582 merge fed that property")
    assert strays.endswith("FOR UPDATE OF pp")
    assert "ORDER BY s.account_id, s.is_terminal, s.position DESC" in home
    assert "parked AS ( DELETE FROM property_pipeline pp" in home
    assert "card_id FROM parked" in home, "a park logs its leave"


def test_a_put_back_never_lands_outside_the_family_the_deal_was_born_in():
    refill = _n(pi._REFILL_SQL)
    assert "b.property_id NOT IN (SELECT f2.h FROM fam f2 WHERE f2.t = f.t)" in refill
    assert "o.id < b.id" in refill, "a birth is the card's FIRST logged event"


def test_every_card_id_lookup_names_its_account():
    """card_id is unique across accounts, yet the carrier runs as service role: every pairing on
    it also pairs the account (tenancy shape 3)."""
    pair = re.compile(r"\b(\w+)\.card_id = (\w+)\.card_id\b")
    for statement in (pi._LOCK_OWN_SQL, pi._SEND_HOME_SQL, pi._MOVE_ABSORBED_SQL,
                      pi._LOCK_STRAYS_SQL, pi._STRAYS_HOME_SQL, pi._REFILL_SQL):
        s = _n(statement)
        for a, b in pair.findall(s):
            assert f"{a}.account_id = {b}.account_id" in s, (a, b, s[:120])


def test_a_removed_deal_comes_back_at_the_stage_it_ended_at():
    assert "COALESCE(l.to_stage_id, l.from_stage_id)" in _n(pi._SEND_HOME_SQL)


def test_a_put_back_lifts_a_dismissal_only_under_a_live_card():
    lift = _n(pi._LIFT_DISMISSALS_SQL)
    assert "lift_reason = 'pipeline'" in lift and "NOT ps.is_terminal" in lift
    assert "pp.account_id = d.account_id" in lift
    assert "split_carry" in _n(pi._REFILL_SQL) and "unmerge_restore" in _n(pi._SEND_HOME_SQL)


def test_every_carrier_statement_is_in_the_prepare_corpus():
    """The PREPARE sweep (migrations.yml) reaches the f-string statements through CARRIER_SQL."""
    corpus = {_n(i.sql) for i in sql_corpus.discover(resolve_imports=True)}
    missing = [_n(s)[:100] for s in CARRIER_SQL if _n(s) not in corpus]
    assert not missing, missing
