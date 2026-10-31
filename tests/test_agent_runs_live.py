"""The run ledger and its executor on the replayed schema (#2070): submit, work once and cancel with a scripted model,
real row-level security and the real identity functions. Runs in CI's migrations job (`TEST_DATABASE_URL`)."""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from api import tenant_pool
from api.agents import claims, executor, loop, terminator
from api.agents.definition import load
from api.capabilities import registry
from api.capabilities.entry import Context, Effect, Entry, Tenancy
from api.filter_args import Args
from api.providers import Completion, ModelPrice
from tests._live_property import REQUIRED_DB, db_url, signups
from tests._tenant_registry import TENANT_TABLES
from tests._tenant_scope import scoped
from tests.api._agent_fixtures import frontmatter, reply, write_folder
from tests.api._fakes import _ScriptedProvider

pytestmark = REQUIRED_DB

AGENT = "probe_scout_v1"
MODEL = "claude-sonnet-4-5"
CHEAP = ModelPrice(1.0, 1.0)
OWN_CONTEXT = "get_my_property_context"
SUBMIT = terminator.NAME
ANSWER = reply((SUBMIT, {"markdown": "Done."}))
SETTINGS = ("agent_executor_enabled", "agent_daily_ceiling_usd", "agent_account_running_cap",
            "agent_account_in_flight_cap", "agent_run_allowlist")


def _connect() -> psycopg.Connection:
    return psycopg.connect(db_url(), autocommit=True, prepare_threshold=None)


@pytest.fixture(scope="module")
def svc() -> Iterator[psycopg.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def people(svc: psycopg.Connection) -> Iterator[dict[str, Any]]:
    """Three confirmed signups, each born with its account, board and monitoring collection."""
    with signups(svc, "runs", "a", "b", "c") as made:
        svc.execute("UPDATE auth.users SET email_confirmed_at = now() WHERE id = ANY(%s)",
                    ([made["a"], made["b"], made["c"]],))
        yield made


def _set(svc: psycopg.Connection, key: str, value: Any) -> None:
    if value is None:
        svc.execute("DELETE FROM app_settings WHERE key = %s", (key,))
    else:
        svc.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s::jsonb) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


@pytest.fixture()
def world(svc: psycopg.Connection, people: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """The executor switched on for this test: an agent folder of its own, the three signups allowlisted, the ledger
    empty, spend reset; every setting and the queue put back on exit."""
    before = dict(svc.execute("SELECT key, value FROM app_settings WHERE key = ANY(%s)", (list(SETTINGS),)).fetchall())
    meta = frontmatter(AGENT, allowed_tools=[OWN_CONTEXT], limits={"max_iterations": 6, "max_cost_usd": 1.0,
                                                                  "wall_clock_timeout_s": 60})
    write_folder(tmp_path, meta=meta)
    monkeypatch.setattr(executor, "SKILLS_DIR", tmp_path)
    monkeypatch.setenv("TENANT_POOL_DB_URL", db_url())
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "c0mm1t")
    emails = [r[0] for r in svc.execute("SELECT email FROM auth.users WHERE id = ANY(%s)",
                                        ([people["a"], people["b"], people["c"]],)).fetchall()]
    for key, value in (("agent_executor_enabled", True), ("agent_daily_ceiling_usd", 1000),
                       ("agent_account_running_cap", 2), ("agent_account_in_flight_cap", 20),
                       ("agent_run_allowlist", emails)):
        _set(svc, key, value)
    svc.execute("DELETE FROM llm_calls WHERE called_for = 'agent_run'")
    try:
        yield {**people, "folder": tmp_path / AGENT, "emails": emails}
    finally:
        for key in SETTINGS:
            _set(svc, key, before.get(key))
        svc.execute("DELETE FROM agent_runs")
        svc.execute("DELETE FROM llm_calls WHERE called_for = 'agent_run'")


def _submit(svc: psycopg.Connection, world: dict[str, Any], key: str = "a", idempotency_key: str | None = None) -> int:
    return executor.submit(svc, principal_user_id=world[key], account_id=world[f"{key}_acc"], agent=AGENT,
                           value={"town": "Brno"}, idempotency_key=idempotency_key).run_id


def _run(svc: psycopg.Connection, run_id: int) -> dict[str, Any]:
    with svc.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
        return cur.fetchone()


def _steps(svc: psycopg.Connection, run_id: int) -> list[dict[str, Any]]:
    with svc.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM agent_run_steps WHERE run_id = %s ORDER BY attempt, n", (run_id,))
        return cur.fetchall()


def _owned_ids(svc: psycopg.Connection, table: str, account: uuid.UUID) -> set[int]:
    rows = svc.execute(f"SELECT id FROM {table} WHERE account_id = %s", (account,)).fetchall()  # noqa: S608 - fixed
    return {int(r[0]) for r in rows}


def _caller(market: psycopg.Connection, user: uuid.UUID, account: uuid.UUID) -> Any:
    return claims.caller_for(market, principal_user_id=user, account_id=account)


@contextlib.contextmanager
def _as_run(svc: psycopg.Connection, user: uuid.UUID, account: uuid.UUID, *, method: str = "GET",
            act_as: uuid.UUID | None = None) -> Iterator[psycopg.Connection]:
    with tenant_pool.tenant_transaction(dict(_caller(svc, user, account).claims), act_as=act_as, method=method) as conn:
        yield conn


class _Rollback(Exception):
    pass


def _ids(conn: psycopg.Connection, sql: str) -> set[Any]:
    return {r[0] for r in conn.execute(sql).fetchall()}


# --- the identity a run carries -------------------------------------------------------------------------------------

@pytest.fixture()
def tenant_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TENANT_POOL_DB_URL", db_url())


@pytest.mark.usefixtures("tenant_db")
def test_a_two_membership_users_run_sees_only_the_runs_account(svc: psycopg.Connection, people: dict[str, Any]) -> None:
    """The claim narrows the membership set to one account; without it the user sees both, exactly as before."""
    svc.execute("INSERT INTO account_members (account_id, user_id, role) VALUES (%s, %s, 'member')",
                (people["b_acc"], people["a"]))
    with scoped(people["a"]) as conn:
        assert _ids(conn, "SELECT current_account_ids()") == {people["a_acc"], people["b_acc"]}
        assert _ids(conn, "SELECT account_id FROM acting_account_ids()") == {people["a_acc"], people["b_acc"]}
    with _as_run(svc, people["a"], people["b_acc"]) as conn:
        assert _ids(conn, "SELECT current_account_ids()") == {people["b_acc"]}
        assert _ids(conn, "SELECT account_id FROM acting_account_ids()") == {people["b_acc"]}
        assert _ids(conn, "SELECT id FROM collections") == _owned_ids(svc, "collections", people["b_acc"])
        assert not _ids(conn, "SELECT id FROM collections") & _owned_ids(svc, "collections", people["a_acc"])


@pytest.mark.usefixtures("tenant_db")
def test_a_claim_naming_an_account_the_user_is_not_in_reads_nothing(svc: psycopg.Connection, people: dict[str, Any]) -> None:
    """A forged or stale account narrows to nothing: an intersection, never a grant. Every tenant table a browser
    role may read, filtered to the account the claim names, is empty; that account's owner sees its rows."""
    with scoped(people["b"]) as conn:
        for table in ("collections", "pipeline_stages"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0, table  # noqa: S608 - fixed
    readable = [t for t in TENANT_TABLES
                if svc.execute("SELECT has_table_privilege('authenticated', %s, 'SELECT')", (t,)).fetchone()[0]]
    assert "collections" in readable and "agent_runs" in readable
    with _as_run(svc, people["a"], people["b_acc"]) as conn:
        assert _ids(conn, "SELECT current_account_ids()") == set()
        assert _ids(conn, "SELECT account_id FROM acting_account_ids()") == set()
        assert conn.execute("SELECT is_platform_admin()").fetchone()[0] is False
        for table in readable:
            held = conn.execute(f"SELECT count(*) FROM {table} WHERE account_id = %s",  # noqa: S608 - fixed
                                (people["b_acc"],)).fetchone()[0]
            assert held == 0, table


@pytest.mark.usefixtures("tenant_db")
def test_the_claim_only_narrows_whatever_account_it_names(svc: psycopg.Connection, people: dict[str, Any]) -> None:
    """For every account a claim could name: exactly the user's memberships intersected with it, never more."""
    svc.execute("INSERT INTO account_members (account_id, user_id, role) VALUES (%s, %s, 'member')",
                (people["b_acc"], people["a"]))
    mine = {people["a_acc"], people["b_acc"]}
    for named in (people["a_acc"], people["b_acc"], people["c_acc"], uuid.uuid4()):
        with _as_run(svc, people["a"], named) as conn:
            assert _ids(conn, "SELECT current_account_ids()") == mine & {named}, named
            assert _ids(conn, "SELECT account_id FROM acting_account_ids()") == mine & {named}, named
            assert _ids(conn, "SELECT account_id FROM acting_account_ids(true)") == mine & {named}, named


def _claims_row(user: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {"sub": str(user), "role": "authenticated", **extra}


@pytest.mark.usefixtures("tenant_db")
def test_only_a_top_level_claim_narrows_so_a_users_own_metadata_cannot(svc: psycopg.Connection, people: dict[str, Any]) -> None:
    """user_metadata is the one part of a JWT a browser user can write (auth.updateUser): it is nested, and read by no
    identity function. Narrowing yourself would gain nothing, but it must not change what the claim means either."""
    forged = _claims_row(people["a"], user_metadata={"run_account_id": str(people["b_acc"])},
                         app_metadata={"run_account_id": str(people["b_acc"])})
    with tenant_pool.tenant_transaction(forged, act_as=None, method="GET") as conn:
        assert _ids(conn, "SELECT current_account_ids()") == {people["a_acc"]}


@pytest.mark.usefixtures("tenant_db")
def test_an_empty_claim_fails_loudly_rather_than_reading_as_absent(people: dict[str, Any]) -> None:
    claims_ = _claims_row(people["a"], run_account_id="")
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        with tenant_pool.tenant_transaction(claims_, act_as=None, method="GET") as conn:
            conn.execute("SELECT current_account_ids()").fetchall()
    with tenant_pool.tenant_transaction(claims_, act_as=None, method="GET") as conn:
        assert conn.execute("SELECT is_platform_admin()").fetchone()[0] is False


_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _replaced_body(file: str, start: str, end: str, renamed: str) -> str:
    """A function as the migration before 704 left it, taken from that file itself, under another schema."""
    sql = (_MIGRATIONS / file).read_text(encoding="utf-8")
    at = sql.index(start)
    return sql[at:sql.index(end, at) + len(end)].replace(start, renamed)


_OLD_FUNCTIONS = (
    ("286_accounts_foundation.sql", "create or replace function current_account_ids()", "$$;",
     "create function old_identity.current_account_ids()"),
    ("641_pipeline_shares_and_acting.sql", "create function acting_account_ids(", "$$;",
     "create function old_identity.acting_account_ids("),
    ("330_admin_gate_fallback_tighten.sql", "create or replace function public.is_platform_admin()", "$function$;",
     "create function old_identity.is_platform_admin()"),
)
_IDENTITY_READS = ("current_account_ids() AS a", "acting_account_ids() AS a", "acting_account_ids(true) AS a",
                   "is_platform_admin() AS a")


def test_without_the_claim_every_identity_answer_is_the_old_functions_answer(people: dict[str, Any]) -> None:
    """The functions as they stood before 704, from their own migration files, against 704's, over a grid of what
    PostgREST and the tenant pool can set: claims (a full Supabase JWT included), x-act-as, method, role. Answers and
    errors alike must match, so traffic carrying no run_account_id cannot tell the difference."""
    a, b, c = people["a"], people["b"], people["c"]
    a_acc, b_acc, c_acc = people["a_acc"], people["b_acc"], people["c_acc"]
    stranger = uuid.uuid4()
    supabase = {"aud": "authenticated", "exp": 2_000_000_000, "iat": 1, "iss": "https://x.supabase.co/auth/v1",
                "sub": str(a), "email": "a@test.local", "phone": "", "role": "authenticated", "aal": "aal1",
                "session_id": str(stranger), "is_anonymous": False, "amr": [{"method": "password", "timestamp": 1}],
                "app_metadata": {"provider": "email", "run_account_id": str(b_acc)},
                "user_metadata": {"run_account_id": str(b_acc)}}
    claims_grid: list[str | None] = [
        None, "", "{}", '{"sub": ""}', '{"role": "anon"}', "not json", json.dumps(supabase),
        *(json.dumps(_claims_row(u)) for u in (a, b, c, stranger)),
    ]
    headers_grid = ["", "{}", json.dumps({"x-act-as": ""}), *(json.dumps({"x-act-as": str(x)}) for x in (b_acc, c_acc, stranger))]

    conn = _connect()
    seen: set[str] = set()
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("CREATE SCHEMA old_identity")
            for file, start, end, renamed in _OLD_FUNCTIONS:
                conn.execute(_replaced_body(file, start, end, renamed))
            conn.execute("GRANT USAGE ON SCHEMA old_identity TO authenticated")
            conn.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA old_identity TO authenticated")
            conn.execute("INSERT INTO account_members (account_id, user_id, role) VALUES (%s, %s, 'member')", (b_acc, a))
            conn.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'edit')", (c_acc, a_acc))
            conn.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'read')", (b_acc, c_acc))
            conn.execute("INSERT INTO admins (user_id) VALUES (%s)", (b,))

            def answer(read: str, claims_: str | None, headers: str, method: str, as_role: bool) -> Any:
                try:
                    with conn.transaction(force_rollback=True):
                        if claims_ is not None:
                            conn.execute("SELECT set_config('request.jwt.claims', %s, true)", (claims_,))
                        conn.execute("SELECT set_config('request.headers', %s, true)", (headers,))
                        conn.execute("SELECT set_config('request.method', %s, true)", (method,))
                        if as_role:
                            conn.execute("SET LOCAL ROLE authenticated")
                        return sorted(str(r[0]) for r in conn.execute(f"SELECT {read}").fetchall())  # noqa: S608 - fixed
                except psycopg.Error as exc:
                    return type(exc).__name__

            for claims_ in claims_grid:
                for headers in headers_grid:
                    for method in ("GET", "POST"):
                        for as_role in (True, False):
                            for read in _IDENTITY_READS:
                                new = answer(read, claims_, headers, method, as_role)
                                old = answer(f"old_identity.{read}", claims_, headers, method, as_role)
                                assert new == old, (read, claims_, headers, method, as_role)
                                seen.add(json.dumps(new))
    finally:
        conn.close()
    assert '["True"]' in seen and '["False"]' in seen and "[]" in seen
    assert f'["{a_acc}", "{b_acc}"]' in seen or f'["{b_acc}", "{a_acc}"]' in seen, "a real membership answered"
    assert any(str(c_acc) in s for s in seen), "the edit share's arm answered"
    assert '"InvalidTextRepresentation"' in seen, "the grid reached the functions' error paths too"


@pytest.mark.usefixtures("tenant_db")
def test_acting_for_another_account_is_refused_even_with_an_edit_share(svc: psycopg.Connection,
                                                                      people: dict[str, Any]) -> None:
    svc.execute("INSERT INTO pipeline_shares (account_id, recipient_account_id, mode) VALUES (%s, %s, 'edit')",
                (people["b_acc"], people["a_acc"]))
    with scoped(people["a"], act_as=people["b_acc"]) as conn:
        assert _ids(conn, "SELECT account_id FROM acting_account_ids()") == {people["b_acc"]}, "the share is real"
    with _as_run(svc, people["a"], people["a_acc"], act_as=people["b_acc"]) as conn:
        assert _ids(conn, "SELECT account_id FROM acting_account_ids()") == set()
        assert not _ids(conn, "SELECT id FROM collections") & _owned_ids(svc, "collections", people["b_acc"])


@pytest.fixture()
def admin(svc: psycopg.Connection, people: dict[str, Any]) -> Iterator[uuid.UUID]:
    svc.execute("INSERT INTO admins (user_id) VALUES (%s)", (people["a"],))
    try:
        yield people["a"]
    finally:
        svc.execute("DELETE FROM admins WHERE user_id = %s", (people["a"],))


@pytest.fixture()
def account_less_note(svc: psycopg.Connection) -> Iterator[int]:
    """A note on an estimation run with no account: of the browser's writers, only the platform admin's RLS arm reaches
    it (migration 639 dropped manual_rental_estimates, the one table only an admin could insert into)."""
    run_id = svc.execute("INSERT INTO estimation_runs (source, mode, status, input_spec, account_id) "
                         "VALUES ('api', 'agent', 'success', '{}'::jsonb, NULL) RETURNING id").fetchone()[0]
    note = svc.execute("INSERT INTO estimation_feedback (estimation_run_id, feedback_text) VALUES (%s, 'note') "
                       "RETURNING id", (run_id,)).fetchone()[0]
    try:
        yield note
    finally:
        svc.execute("DELETE FROM estimation_runs WHERE id = %s", (run_id,))


_ADOPT = "UPDATE estimation_feedback SET feedback_text = 'edited', account_id = %s WHERE id = %s"


@pytest.mark.usefixtures("tenant_db")
def test_an_admin_principals_run_has_no_admin_power(svc: psycopg.Connection, people: dict[str, Any], admin: uuid.UUID,
                                                    account_less_note: int) -> None:
    """The same user, the same write and the same admin-gated read: their login may, their run may not."""
    plain = {"sub": str(admin), "role": "authenticated"}
    with pytest.raises(_Rollback), tenant_pool.tenant_transaction(plain, act_as=None, method="POST") as conn:
        assert conn.execute("SELECT is_platform_admin()").fetchone()[0] is True
        assert conn.execute(_ADOPT, (people["a_acc"], account_less_note)).rowcount == 1
        raise _Rollback
    with _as_run(svc, admin, people["a_acc"], method="POST") as conn:
        assert conn.execute("SELECT is_platform_admin()").fetchone()[0] is False
        assert conn.execute(_ADOPT, (people["a_acc"], account_less_note)).rowcount == 0
        assert _ids(conn, f"SELECT id FROM estimation_feedback WHERE id = {account_less_note}") == set()


def test_the_builder_refuses_a_missing_principal_or_account(svc: psycopg.Connection) -> None:
    some = uuid.uuid4()
    for principal, account in ((None, some), (some, None)):
        with pytest.raises(ValueError):
            claims.caller_for(svc, principal_user_id=principal, account_id=account)


# --- the ledger's own tenancy ---------------------------------------------------------------------------------------

def test_a_browser_role_reads_its_own_account_and_writes_nothing(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    assert _work_on(_Model(ANSWER)).state == "succeeded"
    with scoped(world["a"]) as conn:
        assert _ids(conn, "SELECT id FROM agent_runs") == {run_id}
        assert _ids(conn, "SELECT n FROM agent_run_steps") == {1, 2}
    with scoped(world["b"]) as conn:
        assert _ids(conn, "SELECT id FROM agent_runs") == set()
        assert _ids(conn, "SELECT n FROM agent_run_steps") == set()
    for statement in ("UPDATE agent_runs SET status = 'queued'", "DELETE FROM agent_runs",
                      "UPDATE agent_run_steps SET name = 'x'", "DELETE FROM agent_artifacts",
                      f"INSERT INTO agent_artifacts (account_id, run_id, kind, name, size_bytes, sha256, storage_key) "
                      f"VALUES ('{world['a_acc']}', {run_id}, 'md', 'x', 1, 'x', 'x')"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), scoped(world["a"], method="POST") as conn:
            conn.execute(statement)


def test_the_diary_is_append_only_yet_an_account_deletion_still_cascades(svc: psycopg.Connection) -> None:
    with signups(svc, "diary", "d") as made:
        svc.execute("UPDATE auth.users SET email_confirmed_at = now() WHERE id = %s", (made["d"],))
        run_id = svc.execute(
            "INSERT INTO agent_runs (account_id, principal_user_id, agent, definition_sha, tool_set_sha, commit_sha, "
            "provider, model, input) VALUES (%s, %s, 'x', 'x', 'x', 'x', 'x', 'x', '{}') RETURNING id",
            (made["d_acc"], made["d"])).fetchone()[0]
        svc.execute("INSERT INTO agent_run_steps (run_id, account_id, attempt, n, kind, name, input_sha, summary, "
                    "payload, duration_ms) VALUES (%s, %s, 1, 1, 'tool', 'x', 'x', 'x', '{}', 0)",
                    (run_id, made["d_acc"]))
        for statement in ("UPDATE agent_run_steps SET name = 'y' WHERE run_id = %s",
                          "DELETE FROM agent_run_steps WHERE run_id = %s"):
            with pytest.raises(psycopg.errors.RestrictViolation):
                svc.execute(statement, (run_id,))
    assert svc.execute("SELECT count(*) FROM agent_run_steps WHERE run_id = %s", (run_id,)).fetchone()[0] == 0


def _artifact(svc: psycopg.Connection, account: uuid.UUID, run_id: int, attempt: int | None, n: int | None) -> None:
    svc.execute("INSERT INTO agent_artifacts (account_id, run_id, step_attempt, step_n, kind, name, size_bytes, sha256, "
                "storage_key) VALUES (%s, %s, %s, %s, 'md', 'x', 1, 'x', 'x')", (account, run_id, attempt, n))


def test_an_artifact_names_its_step_by_attempt_and_goes_with_its_account(svc: psycopg.Connection) -> None:
    """A retry starts its steps over from 1, so a step number alone is two different steps."""
    with signups(svc, "artifact", "d") as made:
        run_id = svc.execute(
            "INSERT INTO agent_runs (account_id, principal_user_id, agent, definition_sha, tool_set_sha, commit_sha, "
            "provider, model, input) VALUES (%s, %s, 'x', 'x', 'x', 'x', 'x', 'x', '{}') RETURNING id",
            (made["d_acc"], made["d"])).fetchone()[0]
        for attempt in (1, 2):
            svc.execute("INSERT INTO agent_run_steps (run_id, account_id, attempt, n, kind, name, input_sha, summary, "
                        "payload, duration_ms) VALUES (%s, %s, %s, 1, 'tool', 'x', 'x', 'x', '{}', 0)",
                        (run_id, made["d_acc"], attempt))
        _artifact(svc, made["d_acc"], run_id, 2, 1)
        _artifact(svc, made["d_acc"], run_id, None, None)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _artifact(svc, made["d_acc"], run_id, 3, 1)
        with pytest.raises(psycopg.errors.CheckViolation):
            _artifact(svc, made["d_acc"], run_id, None, 1)
    assert svc.execute("SELECT count(*) FROM agent_artifacts WHERE run_id = %s", (run_id,)).fetchone()[0] == 0


def test_browser_roles_hold_no_write_grant_on_the_ledger(svc: psycopg.Connection) -> None:
    for table in ("agent_runs", "agent_run_steps", "agent_artifacts"):
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            has = svc.execute("SELECT has_table_privilege('authenticated', %s, %s)", (table, privilege)).fetchone()[0]
            assert has is False, (table, privilege)
        assert svc.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)).fetchone()[0]


# --- submit -------------------------------------------------------------------------------------------------------------

def test_a_non_allowlisted_accounts_submit_is_refused_and_writes_nothing(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    _set(svc, "agent_run_allowlist", [e for e in world["emails"] if "-a-" not in e])
    with pytest.raises(executor.SubmitRefused) as refused:
        _submit(svc, world, "a")
    assert refused.value.reason == "not_allowlisted"
    assert "not open to this account" in str(refused.value)
    assert svc.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == 0


def test_an_absent_allowlist_refuses_everyone(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    _set(svc, "agent_run_allowlist", None)
    with pytest.raises(executor.SubmitRefused):
        _submit(svc, world, "a")


def test_the_allowlist_reads_a_confirmed_email_case_blind_and_never_an_unconfirmed_one(svc: psycopg.Connection,
                                                                                      world: dict[str, Any]) -> None:
    _set(svc, "agent_run_allowlist", [e.upper() for e in world["emails"]])
    assert _submit(svc, world, "a")
    svc.execute("UPDATE auth.users SET email_confirmed_at = NULL WHERE id = %s", (world["b"],))
    with pytest.raises(executor.SubmitRefused):
        _submit(svc, world, "b")


def test_a_principal_cannot_submit_on_an_account_they_are_not_in(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    with pytest.raises(executor.SubmitRefused):
        executor.submit(svc, principal_user_id=world["a"], account_id=world["b_acc"], agent=AGENT, value={"town": "x"})


def test_a_duplicate_submit_returns_the_existing_run(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    first = executor.submit(svc, principal_user_id=world["a"], account_id=world["a_acc"], agent=AGENT,
                            value={"town": "Brno"}, idempotency_key="k")
    again = executor.submit(svc, principal_user_id=world["a"], account_id=world["a_acc"], agent=AGENT,
                            value={"town": "Praha"}, idempotency_key="k")
    assert (first.created, again.created) == (True, False)
    assert again.run_id == first.run_id
    assert _run(svc, first.run_id)["input"] == {"town": "Brno"}
    assert _submit(svc, world, "b", idempotency_key="k") != first.run_id, "the key is the account's own"


def test_concurrent_duplicates_make_one_run(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    made: list[executor.Submitted] = []

    def one() -> None:
        with _connect() as conn:
            made.append(executor.submit(conn, principal_user_id=world["a"], account_id=world["a_acc"], agent=AGENT,
                                        value={"town": "Brno"}, idempotency_key="race"))
    threads = [threading.Thread(target=one) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(made) == 6 and len({m.run_id for m in made}) == 1 and sum(m.created for m in made) == 1


def test_the_in_flight_cap_refuses_a_new_run_but_not_a_duplicate(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    _set(svc, "agent_account_in_flight_cap", 2)
    first = _submit(svc, world, "a", idempotency_key="k")
    _submit(svc, world, "a")
    with pytest.raises(executor.SubmitRefused) as refused:
        _submit(svc, world, "a")
    assert refused.value.reason == "in_flight_cap"
    assert _submit(svc, world, "a", idempotency_key="k") == first
    assert _submit(svc, world, "b"), "another account's room is its own"


def test_an_absent_in_flight_cap_refuses_in_plain_words(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    _set(svc, "agent_account_in_flight_cap", None)
    with pytest.raises(executor.SubmitRefused) as refused:
        _submit(svc, world, "a")
    assert refused.value.reason == "in_flight_cap"
    assert str(refused.value) == "Agent runs are not open right now."


@pytest.mark.parametrize("agent, value, error", [
    ("no_such_agent", {"town": "x"}, executor.DefinitionError),
    ("../" + AGENT, {"town": "x"}, executor.DefinitionError),
    (AGENT, {"town": 5}, executor.InputError),
])
def test_a_run_that_could_not_start_is_refused_at_submit(svc: psycopg.Connection, world: dict[str, Any], agent: str,
                                                         value: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        executor.submit(svc, principal_user_id=world["a"], account_id=world["a_acc"], agent=agent, value=value)


# --- work once: what a run records ------------------------------------------------------------------------------------

class _Model(_ScriptedProvider):
    """The scripted model, with something else happening while each call is in the air."""

    def __init__(self, *replies: Completion, during: Callable[[int], None] | None = None) -> None:
        super().__init__("anthropic", list(replies), prices={MODEL: CHEAP})
        self._during = during

    def complete(self, **kwargs: Any) -> Completion:
        if self._during:
            self._during(len(self.calls) + 1)
        return super().complete(**kwargs)


def _work_on(model: _Model, stop: threading.Event | None = None) -> executor.Worked:
    return executor.work_once(providers={"anthropic": model}, stop=stop or threading.Event(), connect=_connect)


def test_a_run_succeeds_and_records_what_it_ran_step_by_step(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    model = _Model(reply((OWN_CONTEXT, {}), text="Looking."), reply((SUBMIT, {"markdown": "# Done", "files": []})))
    assert _work_on(model) == executor.Worked("succeeded", run_id)

    row = _run(svc, run_id)
    definition = load(world["folder"])
    assert row["status"] == "succeeded" and row["attempts"] == 1 and row["stop_reason"] == "submit_result"
    assert row["result_markdown"] == "# Done" and row["proposals"] == [] and row["error"] is None
    assert row["lease_until"] is None and row["started_at"] and row["finished_at"]
    assert (row["agent"], row["definition_sha"], row["commit_sha"], row["provider"], row["model"]) == (
        AGENT, definition.sha, "c0mm1t", "anthropic", MODEL)
    assert row["tool_set_sha"] == executor.tool_set_sha(definition) and len(row["tool_set_sha"]) == 64

    steps = _steps(svc, run_id)
    assert [(s["attempt"], s["n"], s["kind"], s["name"]) for s in steps] == [
        (1, 1, "model", MODEL), (1, 2, "tool", OWN_CONTEXT), (1, 3, "model", MODEL), (1, 4, "tool", SUBMIT)]
    assert steps[1]["payload"]["input"] == {} and "collections" in steps[1]["payload"]["output"]["data"]
    assert len(steps[0]["summary"]) <= executor.SUMMARY_CHARS and len(steps[0]["input_sha"]) == 64

    calls = svc.execute("SELECT id, cost_usd FROM llm_calls WHERE agent_run_id = %s ORDER BY id", (run_id,)).fetchall()
    assert [c[0] for c in calls] == [s["llm_call_id"] for s in steps if s["kind"] == "model"]
    assert row["cost_usd"] == sum(c[1] for c in calls) > 0
    assert all(0 < c["timeout_s"] <= 60 for c in model.calls), "a model call may take only the time the run has left"


def test_a_tool_set_change_changes_the_hash(world: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = load(write_folder(tmp_path / "other", meta=frontmatter(AGENT, allowed_tools=["search_properties"])))
    assert executor.tool_set_sha(other) != executor.tool_set_sha(load(world["folder"]))


def test_a_refused_answer_is_journaled_and_the_run_goes_on(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    """The terminator's rule at this seam: a property no tool returned in this run is not cited."""
    run_id = _submit(svc, world, "a")
    model = _Model(reply((SUBMIT, {"markdown": "Find 7.", "cited_property_ids": [7]})), reply((SUBMIT, {"markdown": "None."})))
    assert _work_on(model).state == "succeeded"
    steps = _steps(svc, run_id)
    assert [s["name"] for s in steps] == [MODEL, SUBMIT, MODEL, SUBMIT]
    assert steps[1]["payload"]["output"]["error"]["code"] == "refused"
    assert _run(svc, run_id)["result_markdown"] == "None."


def test_a_run_without_an_accepted_answer_fails_with_its_reason(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    assert _work_on(_Model(reply(text="I give up."))).state == "failed"
    row = _run(svc, run_id)
    assert row["stop_reason"] == "end_turn" and "end_turn" in row["error"] and row["result_markdown"] is None


def test_a_provider_failure_fails_the_run_keeps_its_details_private_and_is_on_the_ledger(svc: psycopg.Connection,
                                                                                         world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")

    def boom(call: int) -> None:
        raise executor.ProviderError("anthropic call failed: HTTP 500 secret detail")
    assert _work_on(_Model(reply(), during=boom)).state == "failed"
    row = _run(svc, run_id)
    assert row["error"] == "the model call failed"
    logged = svc.execute("SELECT error FROM llm_calls WHERE agent_run_id = %s", (run_id,)).fetchall()
    assert ["secret detail" in r[0] for r in logged] == [True]


def test_one_overload_does_not_fail_the_run_and_the_failed_try_is_on_the_ledger(svc: psycopg.Connection, world: dict[str, Any],
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "RETRY_DELAYS_S", (0.01,))
    run_id = _submit(svc, world, "a")
    errors = [executor.ProviderError("anthropic call failed: overloaded", status=529)]

    def overloaded(call: int) -> None:
        if errors:
            raise errors.pop()
    assert _work_on(_Model(ANSWER, during=overloaded)) == executor.Worked("succeeded", run_id)
    logged = svc.execute("SELECT error IS NULL, cost_usd > 0 FROM llm_calls WHERE agent_run_id = %s ORDER BY id", (run_id,)).fetchall()
    assert logged == [(False, False), (True, True)]


def test_a_call_that_outlasts_the_wall_clock_is_the_runs_timeout(svc: psycopg.Connection, world: dict[str, Any], tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    _swap_agent(world, monkeypatch, tmp_path, [OWN_CONTEXT], wall_clock_timeout_s=1)
    run_id = _submit(svc, world, "a")

    def outlasts(call: int) -> None:
        time.sleep(1.05)
        raise executor.ProviderError("anthropic call failed: Request timed out.")
    assert _work_on(_Model(during=outlasts)) == executor.Worked("failed", run_id)
    row = _run(svc, run_id)
    assert row["stop_reason"] == "timeout" and "timeout" in row["error"]


def test_a_database_error_on_a_live_connection_is_left_to_the_lease(svc: psycopg.Connection, world: dict[str, Any],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Not the run's fault and not a verdict on it: the attempt counter decides, as for any interrupted worker."""
    run_id = _submit(svc, world, "a")
    with monkeypatch.context() as broken:
        broken.setattr(executor, "_STEP", executor._STEP.replace("agent_run_steps", "no_such_diary"))
        assert _work_on(_Model(ANSWER)) == executor.Worked("lost", run_id)
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"], row["error"]) == ("running", 1, None) and row["lease_until"] is not None
    _expire(svc, run_id)
    assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", run_id)
    assert _run(svc, run_id)["attempts"] == 2


def test_a_run_deleted_during_a_call_keeps_its_billed_spend_on_the_ledger(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    """Only an account's deletion removes a run. The provider has billed by then, and the ceiling must count it."""
    run_id = _submit(svc, world, "a")

    def account_deleted(call: int) -> None:
        svc.execute("DELETE FROM agent_runs WHERE id = %s", (run_id,))
    assert _work_on(_Model(ANSWER, during=account_deleted)) == executor.Worked("lost", run_id)
    logged = svc.execute("SELECT cost_usd FROM llm_calls WHERE agent_run_id = %s", (run_id,)).fetchall()
    assert len(logged) == 1 and logged[0][0] > 0


def test_a_definition_gone_since_submit_fails_the_run_with_its_reason(svc: psycopg.Connection, world: dict[str, Any],
                                                                      monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_id = _submit(svc, world, "a")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(executor, "SKILLS_DIR", empty)
    assert _work_on(_Model()).state == "failed"
    assert AGENT in _run(svc, run_id)["error"]


def _swap_agent(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tools: list[str], **limits: Any) -> None:
    root = tmp_path / uuid.uuid4().hex
    bounds = {"max_iterations": 6, "max_cost_usd": 1.0, "wall_clock_timeout_s": 60, **limits}
    write_folder(root, meta=frontmatter(AGENT, allowed_tools=tools, limits=bounds))
    monkeypatch.setattr(executor, "SKILLS_DIR", root)


class _Bare(Args):
    pass  # a probe takes nothing (a docstring would reach the schema)


def _probe(monkeypatch: pytest.MonkeyPatch, handler: Callable[[Context, _Bare], Any],
           tenancy: Tenancy = Tenancy.TENANT) -> None:
    entry = Entry(name="probe", description="probe", args=_Bare, handler=handler, tenancy=tenancy, effect=Effect.READ)
    monkeypatch.setattr(registry, "ENTRIES", {**registry.ENTRIES, "probe": entry})


def test_a_nul_in_a_tool_result_does_not_fail_the_run(svc: psycopg.Connection, world: dict[str, Any], tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _probe(monkeypatch, lambda ctx, args: {"data": [{"text": "ad\x00text"}]})
    _swap_agent(world, monkeypatch, tmp_path, ["probe"])
    run_id = _submit(svc, world, "a")
    assert _work_on(_Model(reply(("probe", {})), reply((SUBMIT, {"markdown": "Done\x00."})))).state == "succeeded"
    assert _steps(svc, run_id)[1]["payload"]["output"]["data"] == [{"text": "adtext"}]
    assert _run(svc, run_id)["result_markdown"] == "Done."


# --- work once: whose data a run reads ----------------------------------------------------------------------------------

def test_a_run_for_account_a_reads_as_a_and_never_bs_rows(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    for me, theirs in (("a", "b_acc"), ("b", "a_acc")):
        run_id = _submit(svc, world, me)
        assert _work_on(_Model(reply((OWN_CONTEXT, {})), ANSWER)).state == "succeeded"
        data = _steps(svc, run_id)[1]["payload"]["output"]["data"]
        seen = {row["id"] for row in data["collections"]}
        assert seen and seen == _owned_ids(svc, "collections", world[f"{me}_acc"])
        assert not seen & _owned_ids(svc, "collections", world[theirs])


def test_a_two_membership_users_run_on_one_account_reads_that_account_only(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    svc.execute("INSERT INTO account_members (account_id, user_id, role) VALUES (%s, %s, 'member')",
                (world["b_acc"], world["a"]))
    run_id = executor.submit(svc, principal_user_id=world["a"], account_id=world["b_acc"], agent=AGENT,
                             value={"town": "Brno"}).run_id
    assert _work_on(_Model(reply((OWN_CONTEXT, {})), ANSWER)).state == "succeeded"
    seen = {row["id"] for row in _steps(svc, run_id)[1]["payload"]["output"]["data"]["collections"]}
    assert seen == _owned_ids(svc, "collections", world["b_acc"])


def test_an_admin_principals_run_is_no_admin_when_it_runs(svc: psycopg.Connection, world: dict[str, Any], admin: uuid.UUID,
                                                          tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(ctx: Context, args: _Bare) -> dict[str, Any]:
        with ctx.conn.cursor() as cur:
            cur.execute("SELECT is_platform_admin()")
            return {"data": {"admin": cur.fetchone()[0]}}
    _probe(monkeypatch, probe)
    _swap_agent(world, monkeypatch, tmp_path, ["probe"])
    run_id = _submit(svc, world, "a")
    assert _work_on(_Model(reply(("probe", {})), ANSWER)).state == "succeeded"
    assert _steps(svc, run_id)[1]["payload"]["output"]["data"] == {"admin": False}


# --- work once: claims, caps, the ceiling and the brake -----------------------------------------------------------------

@pytest.mark.parametrize("brake", [None, False])
def test_the_brake_dark_claims_nothing_and_leaves_the_queue_alone(svc: psycopg.Connection, world: dict[str, Any],
                                                                  brake: bool | None) -> None:
    run_id = _submit(svc, world, "a")
    _set(svc, "agent_executor_enabled", brake)
    assert _work_on(_Model()) == executor.Worked("dark")
    assert _run(svc, run_id)["status"] == "queued"


def test_the_migration_lands_the_executor_dark_and_the_allowlist_absent() -> None:
    sql = Path(__file__).resolve().parents[1].joinpath("migrations/704_agent_runs.sql").read_text(encoding="utf-8")
    assert "('agent_executor_enabled', 'false'," in sql
    assert "('agent_run_allowlist'" not in sql


def test_an_empty_queue_is_idle(world: dict[str, Any]) -> None:
    assert _work_on(_Model()) == executor.Worked("idle")


def test_a_ceiling_of_a_cent_parks_new_claims_and_raising_it_frees_them(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    _set(svc, "agent_daily_ceiling_usd", 0.01)
    first = _submit(svc, world, "a")
    assert _work_on(_Model(reply((SUBMIT, {"markdown": "Done."}), usage=(20_000, 0)))) == executor.Worked("succeeded", first)
    second = _submit(svc, world, "b")
    assert _work_on(_Model()) == executor.Worked("parked")
    assert _run(svc, second)["status"] == "queued", "parked, never failed"
    _set(svc, "agent_daily_ceiling_usd", 10)
    assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", second)


@pytest.mark.parametrize("key", ["agent_daily_ceiling_usd", "agent_account_running_cap"])
def test_an_absent_number_parks_rather_than_guesses(svc: psycopg.Connection, world: dict[str, Any], key: str) -> None:
    run_id = _submit(svc, world, "a")
    _set(svc, key, None)
    assert _work_on(_Model()) == executor.Worked("parked")
    assert _run(svc, run_id)["status"] == "queued"


def test_the_oldest_queued_run_under_its_accounts_cap_is_next(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    held = [_submit(svc, world, "a") for _ in range(2)]
    waiting = _submit(svc, world, "a")
    other = _submit(svc, world, "b")
    last = _submit(svc, world, "c")
    svc.execute("UPDATE agent_runs SET status = 'running', lease_until = now() + interval '1 hour' WHERE id = ANY(%s)", (held,))
    assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", other)
    assert _run(svc, waiting)["status"] == "queued" and _run(svc, last)["status"] == "queued"
    assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", last)
    assert _work_on(_Model()) == executor.Worked("idle")
    svc.execute("UPDATE agent_runs SET status = 'succeeded', lease_until = NULL WHERE id = ANY(%s)", (held,))
    assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", waiting)


class _Peaks:
    """A model every worker shares, that watches how many runs each account has running while calls are in the air."""

    name = "anthropic"

    def __init__(self) -> None:
        self.peak: Counter[uuid.UUID] = Counter()
        self.lock = threading.Lock()

    def price_for(self, model: str) -> ModelPrice:
        return CHEAP

    def complete(self, **kwargs: Any) -> Completion:
        with _connect() as conn:
            rows = conn.execute("SELECT account_id, count(*) FROM agent_runs WHERE status = 'running' GROUP BY 1").fetchall()
        with self.lock:
            for account, running in rows:
                self.peak[account] = max(self.peak[account], running)
        time.sleep(0.02)
        return reply((SUBMIT, {"markdown": "Done."}))


def test_twenty_accounts_fifty_runs_a_cap_of_two_and_nobody_fails_for_waiting(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    keys = [f"u{i}" for i in range(20)]
    with signups(svc, "load", *keys) as load:
        users = [load[k] for k in keys]
        svc.execute("UPDATE auth.users SET email_confirmed_at = now() WHERE id = ANY(%s)", (users,))
        emails = [r[0] for r in svc.execute("SELECT email FROM auth.users WHERE id = ANY(%s)", (users,)).fetchall()]
        _set(svc, "agent_run_allowlist", emails)
        shares = [12, 8] + [2] * 15 + [0, 0, 0]
        assert sum(shares) == 50
        run_ids = [executor.submit(svc, principal_user_id=load[k], account_id=load[f"{k}_acc"], agent=AGENT,
                                   value={"town": "Brno"}).run_id
                   for k, share in zip(keys, shares) for _ in range(share)]
        assert len(run_ids) == 50

        model = _Peaks()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                while True:
                    if _work_on(model).state == "idle":
                        with _connect() as conn:
                            left = conn.execute("SELECT count(*) FROM agent_runs "
                                                "WHERE status IN ('queued', 'running')").fetchone()[0]
                        if not left:
                            return
                        time.sleep(0.01)
            except BaseException as exc:  # noqa: BLE001 - reported on the main thread
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert not errors and not any(t.is_alive() for t in threads)
        statuses = Counter(r[0] for r in svc.execute("SELECT status FROM agent_runs WHERE id = ANY(%s)", (run_ids,)).fetchall())
        assert statuses == {"succeeded": 50}
        assert max(model.peak.values()) == 2, "never above the cap; reaching it shows the test had the concurrency to try"
        svc.execute("DELETE FROM agent_runs")


# --- work once: interruption ------------------------------------------------------------------------------------------

class _Crash(BaseException):
    """A worker that dies mid-call: nothing of ours gets to run after it."""


def _crash(call: int) -> None:
    raise _Crash


def _expire(svc: psycopg.Connection, run_id: int) -> None:
    svc.execute("UPDATE agent_runs SET lease_until = now() - interval '1 second' WHERE id = %s", (run_id,))


def test_an_expired_lease_requeues_once_and_the_second_expiry_fails_the_run_with_a_reason(svc: psycopg.Connection,
                                                                                         world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    with pytest.raises(_Crash):
        _work_on(_Model(during=_crash))
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"]) == ("running", 1) and row["lease_until"] is not None

    assert _work_on(_Model(), stop=_set_event()) == executor.Worked("idle"), "not yet expired, nothing to reap"
    assert _run(svc, run_id)["status"] == "running"

    _expire(svc, run_id)
    assert _work_on(_Model(), stop=_set_event()) == executor.Worked("idle")
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"], row["lease_until"], row["error"]) == ("queued", 2, None, None)

    with pytest.raises(_Crash):
        _work_on(_Model(during=_crash))
    _expire(svc, run_id)
    assert _work_on(_Model()) == executor.Worked("idle")
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"]) == ("failed", 2)
    assert "lease expired" in row["error"] and row["finished_at"] is not None


def _set_event() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


def test_a_run_retried_after_a_crash_succeeds_with_attempts_two_and_keeps_both_diaries(svc: psycopg.Connection,
                                                                                      world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")

    def second_call_crashes(call: int) -> None:
        if call == 2:
            raise _Crash
    with pytest.raises(_Crash):
        _work_on(_Model(reply((OWN_CONTEXT, {})), during=second_call_crashes))
    _expire(svc, run_id)
    assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", run_id)
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"]) == ("succeeded", 2)
    assert [(s["attempt"], s["n"]) for s in _steps(svc, run_id)] == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_a_retry_spends_what_the_runs_cap_has_left_not_the_whole_cap_again(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    with pytest.raises(_Crash):
        _work_on(_Model(during=_crash))
    svc.execute("INSERT INTO llm_calls (called_for, provider, model, input_tokens, output_tokens, cost_usd, duration_ms, "
                "agent_run_id) VALUES ('agent_run', 'anthropic', %s, 0, 0, 0.999, 0, %s)", (MODEL, run_id))
    _expire(svc, run_id)
    retry = _Model()
    assert _work_on(retry) == executor.Worked("failed", run_id)
    assert retry.calls == []
    row = _run(svc, run_id)
    assert row["stop_reason"] == "max_cost" and float(row["cost_usd"]) == pytest.approx(0.999)


def test_a_worker_that_lost_its_lease_writes_nothing_more(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")

    def another_worker_takes_over(call: int) -> None:
        _expire(svc, run_id)
        assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", run_id)
    assert _work_on(_Model(reply((OWN_CONTEXT, {})), during=another_worker_takes_over)) == executor.Worked("lost", run_id)
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"]) == ("succeeded", 2)
    assert {s["attempt"] for s in _steps(svc, run_id)} == {2}


def test_a_long_model_call_keeps_its_lease_while_its_worker_lives(svc: psycopg.Connection, world: dict[str, Any],
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """A reply can take minutes against a two-minute lease; only a dead worker should lose its run."""
    monkeypatch.setattr(executor, "LEASE_S", 1.2)
    run_id = _submit(svc, world, "a")

    def slow(call: int) -> None:
        time.sleep(2.5)
        assert _work_on(_Model()) == executor.Worked("idle"), "the lease held, so a second worker had nothing to take"
    assert _work_on(_Model(ANSWER, during=slow)) == executor.Worked("succeeded", run_id)
    assert _run(svc, run_id)["attempts"] == 1


def test_a_hung_worker_stops_renewing_so_its_run_can_be_taken(svc: psycopg.Connection, world: dict[str, Any],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The heartbeat says the worker lives, not that it progresses; past every agent's wall clock it stops saying so."""
    monkeypatch.setattr(executor, "LEASE_S", 0.6)
    monkeypatch.setattr(executor, "HEARTBEAT_CEILING_S", 0.3)
    run_id = _submit(svc, world, "a")

    def hung(call: int) -> None:
        time.sleep(1.5)
        assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", run_id)
    assert _work_on(_Model(ANSWER, during=hung)) == executor.Worked("lost", run_id)
    assert _run(svc, run_id)["attempts"] == 2


def test_sigterm_stops_between_steps_and_releases_then_the_retry_completes(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    stop = threading.Event()
    model = _Model(reply((OWN_CONTEXT, {})), during=lambda call: stop.set())
    assert _work_on(model, stop=stop) == executor.Worked("released", run_id)
    assert len(model.calls) == 1
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"], row["lease_until"]) == ("queued", 2, None)
    assert [s["name"] for s in _steps(svc, run_id)] == [MODEL], "the tool the model asked for never ran"
    assert _work_on(_Model(ANSWER)) == executor.Worked("succeeded", run_id)
    assert _run(svc, run_id)["attempts"] == 2


def test_a_second_interruption_by_shutdown_fails_the_run_with_a_reason(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    """Story 25: a deploy counts: at most once more, then a reason. Nothing loops."""
    run_id = _submit(svc, world, "a")
    for state in ("released", "failed"):
        stop = threading.Event()
        assert _work_on(_Model(reply((OWN_CONTEXT, {})), during=lambda call: stop.set()), stop=stop).state == state
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"]) == ("failed", 2) and "shut down" in row["error"]


def test_an_answer_accepted_as_the_worker_stops_is_kept_not_repaid(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    stop = threading.Event()
    assert _work_on(_Model(ANSWER, during=lambda call: stop.set()), stop=stop) == executor.Worked("succeeded", run_id)
    assert _run(svc, run_id)["attempts"] == 1


def test_a_refused_answer_is_not_the_end_so_the_stop_lands_on_it(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    stop = threading.Event()
    refused = reply((SUBMIT, {"markdown": "Find 7.", "cited_property_ids": [7]}))
    assert _work_on(_Model(refused, during=lambda call: stop.set()), stop=stop) == executor.Worked("released", run_id)


def test_a_shutdown_claims_nothing_new(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    assert _work_on(_Model(), stop=_set_event()) == executor.Worked("idle")
    assert _run(svc, run_id)["status"] == "queued"


# --- cancel -------------------------------------------------------------------------------------------------------------

def _cancel(svc: psycopg.Connection, world: dict[str, Any], run_id: int, key: str = "a") -> str | None:
    return executor.cancel(svc, run_id=run_id, account_id=world[f"{key}_acc"])


def test_cancel_between_steps_stops_the_run(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    model = _Model(reply((OWN_CONTEXT, {})), during=lambda call: _cancel(svc, world, run_id))
    assert _work_on(model) == executor.Worked("cancelled", run_id)
    assert len(model.calls) == 1, "no second model call was paid for"
    row = _run(svc, run_id)
    assert row["status"] == "cancelled" and row["finished_at"] and row["lease_until"] is None and row["cost_usd"] > 0
    assert [s["name"] for s in _steps(svc, run_id)] == [MODEL], "the tool the model asked for never ran"


def test_a_cancel_wins_over_an_answer_in_hand(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    """A shutdown spares a paid answer, since retrying would pay again; a cancel is the user saying stop."""
    run_id = _submit(svc, world, "a")
    assert _work_on(_Model(ANSWER, during=lambda call: _cancel(svc, world, run_id))) == executor.Worked("cancelled", run_id)
    assert _run(svc, run_id)["result_markdown"] is None


def test_cancelling_a_queued_run_ends_it_and_it_is_never_claimed(svc: psycopg.Connection, world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    assert _cancel(svc, world, run_id) == "cancelled"
    assert _work_on(_Model()) == executor.Worked("idle")


def test_a_run_cancelled_while_its_worker_was_dead_ends_cancelled_rather_than_retries(svc: psycopg.Connection,
                                                                                     world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    with pytest.raises(_Crash):
        _work_on(_Model(during=_crash))
    assert _cancel(svc, world, run_id) == "running"
    _expire(svc, run_id)
    assert _work_on(_Model()) == executor.Worked("idle")
    row = _run(svc, run_id)
    assert (row["status"], row["attempts"], row["error"]) == ("cancelled", 1, None)


def test_cancel_reaches_only_this_accounts_runs_and_leaves_an_ended_run_as_it_is(svc: psycopg.Connection,
                                                                                 world: dict[str, Any]) -> None:
    run_id = _submit(svc, world, "a")
    assert _cancel(svc, world, run_id, "b") is None
    assert _run(svc, run_id)["status"] == "queued" and _run(svc, run_id)["cancel_requested_at"] is None
    assert _work_on(_Model(ANSWER)).state == "succeeded"
    assert _cancel(svc, world, run_id) == "succeeded"
    assert _run(svc, run_id)["cancel_requested_at"] is None
