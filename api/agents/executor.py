"""Runs queue and execute durably: `submit` inserts one, `work_once` claims and runs the oldest it may, `cancel` flags one.
`start_refusal` is the one gate on starting, `queue_state` says whether a queued run can move, `account_limit` reads an
account's daily limit and `set_account_limit` writes it, `offered_agents` lists the image's agents.

The row is the job (ADR-0001). A claim holds a lease that every step and a bounded heartbeat renew, compare-and-set on
the row's `attempts`, so a worker that lost its run can write nothing more. An interrupted run (a lapsed lease, a SIGTERM) starts
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
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
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
from api.routes import billing
from scraper import db
from toolkit import photo_assessment

LOG = logging.getLogger(__name__)

BRAKE = "agent_executor_enabled"
ALLOWLIST = "agent_run_allowlist"
DAILY_CEILING = "agent_daily_ceiling_usd"
RUNNING_CAP = "agent_account_running_cap"
IN_FLIGHT_CAP = "agent_account_in_flight_cap"
DAILY_LIMIT = "agent_account_daily_limit_usd"
_SETTINGS = (BRAKE, ALLOWLIST, DAILY_CEILING, RUNNING_CAP, IN_FLIGHT_CAP, DAILY_LIMIT)

AGENDA = "agents"
ALLOWLIST_REFUSAL = "Agent runs are not open to this account yet."
PLAN_REFUSAL = "Your plan does not include agent runs."

LEASE_S = 120.0
# Past every agent's wall clock (600 s at most, definition.limits): a worker still beating now is hung, not working.
HEARTBEAT_CEILING_S = 900.0
MAX_ATTEMPTS = 2
SUMMARY_CHARS = 500

SubmitRefusal = Literal["not_allowlisted", "plan_without_agents", "in_flight_cap"]
WorkState = Literal["dark", "parked", "idle", "succeeded", "failed", "cancelled", "released", "lost"]
QueueState = Literal["dark", "parked", "limited", "open"]


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


@dataclass(frozen=True)
class AccountLimit:
    """None for a figure this caller cannot read: the default is absent, or the spend is not theirs."""
    limit_usd: float | None
    spent_today_usd: float | None

    @property
    def reached(self) -> bool:
        return self.limit_usd is not None and self.spent_today_usd is not None and self.spent_today_usd >= self.limit_usd


class _Claimed(NamedTuple):
    id: int
    account_id: uuid.UUID
    principal_user_id: uuid.UUID
    agent: str
    provider: str
    input: dict[str, Any]
    attempts: int

    @property
    def held(self) -> dict[str, Any]:
        """The compare-and-set every write by this worker carries."""
        return {"id": self.id, "attempts": self.attempts}


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
# Every model call a run can make: its own turns and a paid tool's. A tool that spends on a model adds its value here.
_RUN_SPEND = (loop.CALLED_FOR, photo_assessment.CALLED_FOR)
_SPENT_TODAY = """
SELECT coalesce(sum(cost_usd), 0) FROM llm_calls
 WHERE called_for = ANY(%s) AND called_at >= date_trunc('day', now(), 'UTC')"""
# One figure per account with a queued run, before the walk: a held account's runs are passed over, not stopped at.
_CLAIM = f"""
WITH under_limit AS MATERIALIZED (
  SELECT q.account_id
    FROM (SELECT DISTINCT account_id FROM agent_runs WHERE status = 'queued') q
    LEFT JOIN agent_account_limits l ON l.account_id = q.account_id
   WHERE agent_account_spent_today(q.account_id, %(called_for)s) < coalesce(l.daily_limit_usd, %(default_limit)s::numeric))
UPDATE agent_runs SET status = 'running', lease_until = {_LEASE}, started_at = coalesce(started_at, now())
 WHERE id = (SELECT r.id FROM agent_runs r
              WHERE r.status = 'queued'
                AND r.account_id IN (SELECT account_id FROM under_limit)
                AND (SELECT count(*) FROM agent_runs x
                      WHERE x.account_id = r.account_id AND x.status = 'running') < %(cap)s
              ORDER BY r.created_at, r.id
              LIMIT 1 FOR UPDATE OF r SKIP LOCKED)
RETURNING id, account_id, principal_user_id, agent, provider, input, attempts"""

_ACCOUNT_LIMIT = """
SELECT coalesce((SELECT daily_limit_usd FROM agent_account_limits WHERE account_id = %(account)s),
                %(default_limit)s::numeric)::float8,
       agent_account_spent_today(%(account)s, %(called_for)s)::float8"""
_SET_LIMIT = """
INSERT INTO agent_account_limits (account_id, daily_limit_usd) VALUES (%s, %s)
ON CONFLICT (account_id) DO UPDATE SET daily_limit_usd = excluded.daily_limit_usd, updated_at = now()"""

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


def _folders() -> list[str]:
    return sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir())


def _definition(agent: str) -> Definition:
    """The agent by name; a name that is no folder is refused here, so an input can never walk out of `skills/`."""
    if agent not in _folders():
        raise DefinitionError(f"there is no agent named {agent!r}")
    return load(SKILLS_DIR / agent)


def offered_agents() -> list[Definition]:
    """Every agent a run can start on: the image's `skills/` folders, read now. A folder that does not load is logged and
    left out, so one bad folder cannot take the others away (CI's definition tests refuse it before it ships)."""
    offered = []
    for name in _folders():
        try:
            offered.append(_definition(name))
        except DefinitionError:
            LOG.exception("AGENT_RUN agent folder does not load: %s", name)
    return offered


def tool_set_sha(definition: Definition) -> str:
    """A hash of every schema this agent's model is offered, the terminator included."""
    return hashlib.sha256(json.dumps(loop.tool_schemas(definition), sort_keys=True, default=str).encode()).hexdigest()


def _pin(definition: Definition, provider: str) -> dict[str, str]:
    """What the row records of the code a run is on: re-stamped at every start, so it names what actually ran."""
    if provider not in definition.models:
        raise DefinitionError(f"{definition.name} runs on {sorted(definition.models)}, not {provider}")
    return {"definition_sha": definition.sha, "tool_set_sha": tool_set_sha(definition),
            "commit_sha": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"), "model": definition.models[provider]}


def _no_nul(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_no_nul(v) for v in value]
    if isinstance(value, dict):
        return {_no_nul(k): _no_nul(v) for k, v in value.items()}
    return value


def _storable(value: Any) -> Any:
    """`value` as JSON reads it back, less the NUL Postgres refuses in text and jsonb (an ad's text can carry one)."""
    return _no_nul(cassette.plain(value))


# --- starting ------------------------------------------------------------------------------------------------------

def _refusal(conn: psycopg.Connection, settings: Mapping[str, Any], principal_user_id: uuid.UUID,
             account_id: uuid.UUID) -> tuple[SubmitRefusal, str] | None:
    """No role is read here: an admin's plan answers as anyone's, since the gate is the account's and a run carries no admin powers."""
    allowed = settings.get(ALLOWLIST)
    emails = [e.lower() for e in allowed if isinstance(e, str)] if isinstance(allowed, list) else []
    if not conn.execute(_ALLOWED, (principal_user_id, account_id, emails)).fetchone()[0]:
        return "not_allowlisted", ALLOWLIST_REFUSAL
    if not billing.plan_includes(conn, account_id, AGENDA):
        return "plan_without_agents", PLAN_REFUSAL
    return None


def start_refusal(conn: psycopg.Connection, *, principal_user_id: uuid.UUID, account_id: uuid.UUID) -> str | None:
    """The one gate on starting a run, shared by `submit` and every surface that offers it: why this user may not start
    one on this account, in words for them, or None."""
    refusal = _refusal(conn, _settings(conn), principal_user_id, account_id)
    return refusal[1] if refusal else None


def account_limit(svc: psycopg.Connection, tenant: psycopg.Connection, *, account_id: uuid.UUID) -> AccountLimit:
    """An account's limit and its spend today. The figures read on the caller's own connection, under their own access."""
    row = tenant.execute(_ACCOUNT_LIMIT, {"account": account_id, "default_limit": _number(_settings(svc), DAILY_LIMIT),
                                          "called_for": list(_RUN_SPEND)}).fetchone()
    return AccountLimit(limit_usd=row[0], spent_today_usd=row[1])


def set_account_limit(conn: psycopg.Connection, *, account_id: uuid.UUID, daily_limit_usd: Decimal) -> None:
    conn.execute(_SET_LIMIT, (account_id, daily_limit_usd))


def queue_state(conn: psycopg.Connection, limit: AccountLimit) -> QueueState:
    """Whether this account's queued run can move now: `dark` the brake is off, `parked` the ceiling or a missing number
    holds it, `limited` only its own limit does. The platform's words come first: raising the limit would not move those."""
    settings = _settings(conn)
    if settings.get(BRAKE) is not True:
        return "dark"
    if _parked(conn, settings):
        return "parked"
    return "limited" if limit.reached else "open"


# --- submit --------------------------------------------------------------------------------------------------------

def submit(conn: psycopg.Connection, *, principal_user_id: uuid.UUID, account_id: uuid.UUID, agent: str,
           value: Mapping[str, Any], provider: str | None = None, idempotency_key: str | None = None) -> Submitted:
    """Queue one run on the service connection, for the identity the route stamped from the caller's own token; a
    repeated idempotency key returns the run it made. Raises SubmitRefused first, then DefinitionError or InputError."""
    # No act-as parameter: a run acts for its own user only (spec #2055 story 34).
    with conn.transaction(), conn.cursor() as cur:
        settings = _settings(conn)
        if (refusal := _refusal(conn, settings, principal_user_id, account_id)) is not None:
            raise SubmitRefused(*refusal)
        definition = _definition(agent)
        definition.check_input(value)
        provider = provider or next(iter(definition.models))
        pinned = _pin(definition, provider)
        cur.execute(_SUBMIT_LOCK, (str(account_id),))
        if idempotency_key is not None:
            cur.execute(_EXISTING, (account_id, idempotency_key))
            if (row := cur.fetchone()) is not None:
                return Submitted(run_id=row[0], status=row[1], created=False)
        cap = _number(settings, IN_FLIGHT_CAP)
        if cap is None:
            raise SubmitRefused("in_flight_cap", "Agent runs are not open right now.")
        cur.execute(_IN_FLIGHT, (account_id,))
        if cur.fetchone()[0] >= cap:
            raise SubmitRefused("in_flight_cap", f"This account already has {cap:g} runs queued or running.")
        cur.execute(_INSERT, (account_id, principal_user_id, agent, pinned["definition_sha"], pinned["tool_set_sha"],
                              pinned["commit_sha"], provider, pinned["model"], Jsonb(_storable(value)),
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


def _parked(conn: psycopg.Connection, settings: Mapping[str, Any]) -> str | None:
    """Why nothing may be claimed now, for the log; None when something may."""
    ceiling = _number(settings, DAILY_CEILING)
    if ceiling is None or _number(settings, RUNNING_CAP) is None or _number(settings, DAILY_LIMIT) is None:
        return f"{DAILY_CEILING}, {RUNNING_CAP} and {DAILY_LIMIT} must all be numbers"
    spent = float(conn.execute(_SPENT_TODAY, (list(_RUN_SPEND),)).fetchone()[0])
    return f"${spent:.4f} spent today, ceiling ${ceiling:.2f}" if spent >= ceiling else None


def _claim(conn: psycopg.Connection, settings: Mapping[str, Any]) -> _Claimed | Literal["parked", "idle"]:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_CLAIM_LOCK)
        if (why := _parked(conn, settings)) is not None:
            LOG.warning("AGENT_RUN parked: %s", why)
            return "parked"
        cur.execute(_CLAIM, {"lease": LEASE_S, "cap": _number(settings, RUNNING_CAP),
                             "default_limit": _number(settings, DAILY_LIMIT), "called_for": list(_RUN_SPEND)})
        row = cur.fetchone()
    return _Claimed(*row) if row else "idle"


@contextlib.contextmanager
def _keep_lease(connect: Callable[[], psycopg.Connection], run: _Claimed) -> Iterator[None]:
    """Renew between steps too: one model call may outlast the lease, and only a dead worker should lose its run. A
    connection of its own, since the run's may be inside a tool's transaction."""
    done = threading.Event()
    ceiling = time.monotonic() + HEARTBEAT_CEILING_S

    def beat() -> None:
        while not done.wait(LEASE_S / 3) and time.monotonic() < ceiling:
            try:
                with connect() as conn:
                    conn.execute(_RENEW, {"lease": LEASE_S, **run.held})
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
    """A reply that only hands in an answer, or the answer accepted: a shutdown waits for the step after (a refused
    answer has one), since releasing here would pay for it all again."""
    if step.kind == "model":
        return [call["name"] for call in output["tool_calls"]] == [terminator.NAME]
    return step.name == terminator.NAME and "markdown" in output


def _recorder(conn: psycopg.Connection, run: _Claimed, stop: threading.Event) -> Callable[[loop.Step], None]:
    """The loop's journal, and the between-steps checkpoint: the step lands only under a held lease, then a cancel or
    a shutdown stops the loop by raising through it."""
    def record(step: loop.Step) -> None:
        given = _storable(step.input)
        output = _storable(step.output)
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_RENEW_FOR_STEP, {"lease": LEASE_S, **run.held})
            held = cur.fetchone()
            if held is None:
                raise _LeaseLost
            cur.execute(_STEP, (run.id, run.account_id, run.attempts, step.n, step.kind, step.name,
                                hashlib.sha256(encode(given).encode()).hexdigest(), encode(output)[:SUMMARY_CHARS],
                                Jsonb({"input": given, "output": output}), step.llm_call_id, step.cost_usd,
                                step.duration_ms))
        if held[0]:
            raise _Cancelled
        if stop.is_set() and not _answer_in_hand(step, output):
            raise _Stopped
    return record


def _finish(conn: psycopg.Connection, run: _Claimed, status: Literal["succeeded", "failed", "cancelled"], *,
            stop_reason: str | None = None, error: str | None = None, answer: terminator.Answer | None = None
            ) -> WorkState:
    fields = {
        "status": status, "stop_reason": stop_reason, "error": error,
        "result_markdown": _storable(answer.markdown) if answer else None,
        "cited_property_ids": list(answer.cited_property_ids) if answer else [],
        # Names only: the bytes are #2073's to store, and the diary holds each file's facts.
        "files": [_no_nul(file.name) for file in answer.files] if answer else [],
        "proposals": Jsonb(_storable(answer.proposed_actions) if answer else []),
        "output": Jsonb(_storable(answer.output)) if answer and answer.output is not None else None,
    }
    written = conn.execute(_FINISH, {**fields, **run.held})
    return status if written.rowcount else "lost"


def _release(conn: psycopg.Connection, run: _Claimed) -> WorkState:
    row = conn.execute(_RELEASE, {"max": MAX_ATTEMPTS, "reason": "its worker shut down twice", **run.held}).fetchone()
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
        conn.execute(_STAMP, {**_pin(definition, run.provider), **run.held})
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
    except psycopg.Error:
        raise  # the ledger's trouble, not the run's: work_once leaves it to the lease and the attempt counter
    except Exception as exc:  # noqa: BLE001 - a run must end with its reason, not wait out a lease nobody will renew
        LOG.exception("AGENT_RUN failed run=%s", run.id)
        return _finish(conn, run, "failed", error=_reason(exc))
    if outcome.answer is None:
        return _finish(conn, run, "failed", stop_reason=outcome.stop_reason,
                       error=f"the run ended without an accepted answer: {outcome.stop_reason}")
    return _finish(conn, run, "succeeded", stop_reason=outcome.stop_reason, answer=outcome.answer)


def work_once(*, stop: threading.Event, providers: Mapping[str, CompletionProvider] | None = None,
              connect: Callable[[], psycopg.Connection] = db.connect) -> Worked:
    """Claim the oldest queued run whose account is under its running cap and its daily limit, and run it to its end, its release or a lost lease.
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
                # A deadlock, a timeout or a dead connection: nothing says the run itself is at fault, so write no
                # verdict. The heartbeat stops with this block, the lease lapses, and `_reap` counts the interruption.
                LOG.exception("AGENT_RUN left to its lease run=%s", claimed.id)
                state = "lost"
        LOG.info("AGENT_RUN done run=%s state=%s", claimed.id, state)
        return Worked(state, claimed.id)
