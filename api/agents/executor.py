"""Runs queue and execute durably: `submit` inserts one, `work_once` claims and runs the oldest it may, `cancel` flags one.

The row is the job (ADR-0001). A claim holds a lease that every step and a heartbeat renew, compare-and-set on the row's
`attempts`, so a worker that lost its run can write nothing more. An interrupted run (a lapsed lease, a SIGTERM) starts
again from the beginning once, and fails with its reason the second time (spec #2055 story 25). Waiting never fails a run.
Every switch and number is an `app_settings` row, read when used; an absent one stops rather than guesses.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, NamedTuple

import psycopg
from psycopg.types.json import Jsonb

from api import dependencies
from api.agents import cassette, claims, loop, terminator
from api.agents.definition import SKILLS_DIR, Definition, DefinitionError, InputError, load
from api.capabilities import registry
from api.capabilities.truncate import encode
from api.llm_client import LLMClient, ProviderError
from api.providers import CompletionProvider
from scraper import db

LOG = logging.getLogger(__name__)

BRAKE = "agent_executor_enabled"
ALLOWLIST = "agent_run_allowlist"
DAILY_CEILING = "agent_daily_ceiling_usd"
RUNNING_CAP = "agent_account_running_cap"
IN_FLIGHT_CAP = "agent_account_in_flight_cap"
_SETTINGS = (BRAKE, ALLOWLIST, DAILY_CEILING, RUNNING_CAP, IN_FLIGHT_CAP)

LEASE_S = 120.0
MAX_ATTEMPTS = 2
SUMMARY_CHARS = 500

SubmitRefusal = Literal["not_allowlisted", "in_flight_cap"]
WorkState = Literal["dark", "parked", "idle", "succeeded", "failed", "cancelled", "released", "lost"]


class SubmitRefused(Exception):
    """A submit the account may not make; `reason` is for the route, the message for the user."""

    def __init__(self, reason: SubmitRefusal, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Submitted:
    run_id: int
    status: str
    created: bool


@dataclass(frozen=True)
class Worked:
    state: WorkState
    run_id: int | None = None


class _Claimed(NamedTuple):
    id: int
    account_id: uuid.UUID
    principal_user_id: uuid.UUID
    agent: str
    provider: str
    input: dict[str, Any]
    attempts: int


class _LeaseLost(Exception):
    """The row is no longer this worker's: re-queued or ended by someone else."""


class _Cancelled(Exception):
    pass


class _Stopped(Exception):
    pass


_LEDGER_COST = "(SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE agent_run_id = agent_runs.id)"
_HELD = "id = %(id)s AND attempts = %(attempts)s AND status = 'running'"
_LEASE = "now() + make_interval(secs => %(lease)s)"

_ALLOWED = """
SELECT EXISTS (SELECT 1 FROM auth.users u JOIN account_members m ON m.user_id = u.id
                WHERE u.id = %s AND m.account_id = %s AND u.email_confirmed_at IS NOT NULL
                  AND lower(u.email) = ANY(%s))"""
_SUBMIT_LOCK = "SELECT pg_advisory_xact_lock(hashtext('agent_run_submit'), hashtext(%s))"
_EXISTING = "SELECT id, status FROM agent_runs WHERE account_id = %s AND idempotency_key = %s"
_IN_FLIGHT = "SELECT count(*) FROM agent_runs WHERE account_id = %s AND status IN ('queued', 'running')"
_INSERT = """
INSERT INTO agent_runs (account_id, principal_user_id, agent, definition_sha, tool_set_sha, commit_sha, provider, model,
                        input, idempotency_key)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, status"""

# Serialised, so two workers cannot both count an account under its cap and overshoot it together.
_CLAIM_LOCK = "SELECT pg_advisory_xact_lock(hashtext('agent_run_claim'))"
_SPENT_TODAY = """
SELECT coalesce(sum(cost_usd), 0) FROM llm_calls
 WHERE called_for = %s AND called_at >= date_trunc('day', now(), 'UTC')"""
_CLAIM = f"""
UPDATE agent_runs SET status = 'running', lease_until = {_LEASE}, started_at = coalesce(started_at, now())
 WHERE id = (SELECT r.id FROM agent_runs r
              WHERE r.status = 'queued'
                AND (SELECT count(*) FROM agent_runs x
                      WHERE x.account_id = r.account_id AND x.status = 'running') < %(cap)s
              ORDER BY r.created_at, r.id
              LIMIT 1 FOR UPDATE OF r SKIP LOCKED)
RETURNING id, account_id, principal_user_id, agent, provider, input, attempts"""

_RENEW = f"UPDATE agent_runs SET lease_until = {_LEASE} WHERE {_HELD}"
_RENEW_FOR_STEP = f"""
UPDATE agent_runs SET lease_until = {_LEASE}, cost_usd = {_LEDGER_COST} WHERE {_HELD}
RETURNING cancel_requested_at IS NOT NULL"""
_STEP = """
INSERT INTO agent_run_steps (run_id, account_id, attempt, n, kind, name, input_sha, summary, payload, llm_call_id,
                             cost_usd, duration_ms)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
_STAMP = f"""
UPDATE agent_runs SET definition_sha = %(definition_sha)s, tool_set_sha = %(tool_set_sha)s, commit_sha = %(commit_sha)s,
                      model = %(model)s
 WHERE {_HELD}"""
_FINISH = f"""
UPDATE agent_runs
   SET status = %(status)s, stop_reason = %(stop_reason)s, error = %(error)s, result_markdown = %(result_markdown)s,
       cited_property_ids = %(cited_property_ids)s, files = %(files)s, proposals = %(proposals)s, output = %(output)s,
       lease_until = NULL, finished_at = now(), cost_usd = {_LEDGER_COST}
 WHERE {_HELD}"""

# One transition for every interruption: a cancel asked for wins, then the second interruption fails the run.
_INTERRUPT = f"""
UPDATE agent_runs
   SET status = CASE WHEN cancel_requested_at IS NOT NULL THEN 'cancelled'
                     WHEN attempts >= %(max)s THEN 'failed' ELSE 'queued' END,
       attempts = CASE WHEN cancel_requested_at IS NULL AND attempts < %(max)s THEN attempts + 1 ELSE attempts END,
       error = CASE WHEN cancel_requested_at IS NULL AND attempts >= %(max)s THEN %(reason)s END,
       finished_at = CASE WHEN cancel_requested_at IS NOT NULL OR attempts >= %(max)s THEN now() END,
       lease_until = NULL, cost_usd = {_LEDGER_COST}
 WHERE {{where}}
RETURNING id, status, attempts"""
_REAP = _INTERRUPT.format(where="status = 'running' AND lease_until < now()")
_RELEASE = _INTERRUPT.format(where=_HELD)

_CANCEL = """
UPDATE agent_runs
   SET cancel_requested_at = coalesce(cancel_requested_at, now()),
       status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
       finished_at = CASE WHEN status = 'queued' THEN now() ELSE finished_at END
 WHERE id = %s AND account_id = %s AND status IN ('queued', 'running')
RETURNING status"""
_STATUS = "SELECT status FROM agent_runs WHERE id = %s AND account_id = %s"


def _settings(conn: psycopg.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM app_settings WHERE key = ANY(%s)", (list(_SETTINGS),)).fetchall()
    return dict(rows)


def _number(settings: Mapping[str, Any], key: str) -> float | None:
    value = settings.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _definition(agent: str) -> Definition:
    """The agent by name; a name that is no folder is refused here, so an input can never walk out of `skills/`."""
    if agent not in {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}:
        raise DefinitionError(f"there is no agent named {agent!r}")
    return load(SKILLS_DIR / agent)


def tool_set_sha(definition: Definition) -> str:
    """A hash of every schema this agent's model is offered, the terminator included."""
    return hashlib.sha256(json.dumps(loop.tool_schemas(definition), sort_keys=True, default=str).encode()).hexdigest()


def _pin(definition: Definition, provider: str) -> dict[str, str]:
    """What the row records of the code a run is on: re-stamped at every start, so it names what actually ran."""
    if provider not in definition.models:
        raise DefinitionError(f"{definition.name} runs on {sorted(definition.models)}, not {provider}")
    return {"definition_sha": definition.sha, "tool_set_sha": tool_set_sha(definition),
            "commit_sha": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"), "model": definition.models[provider]}


def _storable(value: Any) -> Any:
    """`value` as JSON reads it back, less the NUL Postgres refuses in text and jsonb (an ad's text can carry one)."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_storable(v) for v in value]
    if isinstance(value, dict):
        return {_storable(k): _storable(v) for k, v in value.items()}
    return value


# --- submit --------------------------------------------------------------------------------------------------------

def submit(conn: psycopg.Connection, *, principal_user_id: uuid.UUID, account_id: uuid.UUID, agent: str,
           value: Mapping[str, Any], provider: str | None = None, idempotency_key: str | None = None) -> Submitted:
    """Queue one run on the service connection, for the identity the route stamped from the caller's own token.

    Raises DefinitionError or InputError for a run that could not start, SubmitRefused for one this account may not
    make. A repeated idempotency key returns the run it made, at any status. There is no act-as: a run acts for its
    own user only (spec #2055 story 34).
    """
    definition = _definition(agent)
    definition.check_input(value)
    provider = provider or next(iter(definition.models))
    pinned = _pin(definition, provider)
    with conn.transaction(), conn.cursor() as cur:
        settings = _settings(conn)
        allowed = settings.get(ALLOWLIST)
        emails = [e.lower() for e in allowed if isinstance(e, str)] if isinstance(allowed, list) else []
        cur.execute(_ALLOWED, (principal_user_id, account_id, emails))
        if not cur.fetchone()[0]:
            raise SubmitRefused("not_allowlisted", "Agent runs are not open to this account yet.")
        cur.execute(_SUBMIT_LOCK, (str(account_id),))
        if idempotency_key is not None:
            cur.execute(_EXISTING, (account_id, idempotency_key))
            if (row := cur.fetchone()) is not None:
                return Submitted(run_id=row[0], status=row[1], created=False)
        cap = _number(settings, IN_FLIGHT_CAP) or 0
        cur.execute(_IN_FLIGHT, (account_id,))
        if cur.fetchone()[0] >= cap:
            raise SubmitRefused("in_flight_cap", f"This account already has {cap:g} runs queued or running.")
        cur.execute(_INSERT, (account_id, principal_user_id, agent, pinned["definition_sha"], pinned["tool_set_sha"],
                              pinned["commit_sha"], provider, pinned["model"], Jsonb(_storable(cassette.plain(value))),
                              idempotency_key))
        run_id, status = cur.fetchone()
    return Submitted(run_id=run_id, status=status, created=True)


# --- cancel --------------------------------------------------------------------------------------------------------

def cancel(conn: psycopg.Connection, *, run_id: int, account_id: uuid.UUID) -> str | None:
    """Ask a run of this account to stop: one still queued ends now, one running at its next step. Its status after,
    a run already ended left as it is; None when the account holds no such run."""
    row = conn.execute(_CANCEL, (run_id, account_id)).fetchone()
    if row is None:
        row = conn.execute(_STATUS, (run_id, account_id)).fetchone()
    return row[0] if row else None


# --- work once -----------------------------------------------------------------------------------------------------

def _reap(conn: psycopg.Connection) -> None:
    rows = conn.execute(_REAP, {"max": MAX_ATTEMPTS, "reason": "its worker stopped twice (the lease expired)"}).fetchall()
    for run_id, status, attempts in rows:
        LOG.warning("AGENT_RUN lease expired run=%s now=%s attempts=%s", run_id, status, attempts)


def _claim(conn: psycopg.Connection, settings: Mapping[str, Any]) -> _Claimed | Literal["parked", "idle"]:
    ceiling = _number(settings, DAILY_CEILING)
    cap = _number(settings, RUNNING_CAP)
    if ceiling is None or cap is None:
        LOG.warning("AGENT_RUN parked: %s and %s must both be numbers", DAILY_CEILING, RUNNING_CAP)
        return "parked"
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_CLAIM_LOCK)
        cur.execute(_SPENT_TODAY, (loop.CALLED_FOR,))
        spent = float(cur.fetchone()[0])
        if spent >= ceiling:
            LOG.warning("AGENT_RUN parked: $%.4f spent today, ceiling $%.2f", spent, ceiling)
            return "parked"
        cur.execute(_CLAIM, {"lease": LEASE_S, "cap": cap})
        row = cur.fetchone()
    return _Claimed(*row) if row else "idle"


@contextlib.contextmanager
def _keep_lease(connect: Callable[[], psycopg.Connection], run: _Claimed) -> Iterator[None]:
    """Renew between steps too: one model call may outlast the lease, and only a dead worker should lose its run. A
    connection of its own, since the run's may be inside a tool's transaction."""
    done = threading.Event()

    def beat() -> None:
        while not done.wait(LEASE_S / 3):
            try:
                with connect() as conn:
                    conn.execute(_RENEW, {"lease": LEASE_S, "id": run.id, "attempts": run.attempts})
            except psycopg.Error as exc:
                LOG.warning("AGENT_RUN lease renewal failed run=%s: %s", run.id, exc)

    thread = threading.Thread(target=beat, name=f"agent-run-{run.id}-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(timeout=5)


def _answer_in_hand(step: loop.Step, output: Mapping[str, Any]) -> bool:
    """A reply that only hands in an answer, or the answer accepted: already paid for, with nothing left to stop, so a
    stop lands on the step after (a refused answer has one); stopping here would pay for it all again."""
    if step.kind == "model":
        return [call["name"] for call in output["tool_calls"]] == [terminator.NAME]
    return step.name == terminator.NAME and "markdown" in output


def _recorder(conn: psycopg.Connection, run: _Claimed, stop: threading.Event) -> Callable[[loop.Step], None]:
    """The loop's journal, and the between-steps checkpoint: the step lands only under a held lease, then a cancel or
    a shutdown stops the loop by raising through it."""
    def record(step: loop.Step) -> None:
        given = _storable(cassette.plain(step.input))
        output = _storable(cassette.plain(step.output))
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_RENEW_FOR_STEP, {"lease": LEASE_S, "id": run.id, "attempts": run.attempts})
            held = cur.fetchone()
            if held is None:
                raise _LeaseLost
            cur.execute(_STEP, (run.id, run.account_id, run.attempts, step.n, step.kind, step.name,
                                hashlib.sha256(encode(given).encode()).hexdigest(), encode(output)[:SUMMARY_CHARS],
                                Jsonb({"input": given, "output": output}), step.llm_call_id, step.cost_usd,
                                step.duration_ms))
        if _answer_in_hand(step, output):
            return
        if held[0]:
            raise _Cancelled
        if stop.is_set():
            raise _Stopped
    return record


def _finish(conn: psycopg.Connection, run: _Claimed, status: Literal["succeeded", "failed", "cancelled"], *,
            stop_reason: str | None = None, error: str | None = None, answer: terminator.Answer | None = None
            ) -> WorkState:
    fields = {
        "status": status, "stop_reason": stop_reason, "error": error,
        "result_markdown": _storable(answer.markdown) if answer else None,
        "cited_property_ids": list(answer.cited_property_ids) if answer else [],
        "files": _storable(list(answer.files)) if answer else [],
        "proposals": Jsonb(_storable(cassette.plain(answer.proposed_actions)) if answer else []),
        "output": Jsonb(_storable(cassette.plain(answer.output))) if answer and answer.output is not None else None,
    }
    written = conn.execute(_FINISH, {**fields, "id": run.id, "attempts": run.attempts})
    return status if written.rowcount else "lost"


def _release(conn: psycopg.Connection, run: _Claimed) -> WorkState:
    row = conn.execute(_RELEASE, {"max": MAX_ATTEMPTS, "reason": "its worker shut down twice", "id": run.id,
                                  "attempts": run.attempts}).fetchone()
    if row is None:
        return "lost"
    return "released" if row[1] == "queued" else row[1]


def _reason(exc: Exception) -> str:
    """What the account reads on its row: a definition's own words; the provider's and our internals go to the log."""
    if isinstance(exc, (DefinitionError, InputError)):
        return str(exc)
    return "the model call failed" if isinstance(exc, ProviderError) else "an internal error stopped the run"


def _execute(conn: psycopg.Connection, run: _Claimed, providers: Mapping[str, CompletionProvider],
             stop: threading.Event) -> WorkState:
    try:
        definition = _definition(run.agent)
        conn.execute(_STAMP, {**_pin(definition, run.provider), "id": run.id, "attempts": run.attempts})
        # A retry starts from the beginning but not from zero: the cap is per run, and its earlier attempts were paid.
        spent = float(conn.execute(f"SELECT {_LEDGER_COST} FROM agent_runs WHERE id = %s", (run.id,)).fetchone()[0])
        budget = replace(definition.limits, max_cost_usd=max(0.0, definition.limits.max_cost_usd - spent))
        caller = claims.caller_for(conn, principal_user_id=run.principal_user_id, account_id=run.account_id)
        outcome = loop.run(replace(definition, limits=budget), run.input, provider=run.provider,
                           llm=LLMClient(conn, providers=dict(providers), agent_run_id=run.id),
                           call_tool=lambda name, args: registry.call(caller, name, args),
                           record=_recorder(conn, run, stop))
    except _LeaseLost:
        LOG.warning("AGENT_RUN lost its lease run=%s attempt=%s", run.id, run.attempts)
        return "lost"
    except _Cancelled:
        return _finish(conn, run, "cancelled")
    except _Stopped:
        return _release(conn, run)
    except Exception as exc:  # noqa: BLE001 - a run must end with its reason, not wait out a lease nobody will renew
        LOG.exception("AGENT_RUN failed run=%s", run.id)
        return _finish(conn, run, "failed", error=_reason(exc))
    if outcome.answer is None:
        return _finish(conn, run, "failed", stop_reason=outcome.stop_reason,
                       error=f"the run ended without an accepted answer: {outcome.stop_reason}")
    return _finish(conn, run, "succeeded", stop_reason=outcome.stop_reason, answer=outcome.answer)


def work_once(*, stop: threading.Event, providers: Mapping[str, CompletionProvider] | None = None,
              connect: Callable[[], psycopg.Connection] = db.connect) -> Worked:
    """Claim the oldest queued run whose account is under its cap and run it to its end, its release or a lost lease.
    The caller's SIGTERM handler sets `stop`; the run stops at its next step and goes back on the queue."""
    with connect() as conn:
        settings = _settings(conn)
        if settings.get(BRAKE) is not True:
            return Worked("dark")
        _reap(conn)
        if stop.is_set():
            return Worked("idle")
        claimed = _claim(conn, settings)
        if isinstance(claimed, str):
            return Worked(claimed)
        LOG.info("AGENT_RUN claimed run=%s agent=%s attempt=%s", claimed.id, claimed.agent, claimed.attempts)
        with _keep_lease(connect, claimed):
            try:
                state = _execute(conn, claimed, providers or dependencies.get_providers(), stop)
            except psycopg.Error:
                # The ledger itself is unreachable, so nothing can be written; the lease decides.
                LOG.exception("AGENT_RUN left to its lease run=%s", claimed.id)
                state = "lost"
        LOG.info("AGENT_RUN done run=%s state=%s", claimed.id, state)
        return Worked(state, claimed.id)
