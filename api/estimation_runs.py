"""Persistence and trace machinery for /estimations endpoints.

Trace contract (TRACE_SCHEMA_VERSION = 2):

    {
      "version": 2,
      "summary": "<one-line human-readable summary>",
      "steps": [
        {
          "n": 1,                                  # 1-indexed monotonic
          "kind": "tool_call" | "computation" | "reasoning",
          "started_at": "ISO-8601 UTC ms",
          "duration_ms": int,
          "output_summary": {...},                 # NEVER full output
          # tool_call adds:    "tool", "input"
          # computation adds:  "label"
        },
        ...
      ]
    }

Version 2 is additive over version 1: agent-mode runs append a final
`computation` step labelled `'comparable_selection_summary'` whose
`output_summary` carries the per-iteration filter ladder + cohort
diffs + final picks. Deterministic-mode traces remain a 4-step flat
list; the only schema change at the deterministic level is the
`version` field.

Full tool outputs (lists of comparables, full distribution stats) live
in dedicated columns on estimation_runs (comparables_used etc.), not
in the trace. The trace stays bounded regardless of cohort size.

Bumping TRACE_SCHEMA_VERSION is a deliberate change — readers must
handle older versions.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from api.cursor import decode_cursor, encode_cursor
from api.dependencies import SYSTEM_ACCOUNT_ID, is_admin

from psycopg.types.json import Jsonb

from api import maps
from api import schemas as s
from location_data.claims_common import SUBJECT_LOCATION_JOIN
from toolkit import ComparableFilters, TargetSpec
from toolkit.rent_map import compute_reference_rent
from toolkit.subject import match_listing, subject_mf_facts

if TYPE_CHECKING:
    import psycopg

    from api.llm_client import LLMClient
    from scraper.sreality_client import SrealityClient

LOG = logging.getLogger(__name__)

TRACE_SCHEMA_VERSION = 2

# Filter defaults used by /estimations. Live values are read from
# `app_settings` (seeded by migration 052) so the operator can tune
# them via the Settings page without a redeploy. The constants below
# are fallbacks for when an app_settings row is missing or unreadable.
# Agent-mode runs use these as round-1 base filters; the agent
# overrides any of them per round through find_comparables_relaxed.
_DEFAULT_RADIUS_M = 1000
_DEFAULT_AREA_BAND_PCT = 0.20
_DEFAULT_DISPOSITION_MATCH = "exact"
_DEFAULT_LIFECYCLE = "active"
_DEFAULT_MIN_RESULTS = 5


def _default_max_age_days(estimate_kind: str) -> int:
    return 7 if estimate_kind == "rent" else 30


def _load_app_setting(
    conn: "psycopg.Connection", key: str, fallback: Any,
) -> Any:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM app_settings WHERE key = %s", (key,),
            )
            row = cur.fetchone()
    except Exception as exc:
        LOG.warning("app_settings lookup failed for %r: %s", key, exc)
        return fallback
    if row is None or row[0] is None:
        return fallback
    return row[0]


@dataclass(frozen=True)
class FilterDefaults:
    radius_m: int
    area_band_pct: float
    disposition_match: str
    lifecycle: str
    max_age_days_rent: int
    max_age_days_sale: int
    min_results: int

    def max_age_days_for(self, estimate_kind: str) -> int:
        if estimate_kind == "rent":
            return self.max_age_days_rent
        return self.max_age_days_sale


def load_filter_defaults(conn: "psycopg.Connection") -> FilterDefaults:
    return FilterDefaults(
        radius_m=int(_load_app_setting(conn, "default_radius_m", _DEFAULT_RADIUS_M)),
        area_band_pct=float(_load_app_setting(conn, "default_area_band_pct", _DEFAULT_AREA_BAND_PCT)),
        disposition_match=str(_load_app_setting(conn, "default_disposition_match", _DEFAULT_DISPOSITION_MATCH)),
        lifecycle=str(_load_app_setting(conn, "default_lifecycle", _DEFAULT_LIFECYCLE)),
        max_age_days_rent=int(_load_app_setting(conn, "default_max_age_days_rent", 7)),
        max_age_days_sale=int(_load_app_setting(conn, "default_max_age_days_sale", 30)),
        min_results=int(_load_app_setting(conn, "default_min_results", _DEFAULT_MIN_RESULTS)),
    )


class StepHandle:
    """Returned by recorder context managers; caller sets the summary."""

    _UNSET = object()

    def __init__(self) -> None:
        self.summary: dict[str, Any] = {}
        self.full_output: Any = StepHandle._UNSET

    def set_summary(self, summary: dict[str, Any]) -> None:
        self.summary = summary

    def set_full_output(self, full_output: Any) -> None:
        """Capture the unbounded tool output for the trace_payloads side-table.

        Trace JSONB stores only `output_summary` per architectural rule #9;
        full payloads land in `estimation_trace_payloads` keyed on
        (estimation_run_id, step_n) for click-to-expand drill-down.
        """
        self.full_output = full_output


class TraceRecorder:
    """Captures tool calls and computations into the trace format."""

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []
        self._payloads: list[tuple[int, Any]] = []
        self._n = 0

    @contextlib.contextmanager
    def tool_call(
        self, tool: str, input: dict[str, Any]
    ) -> Iterator[StepHandle]:
        started_at = datetime.now(timezone.utc)
        mono_start = time.monotonic()
        handle = StepHandle()
        try:
            yield handle
        finally:
            self._append(
                kind="tool_call",
                started_at=started_at,
                duration_ms=_ms_since(mono_start),
                fields={"tool": tool, "input": input},
                handle=handle,
            )

    @contextlib.contextmanager
    def computation(self, label: str) -> Iterator[StepHandle]:
        started_at = datetime.now(timezone.utc)
        mono_start = time.monotonic()
        handle = StepHandle()
        try:
            yield handle
        finally:
            self._append(
                kind="computation",
                started_at=started_at,
                duration_ms=_ms_since(mono_start),
                fields={"label": label},
                handle=handle,
            )

    @contextlib.contextmanager
    def reasoning(self) -> Iterator[StepHandle]:
        """Capture one turn of plain-text reasoning emitted by the agent.

        The handle's summary should be set to
            {"text": "<truncated 800 chars>",
             "tool_calls_queued": [<tool names>],
             "provider": "<provider name>"}
        The agent loop owns those fields; the recorder only enforces
        the kind / monotonic step number / timing.
        """
        started_at = datetime.now(timezone.utc)
        mono_start = time.monotonic()
        handle = StepHandle()
        try:
            yield handle
        finally:
            self._append(
                kind="reasoning",
                started_at=started_at,
                duration_ms=_ms_since(mono_start),
                fields={},
                handle=handle,
            )

    def to_dict(self, summary: str) -> dict[str, Any]:
        return {
            "version": TRACE_SCHEMA_VERSION,
            "summary": summary,
            "steps": list(self._steps),
        }

    def iter_payloads(self) -> list[tuple[int, Any]]:
        """Return the (step_n, full_output) pairs captured during the run."""
        return list(self._payloads)

    def _append(
        self,
        *,
        kind: str,
        started_at: datetime,
        duration_ms: int,
        fields: dict[str, Any],
        handle: StepHandle,
    ) -> None:
        self._n += 1
        step: dict[str, Any] = {
            "n": self._n,
            "kind": kind,
            "started_at": started_at.isoformat(timespec="milliseconds"),
            "duration_ms": duration_ms,
            **fields,
            "output_summary": handle.summary,
        }
        self._steps.append(step)
        if handle.full_output is not StepHandle._UNSET:
            self._payloads.append((self._n, handle.full_output))


class _NullStepHandle:
    def set_summary(self, summary: dict[str, Any]) -> None:
        return None

    def set_full_output(self, full_output: Any) -> None:
        return None


class _NullTraceRecorder:
    """No-op recorder used when estimate_yield is called without one.

    Lets estimate_yield use the same `with rec.tool_call(...)` form
    regardless of whether a real recorder was passed, with effectively
    zero overhead and no behavioural change for existing callers.
    """

    @contextlib.contextmanager
    def tool_call(
        self, tool: str, input: dict[str, Any]
    ) -> Iterator[_NullStepHandle]:
        yield _NULL_HANDLE

    @contextlib.contextmanager
    def computation(self, label: str) -> Iterator[_NullStepHandle]:
        yield _NULL_HANDLE

    @contextlib.contextmanager
    def reasoning(self) -> Iterator[_NullStepHandle]:
        yield _NULL_HANDLE

    def to_dict(self, summary: str) -> dict[str, Any]:
        return {
            "version": TRACE_SCHEMA_VERSION,
            "summary": summary,
            "steps": [],
        }

    def iter_payloads(self) -> list[tuple[int, Any]]:
        return []


_NULL_HANDLE = _NullStepHandle()
NULL_RECORDER: Any = _NullTraceRecorder()


def flush_trace_payloads(
    conn: "psycopg.Connection",
    run_id: int,
    recorder: TraceRecorder,
) -> None:
    """Persist the recorder's accumulated tool-call full outputs.

    Called after the parent estimation_runs row exists. ON CONFLICT
    DO NOTHING so a retry path that double-flushes is a no-op.

    Best-effort: a failure here must never bubble up. The run row is
    already committed at the call site; losing the drill-down side-
    table for a step is a UX degradation, not a request failure.
    """
    rows = recorder.iter_payloads()
    if not rows:
        return
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO estimation_trace_payloads "
                "(estimation_run_id, step_n, full_output) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (estimation_run_id, step_n) DO NOTHING",
                [(run_id, step_n, Jsonb(payload)) for step_n, payload in rows],
            )
    except Exception as exc:
        LOG.warning(
            "flush_trace_payloads failed for run %s: %s: %s",
            run_id, type(exc).__name__, exc,
        )


def _ms_since(mono_start: float) -> int:
    return int((time.monotonic() - mono_start) * 1000)


# --- create / get / list endpoints -----------------------------------------

_RUN_COLUMNS: tuple[str, ...] = (
    "id", "created_at", "account_id", "source", "mode", "status",
    "estimate_kind",
    "input_url", "input_sreality_id", "input_listing_id", "input_spec",
    "input_purchase_price_czk",
    "estimated_monthly_rent_czk", "rent_p25_czk", "rent_p75_czk",
    "estimated_sale_price_czk", "sale_p25_czk", "sale_p75_czk",
    "gross_yield_pct", "confidence",
    "comparables_used", "comparables_excluded",
    "trace", "warnings", "error_message",
    "parent_run_id", "rerun_reason",
    "special_instructions", "contextual_text",
    "skill_name", "skill_version",
    "scenario",
    "reference_rent",
)

# input_url is read-only: the historic URL-submitted runs keep showing theirs, and no
# submit carries one.
_INSERT_COLUMNS: tuple[str, ...] = tuple(
    c for c in _RUN_COLUMNS if c not in ("id", "created_at", "input_url")
)

_COST_TOTAL_SUBSELECT = (
    "coalesce("
    "(SELECT sum(cost_usd) FROM llm_calls WHERE estimation_run_id = er.id), "
    "0)::float AS cost_usd_total"
)
# Boolean: is there at least one operator-supplied feedback row on
# this run? Drives the "Feedback" button enable/disable on the
# /estimations list (slice B follow-up).
_HAS_FEEDBACK_SUBSELECT = (
    "EXISTS("
    "SELECT 1 FROM estimation_feedback WHERE estimation_run_id = er.id"
    ") AS has_feedback"
)
# The /estimations list's place label: the resolver's municipality at the subject's place
# (`SUBJECT_LOCATION_JOIN`, ruling R7: its property's location ad, else its own row); NULL
# for a raw-spec run and for an ad the resolver has not placed.
_LOCALITY_DISPLAY_EXPR = "ll.obec_name AS locality_display"
# The run's subject ad, by which the SPA routes a run to its ad's page. The detail read runs
# on the tenant connection, where `listings` (RLS, no policy) returns no row, so it joins
# the owner view; the list runs on the service-role connection and joins `listings`.
_SUBJECT_AD_PROJECTION = (
    "l.source AS subject_source, l.source_id_native AS subject_source_id_native"
)
_RUN_PROJECTION = (
    ", ".join(f"er.{c}" for c in _RUN_COLUMNS)
    + ", " + _COST_TOTAL_SUBSELECT
    + ", " + _HAS_FEEDBACK_SUBSELECT
    + ", " + _SUBJECT_AD_PROJECTION
)
_RUN_FROM = "estimation_runs er LEFT JOIN listings_public l ON l.id = er.input_listing_id"
_LIST_PROJECTION = _RUN_PROJECTION + ", " + _LOCALITY_DISPLAY_EXPR
_LIST_FROM = (
    "estimation_runs er "
    "LEFT JOIN listings l ON l.id = er.input_listing_id "
    + SUBJECT_LOCATION_JOIN
)
_RUN_COLUMNS_OUT: tuple[str, ...] = _RUN_COLUMNS + (
    "cost_usd_total", "has_feedback", "subject_source", "subject_source_id_native",
)
_LIST_COLUMNS_OUT: tuple[str, ...] = _RUN_COLUMNS_OUT + ("locality_display",)


@dataclass
class _Resolution:
    """The subject a CreateEstimationIn body names, resolved once at submit and reused by
    every persistence path and the job lane's snapshot: the target spec, and for an ad we
    hold its ids, its (source, native id) and the facts the yield derivation reads."""
    target_spec: dict[str, Any]
    input_listing_id: int | None = None
    input_sreality_id: int | None = None
    subject_source: str | None = None
    subject_source_id_native: str | None = None
    subject_listing_price_czk: int | None = None
    subject_listing_category_type: str | None = None
    yield_input_derivation: dict[str, Any] | None = None


JOB_LANE_SETTING = "estimation_job_lane_enabled"


def _setting_bool(value: Any) -> bool:
    """An app_settings flag's value: a JSON bool as is, a string by its spelling, else truthiness."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _job_lane_enabled(conn: "psycopg.Connection") -> bool:
    """True when POST /estimations should hand execution to the realtime
    worker's estimation lane (Amendment A10) instead of running the heavy work
    in-process. Reads app_settings.estimation_job_lane_enabled — an absent key
    (or any read error) means False, so the lane ships dark and a settings
    hiccup never strands a submit."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM app_settings WHERE key = %s",
                (JOB_LANE_SETTING,),
            )
            row = cur.fetchone()
    except Exception:  # noqa: BLE001 - a settings read must never fail a submit
        LOG.warning("job-lane flag read failed; treating as disabled")
        return False
    return row is not None and _setting_bool(row[0])


def _job_payload(
    body: s.CreateEstimationIn, resolution: _Resolution,
) -> dict[str, Any]:
    """Snapshot the execution inputs for the worker job lane. The run row IS the
    job (no new table): {body, resolution} is everything _execute_estimation_run
    needs, captured AFTER yield-input derivation so the worker re-runs nothing."""
    return {"body": body.model_dump(mode="json"), "resolution": asdict(resolution)}


def execute_pending_run(
    conn: "psycopg.Connection",
    sreality_client: "SrealityClient",
    llm_client: "LLMClient",
    run_id: int,
    payload: dict[str, Any],
) -> None:
    """Execute a `pending` estimation_runs row claimed by the realtime worker's
    estimation lane. Rehydrates the job_payload snapshot, rebuilds target/filters,
    and runs the SAME heavy path the in-process BackgroundTask runs — only the
    executor moved off the request threadpool (Amendment A10). Fully
    self-contained: any failure flips the row to 'failed', so a claimed row can
    never get stuck. Never raises."""
    try:
        body = s.CreateEstimationIn(**payload["body"])
        resolution = _Resolution(**payload["resolution"])
    except Exception as exc:  # noqa: BLE001
        LOG.exception("estimation lane: invalid job_payload for run %s", run_id)
        _safe_mark_failed(
            conn, run_id,
            f"job payload invalid: {type(exc).__name__}: {exc}"[:1000],
        )
        return
    try:
        target = _build_target(resolution.target_spec, resolution.input_listing_id)
        filters = _build_filters(body, load_filter_defaults(conn))
    except Exception as exc:  # noqa: BLE001
        LOG.exception(
            "estimation lane: target/filters build failed for run %s", run_id,
        )
        _safe_mark_failed(
            conn, run_id,
            f"target build failed: {type(exc).__name__}: {exc}"[:1000],
        )
        return
    try:
        _execute_estimation_run(
            conn, sreality_client, llm_client, run_id,
            body=body, resolution=resolution, target=target, filters=filters,
        )
    except Exception as exc:  # noqa: BLE001 - the lane must not crash on one run
        LOG.exception("estimation lane: run %s crashed", run_id)
        _safe_mark_failed(
            conn, run_id, f"lane crash: {type(exc).__name__}: {exc}"[:1000],
        )


# --- Wave 1 metering + atomic submit-time gates (Phase 1 items J + A9) --------
#
# The paid agent estimation is metered per SUCCESSFUL run against a MONTHLY quota
# (operator decision 2026-07-22 — run-count, not USD; free = 3/mo, trial = 10).
# Deterministic runs stay free + ungated. Only a real, non-admin tenant sending
# mode='agent' is metered; admin / SYSTEM callers (operator, ClickUp, tests)
# bypass everything, exactly like require_entitlement.
#
# The spine is atomic (A9 — check-then-act is TOCTOU-racy over the tx pooler, the
# mig-279 lesson): idempotency + single-in-flight ride a UNIQUE partial index +
# ON CONFLICT (mig 355); the monthly budget + concurrency cap ride an atomic
# INSERT ... SELECT WHERE (count) < limit. A cheap pre-INSERT check short-circuits
# the common duplicate / over-quota case.

AGENT_ESTIMATION_ACTION = "agent_estimation"
BUDGET_ENABLED_SETTING = "estimation_budget_enabled"   # absent => enabled (fail-closed)
CONCURRENCY_CAP_SETTING = "agent_estimation_concurrency_cap"
CONCURRENCY_CAP_DEFAULT = 3


@dataclass
class _MeterDecision:
    idempotency_key: str
    quota: int
    concurrency_cap: int
    short_circuit_run: dict[str, Any] | None = None


def _is_privileged(claims: dict[str, Any] | None) -> bool:
    """Admin callers bypass metering (operator, ClickUp, internal + tests),
    mirroring require_entitlement's bypass."""
    if claims is None:
        return True   # internal caller (ClickUp / agent / test) — never metered
    return is_admin(claims)


def _budget_enabled(conn: "psycopg.Connection") -> bool:
    return _setting_bool(_load_app_setting(conn, BUDGET_ENABLED_SETTING, True))


def _idempotency_key(body: s.CreateEstimationIn, resolution: _Resolution) -> str:
    """A stable per-subject key: an ad's (source, native id), whichever handle named it; a
    spec is hashed (spec is blocked for metered callers, so that arm is defensive only)."""
    if resolution.subject_source is not None:
        return f"ad:{resolution.subject_source}:{resolution.subject_source_id_native}"
    assert body.spec is not None
    blob = json.dumps(body.spec.model_dump(mode="json"), sort_keys=True)
    return "spec:" + hashlib.sha256(blob.encode()).hexdigest()[:32]


def _resolve_entitlement(
    conn: "psycopg.Connection", account_id: str,
) -> tuple[str, bool, int]:
    """(status, estimations_agenda_ok, monthly_agent_quota) for an account.
    Honors an active trial (status='trialing' + unexpired → the plan's trial
    quota). No entitlements row (mig 286 signup makes none) → the default plan,
    status 'active'."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.status, (p.agendas->>'estimations') = 'true', "
            "  CASE WHEN e.status = 'trialing' AND (e.current_period_end IS NULL "
            "            OR e.current_period_end > now()) "
            "       THEN coalesce(p.trial_agent_estimations_monthly_quota, 0) "
            "       ELSE coalesce(p.agent_estimations_monthly_quota, 0) END "
            "FROM entitlements e JOIN plans p ON p.key = e.plan "
            "WHERE e.account_id = %s",
            (account_id,),
        )
        row = cur.fetchone()
        if row is not None:
            return str(row[0]), bool(row[1]), int(row[2])
        cur.execute(
            "SELECT (agendas->>'estimations') = 'true', "
            "  coalesce(agent_estimations_monthly_quota, 0) "
            "FROM plans WHERE is_default LIMIT 1",
        )
        d = cur.fetchone()
    if d is None:
        return "active", False, 0
    return "active", bool(d[0]), int(d[1])


def _count_agent_runs_this_month(
    conn: "psycopg.Connection", account_id: str,
) -> int:
    """Non-failed agent runs this calendar month — the budget count. Counting
    pending+running+success (not just success) bounds run CREATION, and a failed
    run frees the slot (matches 'absorb the count on failure')."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM estimation_runs "
            "WHERE account_id = %s AND mode = 'agent' AND status <> 'failed' "
            "  AND created_at >= date_trunc('month', now())",
            (account_id,),
        )
        return int(cur.fetchone()[0])


def _count_inflight_agent_runs(
    conn: "psycopg.Connection", account_id: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM estimation_runs "
            "WHERE account_id = %s AND mode = 'agent' "
            "  AND status IN ('pending', 'running')",
            (account_id,),
        )
        return int(cur.fetchone()[0])


def _find_inflight_run(
    conn: "psycopg.Connection", account_id: str, idem_key: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM estimation_runs "
            "WHERE account_id = %s AND idempotency_key = %s "
            "  AND status IN ('pending', 'running') "
            "ORDER BY created_at DESC LIMIT 1",
            (account_id, idem_key),
        )
        row = cur.fetchone()
    return _fetch_run(conn, int(row[0])) if row else None


def _concurrency_cap(conn: "psycopg.Connection") -> int:
    try:
        return max(1, int(_load_app_setting(
            conn, CONCURRENCY_CAP_SETTING, CONCURRENCY_CAP_DEFAULT)))
    except (TypeError, ValueError):
        return CONCURRENCY_CAP_DEFAULT


def _prepare_metered_submit(
    conn: "psycopg.Connection",
    claims: dict[str, Any] | None,
    body: s.CreateEstimationIn,
    account_id: str,
    resolution: _Resolution,
) -> _MeterDecision | None:
    """Pre-INSERT submit gates for a metered (agent, non-admin) submit. Returns
    None for ungated callers; a _MeterDecision (carrying an existing in-flight
    run to short-circuit, or the quota/cap for the atomic INSERT) when cleared;
    raises HTTPException (403 not entitled / raw spec, 429 over quota / too many
    in flight) otherwise."""
    if (
        _is_privileged(claims)
        or body.mode != "agent"
        or account_id == SYSTEM_ACCOUNT_ID
        or not _budget_enabled(conn)
    ):
        return None
    if body.spec is not None or body.spec_overrides is not None:
        raise HTTPException(
            status_code=403,
            detail="Custom spec is not permitted for this caller",
        )
    status, estimations_ok, quota = _resolve_entitlement(conn, account_id)
    if status == "canceled" or not estimations_ok:
        raise HTTPException(
            status_code=403, detail="Your plan does not include agent estimations",
        )
    idem_key = _idempotency_key(body, resolution)
    cap = _concurrency_cap(conn)
    existing = _find_inflight_run(conn, account_id, idem_key)
    if existing is not None:
        # Duplicate submit while one is already in flight — return the existing
        # run instead of charging again (and don't re-enqueue).
        return _MeterDecision(idem_key, quota, cap, short_circuit_run=existing)
    if _count_agent_runs_this_month(conn, account_id) >= quota:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly agent-estimation limit reached ({quota}/mo)",
        )
    if _count_inflight_agent_runs(conn, account_id) >= cap:
        raise HTTPException(
            status_code=429,
            detail="Too many estimations in progress; try again shortly",
        )
    return _MeterDecision(idem_key, quota, cap)


def _record_usage(conn: "psycopg.Connection", run_id: int) -> None:
    """Append a usage_ledger row at a metered agent run's terminal SUCCESS —
    cost = the run's llm_calls sum (mig 355). Best-effort: a ledger hiccup must
    not fail the run (estimation_runs + llm_calls stay the source of truth).
    Reads the run's own account_id/mode so callers needn't thread them."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT account_id, mode FROM estimation_runs WHERE id = %s",
                (run_id,),
            )
            row = cur.fetchone()
        if not row:
            return
        account_id, mode = row
        if mode != "agent" or account_id is None or str(account_id) == SYSTEM_ACCOUNT_ID:
            return
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO usage_ledger "
                "  (account_id, action, cost_usd, estimation_run_id) "
                "SELECT %(aid)s, %(act)s, "
                "  (SELECT sum(cost_usd) FROM llm_calls WHERE estimation_run_id = %(rid)s), "
                "  %(rid)s",
                {"aid": account_id, "act": AGENT_ESTIMATION_ACTION, "rid": run_id},
            )
    except Exception:  # noqa: BLE001 - metering must never fail a completed run
        LOG.exception("usage_ledger write failed for run %s", run_id)


def create_estimation_run(
    conn: "psycopg.Connection",
    sreality_client: "SrealityClient",
    llm_client: "LLMClient",
    body: s.CreateEstimationIn,
    background_tasks: Any | None = None,
    # Stays optional where every other write path is now required: this runs on
    # the SERVICE-ROLE connection and building_runs' internal child-run caller
    # has no request identity — estimation_runs genuinely carries the SYSTEM arm
    # (migration 291), which the curation/pipeline tables do not.
    account_id: str | None = None,
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST /estimations: resolve the subject, INSERT a pending row, schedule the
    heavy work as a BackgroundTask, return the row immediately.

    The handler completes in well under a second (the subject read + INSERT)
    instead of waiting 3–10 s for the full estimate. The browser navigates to
    the detail page, which polls until the row reaches a terminal status.

    A body naming an ad we do not hold, or one with no location yet, is a 422
    before any row. Hard failures during setup (target / filter build, skill
    lookup) are persisted inline with `status='failed'` — those rows have no
    work to background.

    When `background_tasks` is None (tests that want synchronous
    behaviour, ClickUp / agent callers that want the row populated
    before they read it back), the heavy work runs inline on the
    request thread.

    `account_id` is the caller's account, resolved from the verified JWT by
    the route handler (Wave 1 W1-1) — hand-threaded because the run persists
    on the service-role connection, which has no JWT/RLS context to read it
    from. Falls back to the platform SYSTEM account for an internal caller that
    has no JWT (the column no longer relies on its own DEFAULT once it's named
    explicitly in every INSERT).
    """
    account_id = account_id or SYSTEM_ACCOUNT_ID

    # The subject first, so a body naming an ad we cannot place is refused before any
    # gate counts it.
    resolution = _resolve_input(conn, body)

    # Submit-time gates BEFORE the INSERT (entitlement + monthly budget +
    # concurrency + idempotency). Ungated for admin/ClickUp/deterministic;
    # a rejected metered submit raises HTTPException here.
    meter = _prepare_metered_submit(conn, claims, body, account_id, resolution)
    if meter is not None and meter.short_circuit_run is not None:
        return meter.short_circuit_run

    try:
        target = _build_target(resolution.target_spec, resolution.input_listing_id)
        filters = _build_filters(body, load_filter_defaults(conn))
    except Exception as exc:
        LOG.warning("target/filters build failed: %s", exc)
        return _persist_failed_run(
            conn, body=body, resolution=resolution, recorder=TraceRecorder(),
            error_msg=f"target build failed: {type(exc).__name__}: {exc}"[:1000],
            account_id=account_id,
        )

    purchase, expected_rent, derivation = _derive_yield_inputs(body, resolution)
    body.purchase_price_czk = purchase
    body.expected_monthly_rent_czk = expected_rent
    resolution.yield_input_derivation = derivation

    lane = _job_lane_enabled(conn)
    skill_obj = None
    if body.mode == "agent":
        from api.skills import SkillNotFound, load_skill
        try:
            skill_obj = load_skill(conn, body.skill)
        except SkillNotFound:
            return _persist_failed_run(
                conn, body=body, resolution=resolution,
                recorder=TraceRecorder(),
                error_msg=f"unknown skill: {body.skill!r}",
                account_id=account_id,
            )
        # Agent runs normally INSERT 'running' so per-turn llm_calls attribute
        # while the loop runs in-process. On the job lane the worker stamps
        # 'running' + claimed_at at claim time, so the row starts 'pending' and
        # the lane's claim (WHERE status='pending') can see it.
        initial_status = "pending" if lane else "running"
    else:
        initial_status = "pending"

    run_id = _insert_run(
        conn,
        account_id=account_id,
        source=body.source,
        mode=body.mode,
        status=initial_status,
        estimate_kind=body.estimate_kind,
        input_sreality_id=resolution.input_sreality_id,
        input_listing_id=resolution.input_listing_id,
        input_spec=resolution.target_spec,
        input_purchase_price_czk=body.purchase_price_czk,
        estimated_monthly_rent_czk=None,
        rent_p25_czk=None,
        rent_p75_czk=None,
        estimated_sale_price_czk=None,
        sale_p25_czk=None,
        sale_p75_czk=None,
        gross_yield_pct=None,
        confidence=None,
        comparables_used=None,
        comparables_excluded=None,
        trace=TraceRecorder().to_dict("pending"),
        warnings=None,
        error_message=None,
        parent_run_id=body.parent_run_id,
        rerun_reason=body.rerun_reason,
        special_instructions=body.special_instructions,
        contextual_text=body.contextual_text,
        skill_name=skill_obj.name if skill_obj is not None else None,
        skill_version=skill_obj.version if skill_obj is not None else None,
        job_payload=_job_payload(body, resolution) if lane else None,
        idempotency_key=meter.idempotency_key if meter else None,
        gate=meter,
    )

    if run_id is None:
        # The atomic gate rejected the INSERT (a concurrent submit crossed the
        # quota / cap between the pre-INSERT check and here) or an idempotent
        # duplicate raced in — return the winner if one exists, else 429.
        assert meter is not None
        existing = _find_inflight_run(conn, account_id, meter.idempotency_key)
        if existing is not None:
            return existing
        raise HTTPException(
            status_code=429,
            detail=f"Monthly agent-estimation limit reached ({meter.quota}/mo)",
        )

    if lane:
        # Execution drains off the request process onto the realtime worker's
        # estimation lane (Amendment A10): a 240 s agent run no longer pins a
        # Starlette threadpool token, and a deploy SIGTERM no longer kills it
        # mid-flight with no ledger row. The worker claims this pending row and
        # runs the same heavy path; the client polls GET /estimations/{id}.
        return _fetch_run(conn, run_id) or {}

    if background_tasks is not None:
        background_tasks.add_task(
            _execute_estimation_run_background,
            run_id=run_id,
            body=body,
            resolution=resolution,
        )
        return _fetch_run(conn, run_id) or {}

    _execute_estimation_run(
        conn, sreality_client, llm_client, run_id,
        body=body, resolution=resolution, target=target, filters=filters,
    )
    return _fetch_run(conn, run_id) or {}


def _execute_estimation_run_background(
    *,
    run_id: int,
    body: s.CreateEstimationIn,
    resolution: _Resolution,
) -> None:
    """Background-task entry: opens its own connection + clients and
    runs the heavy work. Any uncaught exception flips the row to
    'failed' so it can't get stuck.
    """
    from api import dependencies as deps

    try:
        with deps.open_background_conn() as conn:
            from api.llm_client import LLMClient
            sreality_client = deps.get_sreality_client()
            llm_client = LLMClient(conn, providers=deps.get_providers())
            try:
                target = _build_target(resolution.target_spec, resolution.input_listing_id)
                filters = _build_filters(body, load_filter_defaults(conn))
            except Exception as exc:
                LOG.exception("background target/filters build failed for run %s", run_id)
                _safe_mark_failed(
                    conn, run_id,
                    f"target build failed: {type(exc).__name__}: {exc}"[:1000],
                )
                return
            _execute_estimation_run(
                conn, sreality_client, llm_client, run_id,
                body=body, resolution=resolution,
                target=target, filters=filters,
            )
    except Exception as exc:
        LOG.exception("background estimation run %s crashed", run_id)
        # Last-ditch: open a fresh connection so we can still mark failed.
        try:
            with deps.open_background_conn() as conn:
                _safe_mark_failed(
                    conn, run_id,
                    f"background crash: {type(exc).__name__}: {exc}"[:1000],
                )
        except Exception:
            LOG.exception(
                "failed to mark run %s failed after background crash", run_id,
            )


def _safe_mark_failed(
    conn: "psycopg.Connection", run_id: int, error_msg: str,
) -> None:
    """UPDATE the row to status='failed' with an error_message.

    Used as a last-ditch path when the background task can't otherwise
    record the failure (e.g. it crashed before the per-step trace was
    finalised). Best-effort — never raises.
    """
    try:
        _update_run_terminal(
            conn, run_id, status="failed", error_message=error_msg,
        )
    except Exception:
        LOG.exception("_safe_mark_failed failed for run %s", run_id)


def _execute_estimation_run(
    conn: "psycopg.Connection",
    sreality_client: "SrealityClient",
    llm_client: "LLMClient",
    run_id: int,
    *,
    body: s.CreateEstimationIn,
    resolution: _Resolution,
    target: TargetSpec,
    filters: ComparableFilters,
) -> None:
    """Run the heavy estimation work for an already-INSERTed row.

    Deterministic mode: runs estimate_yield, UPDATEs to terminal.
    Agent mode: delegates to the existing agent dispatch which UPDATEs
    the same row in place.
    """
    if body.mode == "agent":
        _run_agent_path(
            conn, sreality_client, llm_client, run_id, body,
            resolution=resolution, target=target, filters=filters,
        )
        return

    from api.estimate_yield import estimate_yield

    recorder = TraceRecorder()
    _record_yield_input_derivation(recorder, resolution)
    try:
        result = estimate_yield(
            conn, target, filters, body.purchase_price_czk,
            estimate_kind=body.estimate_kind,
            expected_monthly_rent_czk=body.expected_monthly_rent_czk,
            trace_recorder=recorder,
        )
    except Exception as exc:
        LOG.warning("estimate_yield failed for run %s: %s", run_id, exc)
        error_msg = f"{type(exc).__name__}: {exc}"[:1000]
        trace = recorder.to_dict(f"failed: {error_msg.split(':', 1)[0]}")
        _update_run_terminal(
            conn, run_id,
            status="failed",
            trace=trace,
            error_message=error_msg,
        )
        flush_trace_payloads(conn, run_id, recorder)
        return

    d = result["data"]
    reference_rent = _reference_rent_for_run(conn, recorder, resolution, target, body)
    summary_text = _summary_line(d, filters.radius_m)
    trace = recorder.to_dict(summary_text)
    _update_run_terminal(
        conn, run_id,
        status="success",
        reference_rent=reference_rent,
        estimated_monthly_rent_czk=d.get("estimated_monthly_rent_czk"),
        rent_p25_czk=d.get("rent_p25_czk"),
        rent_p75_czk=d.get("rent_p75_czk"),
        estimated_sale_price_czk=d.get("estimated_sale_price_czk"),
        sale_p25_czk=d.get("sale_p25_czk"),
        sale_p75_czk=d.get("sale_p75_czk"),
        gross_yield_pct=d.get("gross_yield_pct"),
        confidence=d.get("confidence"),
        comparables_used=d.get("comparables_used"),
        trace=trace,
        warnings=d.get("warnings") or None,
    )
    flush_trace_payloads(conn, run_id, recorder)


def get_estimation_run(
    conn: "psycopg.Connection", run_id: int
) -> dict[str, Any] | None:
    return _fetch_run(conn, run_id)


def estimation_run_visible(conn: "psycopg.Connection", run_id: int) -> bool:
    """Can `conn` see the run? On a tenant conn this IS the RLS ownership gate.

    Strict where `_fetch_run` is lenient: that one degrades a failed SELECT to a
    stub row so the detail page still renders, which would read as "visible" here —
    and this gate sits in front of service-role writes and LLM spend. Errors raise.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM estimation_runs WHERE id = %s", (run_id,))
        return cur.fetchone() is not None


def estimation_run_owned(conn: "psycopg.Connection", run_id: int) -> bool:
    """Is the run one of the caller's OWN accounts' — not merely visible?

    `estimation_run_visible` also passes migration 291's shared SYSTEM arm, where
    every pre-tenancy run sits: a child row a non-admin writes there is stamped
    SYSTEM (migration 292) and so readable by every account. Errors raise.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM estimation_runs "
            "WHERE id = %s AND account_id IN (SELECT current_account_ids())",
            (run_id,),
        )
        return cur.fetchone() is not None


def update_scenario(
    conn: "psycopg.Connection",
    run_id: int,
    *,
    rent_czk: float | None,
    fond_per_m2_czk: float | None,
    price_czk: float | None,
    renovation_czk: float | None = None,
) -> dict[str, Any] | None:
    """PATCH the operator-tunable yield scenario on an estimation_runs row.

    A body with every number None clears the column back to NULL
    (re-render defaults). Otherwise we store the supplied subset plus
    an `updated_at` stamp so concurrent edits between the SPA and the
    Chrome extension can be reasoned about. `renovation_czk` is a flat
    one-off renovation budget added to the price to form the total
    acquisition cost (the yield denominator).

    Returns the refreshed row, or None when the run id is unknown.
    """
    has_any = any(
        v is not None
        for v in (rent_czk, fond_per_m2_czk, price_czk, renovation_czk)
    )
    if has_any:
        payload: dict[str, Any] = {
            "rent_czk": rent_czk,
            "fond_per_m2_czk": fond_per_m2_czk,
            "price_czk": price_czk,
            "renovation_czk": renovation_czk,
            "updated_at": datetime.now(timezone.utc).isoformat(
                timespec="milliseconds",
            ),
        }
        scenario_value: Any = Jsonb(payload)
    else:
        scenario_value = None
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE estimation_runs SET scenario = %s "
            "WHERE id = %s RETURNING id",
            (scenario_value, run_id),
        )
        if cur.fetchone() is None:
            return None
    return _fetch_run(conn, run_id)


def sweep_stuck_runs(
    conn: "psycopg.Connection",
    *,
    older_than_minutes: int = 10,
) -> int:
    """Mark any estimation_runs in a non-terminal status older than the
    cutoff as 'failed'. Returns the number of rows updated.

    Called from the FastAPI lifespan startup hook to recover rows orphaned by a
    server restart mid-background-task, AND periodically by the realtime worker's
    estimation lane so a run orphaned mid-execution (worker crash) frees its
    concurrency/idempotency slot instead of polling a corpse forever (A10).

    `running` rows are keyed off coalesce(claimed_at, created_at) — the worker
    stamps claimed_at when it starts a run, so a legitimately long agent run is
    timed from when execution BEGAN, not from when it was queued behind a
    backlog. Legacy background-task runs have claimed_at NULL and fall back to
    created_at (identical to the pre-lane behavior). `pending` rows have no claim
    time, so they key off created_at as before.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "UPDATE estimation_runs "
            "SET status = 'failed', "
            "    error_message = coalesce(error_message, "
            "        'interrupted by server restart') "
            "WHERE status IN ('pending', 'running') "
            "  AND coalesce(claimed_at, created_at) "
            "      < now() - make_interval(mins => %s) "
            "RETURNING id",
            (older_than_minutes,),
        )
        return len(cur.fetchall())


def get_trace_payload(
    conn: "psycopg.Connection", run_id: int, step_n: int,
) -> dict[str, Any] | None:
    """Fetch one estimation_trace_payloads row, or None if absent.

    Returns `{step_n, full_output, captured_at}`. Drives the
    click-to-expand drill-down on tool_call steps in the timeline.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT step_n, full_output, captured_at "
            "FROM estimation_trace_payloads "
            "WHERE estimation_run_id = %s AND step_n = %s",
            (run_id, step_n),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "step_n": row[0],
        "full_output": row[1],
        "captured_at": (
            row[2].isoformat(timespec="milliseconds")
            if row[2] is not None
            else None
        ),
    }


def list_estimation_runs(
    conn: "psycopg.Connection",
    *,
    source: str | None = None,
    status: str | None = None,
    sreality_id: int | None = None,
    listing_ids: list[int] | None = None,
    limit: int = 50,
    offset: int = 0,
    cursor: str | None = None,
    account_ids: list[str],
) -> dict[str, Any]:
    """Newest-first list, KEYSET-paginated on (created_at, id) DESC.

    `cursor` is the opaque token returned as `next_cursor`; pass it for the
    next page. `offset` remains for any legacy caller (used only when no
    cursor is given). `total` is computed once — on the first page — and is
    null on cursor'd pages (it doesn't change as you scroll).

    `account_ids` is REQUIRED and has no default on purpose: a caller that
    forgets it raises TypeError at the call site rather than silently reading
    across every account (this endpoint had no account predicate at all until
    the subject-identity cutover, and ran on the RLS-bypassing service-role
    connection).
    """
    where: list[str] = []
    params: dict[str, Any] = {}
    if not account_ids:
        raise ValueError("account_ids must be a non-empty read scope")
    # Mirrors the estimation_runs_tenant_read policy (migration 291) instead of
    # restating tenancy: the caller's account(s) OR SYSTEM, which owns every
    # pre-multi-tenancy run and every watchdog-produced run (api/notifications.py
    # writes no account_id, so migration 291's DEFAULT applies). The policy's
    # third arm (account_id IS NULL AND is_platform_admin()) is deliberately not
    # reproduced — NULL is reachable only via `on delete set null`, and there is
    # no is_platform_admin() equivalent on this connection.
    where.append("er.account_id = ANY(%(account_ids)s::uuid[])")
    params["account_ids"] = list(account_ids)
    if source is not None:
        where.append("er.source = %(source)s")
        params["source"] = source
    if status is not None:
        where.append("er.status = %(status)s")
        params["status"] = status
    if sreality_id is not None:
        where.append("er.input_sreality_id = %(sreality_id)s")
        params["sreality_id"] = sreality_id
    if listing_ids is not None:
        # `is not None`, never truthiness: an EMPTY list must still emit the
        # predicate (matching nothing) rather than silently dropping it. The
        # truthiness spelling is exactly how the fail-open shipped.
        # Property-grain fetch keyed ONLY on the surrogate listings.id: every run
        # on any of the property's child listings (the Listing Detail estimations
        # section). The legacy input_sreality_id is NULL for every post-Gate-2
        # non-sreality subject — migration 311's sign check makes a positive
        # sreality_id impossible off sreality — so keying on it silently dropped
        # those subjects, and an empty id set collapsed to "no filter at all".
        # A run with input_listing_id NULL (a raw spec, or one of the historic
        # URL-submitted runs) belongs to no listing and correctly appears under none.
        where.append("er.input_listing_id = ANY(%(listing_ids)s)")
        params["listing_ids"] = listing_ids

    filter_sql = "WHERE " + " AND ".join(where) if where else ""

    page_where = list(where)
    if cursor is not None:
        c_ts, c_id = decode_cursor(cursor)
        page_where.append(
            "(er.created_at, er.id) < (%(c_ts)s::timestamptz, %(c_id)s::bigint)"
        )
        params["c_ts"] = c_ts
        params["c_id"] = c_id
    page_where_sql = "WHERE " + " AND ".join(page_where) if page_where else ""

    list_sql = (
        f"SELECT {_LIST_PROJECTION} FROM {_LIST_FROM} {page_where_sql} "
        f"ORDER BY er.created_at DESC, er.id DESC LIMIT %(limit)s OFFSET %(offset)s"
    )
    # Keyset pages ignore offset; only the legacy no-cursor path uses it.
    list_params = {**params, "limit": limit, "offset": 0 if cursor else offset}

    with conn.cursor() as cur:
        cur.execute(list_sql, list_params)
        rows = cur.fetchall()
        total: int | None = None
        # Count on the first page only: any cursor'd page is page 2+ of an
        # infinite scroll, where the cohort total hasn't changed and a fresh
        # count is wasted. The legacy offset path (cursor None) still always
        # returns a total, preserving its contract.
        if cursor is None:
            count_params = {k: params[k] for k in params if k not in ("c_ts", "c_id")}
            cur.execute(
                f"SELECT count(*) FROM estimation_runs er {filter_sql}", count_params
            )
            total_row = cur.fetchone()
            total = int(total_row[0]) if total_row else 0

    id_idx = _RUN_COLUMNS.index("id")
    created_idx = _RUN_COLUMNS.index("created_at")
    next_cursor: str | None = None
    if len(rows) == limit and rows:
        last = rows[-1]
        next_cursor = encode_cursor([last[created_idx].isoformat(), last[id_idx]])

    return {
        "data": [_row_to_dict(_LIST_COLUMNS_OUT, r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_cursor": next_cursor,
    }


# --- _resolve_input: the ad a body names -------------------------------------

def _subject_listing_id(
    conn: "psycopg.Connection", body: s.CreateEstimationIn,
) -> int | None:
    """The listings.id a body names: its listing_id, or the sreality ad its legacy sreality_id keys."""
    if body.listing_id is not None:
        return int(body.listing_id)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM listings WHERE sreality_id = %s", (int(body.sreality_id),))
        row = cur.fetchone()
    return int(row[0]) if row else None


def latest_rent_estimations_by_listing(
    conn: "psycopg.Connection", sreality_ids: list[int], *, account_ids: list[str],
) -> dict[int, dict[str, Any]]:
    """Latest RENT estimation_runs row per listing id (any status), for the
    Browse cards' on-card estimate chip. Returns {sreality_id: {...}}; ids with
    no rent run are absent. Joined through `listings` on the surrogate
    `input_listing_id`, the handle every run of an ad carries.

    STILL SREALITY-KEYED ON PURPOSE. Browse's estimate chip is sreality-keyed
    end to end — the cards' id list, the estimate-state map, the pending-poll
    set, and `createEstimation({sreality_id})` itself. Re-keying only this read
    would produce a read path that can find estimates the Browse write path can
    never create for the same card. Making Browse able to estimate a
    non-sreality listing is its own cutover; until then a NULL-sreality card
    correctly shows no chip.

    `account_ids` is REQUIRED and has no default: same reasoning as
    list_estimation_runs — this ran unscoped on the service-role connection.
    """
    if not sreality_ids:
        return {}
    if not account_ids:
        raise ValueError("account_ids must be a non-empty read scope")
    ids = [int(x) for x in sreality_ids]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (l.sreality_id) "
            "l.sreality_id, er.id, er.status, er.estimate_kind, "
            "er.gross_yield_pct, er.estimated_monthly_rent_czk, er.created_at "
            "FROM listings l "
            "JOIN estimation_runs er ON er.input_listing_id = l.id "
            "WHERE l.sreality_id = ANY(%(ids)s) AND er.estimate_kind = 'rent' "
            # Scoped before DISTINCT ON picks, so the chip shows the newest run
            # the caller may actually see — never a hidden row blanked after the fact.
            "  AND er.account_id = ANY(%(account_ids)s::uuid[]) "
            "ORDER BY l.sreality_id, er.created_at DESC",
            {"ids": ids, "account_ids": list(account_ids)},
        )
        rows = cur.fetchall()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        out[int(r[0])] = {
            "sreality_id": int(r[0]),
            "run_id": int(r[1]),
            "status": r[2],
            "estimate_kind": r[3],
            "gross_yield_pct": float(r[4]) if r[4] is not None else None,
            "estimated_monthly_rent_czk": int(r[5]) if r[5] is not None else None,
            "created_at": r[6].isoformat() if r[6] is not None else None,
        }
    return out


def _resolve_input(
    conn: "psycopg.Connection", body: s.CreateEstimationIn,
) -> _Resolution:
    """The subject a body names: a raw spec as given, else the ad it names (listing_id or the
    legacy sreality_id) under the body's spec_overrides. An ad we do not hold, or one with no
    point to search around, is a 422."""
    if body.spec is not None:
        return _Resolution(target_spec=body.spec.model_dump())
    named = (
        f"listing {body.listing_id}" if body.listing_id is not None
        else f"sreality ad {body.sreality_id}"
    )
    listing_id = _subject_listing_id(conn, body)
    matched = match_listing(conn, listing_id) if listing_id is not None else None
    if matched is None:
        raise HTTPException(status_code=422, detail=f"{named} is not in our database")
    target_spec = {**matched["spec"], **(body.spec_overrides or {})}
    if target_spec.get("lat") is None or target_spec.get("lng") is None:
        raise HTTPException(
            status_code=422,
            detail=f"{named} needs a location before it can be estimated",
        )
    return _Resolution(
        target_spec=target_spec,
        input_listing_id=matched["listing_id"],
        input_sreality_id=matched["sreality_id"],
        subject_source=matched["source"],
        subject_source_id_native=matched["source_id_native"],
        subject_listing_price_czk=matched["price_czk"],
        subject_listing_category_type=matched["category_type"],
    )


def _derive_yield_inputs(
    body: s.CreateEstimationIn, resolution: _Resolution,
) -> tuple[int | None, int | None, dict[str, Any] | None]:
    """Fill in yield-formula inputs from the subject listing when the
    operator didn't supply them.

    Rent estimate + sale listing → use listing.price_czk as purchase price.
    Sale estimate + rental listing → use listing.price_czk as expected rent.
    Operator-supplied body values always win.

    Returns (purchase_price_czk, expected_monthly_rent_czk, derivation_log).
    derivation_log is None when nothing was derived; otherwise a dict
    describing the source for the trace step.
    """
    purchase = body.purchase_price_czk
    expected_rent = body.expected_monthly_rent_czk
    listing_price = resolution.subject_listing_price_czk
    listing_kind = resolution.subject_listing_category_type
    if listing_price is None or listing_price <= 0:
        return purchase, expected_rent, None
    if body.estimate_kind == "rent" and purchase is None and listing_kind == "prodej":
        return listing_price, expected_rent, {
            "field": "purchase_price_czk",
            "value": listing_price,
            "source": "subject_listing.price_czk",
            "subject_category_type": listing_kind,
        }
    if body.estimate_kind == "sale" and expected_rent is None and listing_kind == "pronajem":
        return purchase, listing_price, {
            "field": "expected_monthly_rent_czk",
            "value": listing_price,
            "source": "subject_listing.price_czk",
            "subject_category_type": listing_kind,
        }
    return purchase, expected_rent, None


def _record_yield_input_derivation(
    recorder: TraceRecorder, resolution: _Resolution,
) -> None:
    derivation = resolution.yield_input_derivation
    if derivation is None:
        return
    with recorder.computation("derive yield inputs from subject listing") as step:
        step.set_summary(dict(derivation))


def _persist_failed_run(
    conn: "psycopg.Connection",
    *,
    body: s.CreateEstimationIn,
    resolution: _Resolution,
    recorder: TraceRecorder,
    error_msg: str,
    # Optional for the same reason as create_estimation_run's: it inherits that
    # caller's account and falls back to SYSTEM below.
    account_id: str | None = None,
) -> dict[str, Any]:
    trace = recorder.to_dict(f"failed: {error_msg.split(':', 1)[0]}")
    run_id = _insert_run(
        conn,
        account_id=account_id or SYSTEM_ACCOUNT_ID,
        source=body.source,
        mode=body.mode,
        status="failed",
        estimate_kind=body.estimate_kind,
        input_sreality_id=resolution.input_sreality_id,
        input_listing_id=resolution.input_listing_id,
        input_spec=resolution.target_spec,
        input_purchase_price_czk=body.purchase_price_czk,
        estimated_monthly_rent_czk=None,
        rent_p25_czk=None,
        rent_p75_czk=None,
        estimated_sale_price_czk=None,
        sale_p25_czk=None,
        sale_p75_czk=None,
        gross_yield_pct=None,
        confidence=None,
        comparables_used=None,
        comparables_excluded=None,
        trace=trace,
        warnings=None,
        error_message=error_msg,
        parent_run_id=body.parent_run_id,
        rerun_reason=body.rerun_reason,
        special_instructions=body.special_instructions,
        contextual_text=body.contextual_text,
        skill_name=None,
        skill_version=None,
    )
    flush_trace_payloads(conn, run_id, recorder)
    return _fetch_run(conn, run_id) or {}


def _load_subject_condition(
    conn: "psycopg.Connection",
    listing_id: int | None,
) -> dict[str, Any] | None:
    """Fetch the subject ad's `condition` for the agent prompt; None for a raw-spec subject.

    The derived 1-5 levels are not sent: condition scoring is parked (rule 14).
    """
    if listing_id is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT condition FROM listings WHERE id = %s",
            (listing_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"condition": row[0]}


def _subject_mf_facts(
    conn: "psycopg.Connection", resolution: _Resolution, target: TargetSpec,
    body: s.CreateEstimationIn,
) -> dict[str, Any]:
    """What mf_reference() needs about the run's subject besides the target's
    disposition and area. A raw-spec subject has no stored location: its obec comes
    from the one containing-obec statement and it never binds a KÚ."""
    if resolution.input_listing_id is not None:
        facts = subject_mf_facts(conn, resolution.input_listing_id)
        if facts is not None:
            return facts
    spec = resolution.target_spec or {}
    return {
        "category_main": body.category_main,
        "category_type": resolution.subject_listing_category_type,
        "price_czk": resolution.subject_listing_price_czk,
        "condition": spec.get("condition"), "has_balcony": spec.get("has_balcony"),
        "terrace": spec.get("terrace"), "furnished": spec.get("furnished"),
        "garage": spec.get("garage"), "has_lift": spec.get("has_lift"),
        "building_type": spec.get("building_type"),
        "obec_kod": maps.containing_obec_kod(conn, lat=target.lat, lng=target.lng),
        "country_status": None,
    }


def _reference_rent_for_run(
    conn: "psycopg.Connection",
    recorder: TraceRecorder,
    resolution: _Resolution,
    target: TargetSpec,
    body: s.CreateEstimationIn,
) -> dict[str, Any] | None:
    """MF Cenová mapa secondary reference, emitted as a trace step. Rent estimates
    only; best-effort -- a secondary reference never fails a run."""
    if body.estimate_kind != "rent":
        return None
    with recorder.computation("reference rent (Cenová mapa MF)") as step:
        try:
            ref = compute_reference_rent(
                conn, disposition=target.disposition, area_m2=target.area_m2,
                **_subject_mf_facts(conn, resolution, target, body),
            )
        except Exception:  # noqa: BLE001 - secondary reference never fails a run
            LOG.warning("MF reference for the run failed", exc_info=True)
            ref = None
        if ref is None:
            step.set_summary({"matched": False})
        else:
            step.set_summary({
                "matched": "monthly_rent_czk" in ref,
                "status": ref.get("status"),
                "territory": (ref.get("territory") or {}).get("name"),
                "vk": ref.get("vk"),
                "is_novostavba": ref.get("is_novostavba"),
                "base_per_m2": ref.get("base_per_m2"),
                "total_per_m2": ref.get("total_per_m2"),
                "monthly_rent_czk": ref.get("monthly_rent_czk"),
            })
    return ref


def _build_target(
    spec: dict[str, Any], input_listing_id: int | None = None,
) -> TargetSpec:
    if spec.get("lat") is None or spec.get("lng") is None:
        raise ValueError("target has no coordinates")
    return TargetSpec(
        lat=float(spec["lat"]),
        lng=float(spec["lng"]),
        area_m2=spec.get("area_m2"),
        disposition=spec.get("disposition"),
        floor=spec.get("floor"),
        # Decision 13: every advert of the subject's property is left out; a frozen spec's
        # sreality-keyed `exclude_ids` is its own history and is not re-read (rule 12).
        exclude_listing_ids=[int(i) for i in spec.get("exclude_listing_ids") or []]
        + ([int(input_listing_id)] if input_listing_id is not None else []),
    )


def _build_filters(
    body: s.CreateEstimationIn,
    defaults: FilterDefaults,
) -> ComparableFilters:
    return ComparableFilters(
        radius_m=defaults.radius_m,
        area_band_pct=defaults.area_band_pct,
        disposition_match=defaults.disposition_match,
        max_age_days=defaults.max_age_days_for(body.estimate_kind),
        lifecycle=(
            body.lifecycle if body.lifecycle is not None else defaults.lifecycle
        ),
        floor_band=body.floor_band,
        portals=body.portals,
        condition_match=body.condition_match,
        building_type_match=body.building_type_match,
        energy_rating_match=body.energy_rating_match,
        has_balcony=body.has_balcony,
        has_lift=body.has_lift,
        has_parking=body.has_parking,
        min_price_czk=body.min_price_czk,
        max_price_czk=body.max_price_czk,
        category_main=body.category_main,
        category_type=body.category_type,
        include_unreliable=body.include_unreliable,
        category_sub_cb=body.category_sub_cb,
        furnished=body.furnished,
        terrace=body.terrace,
        cellar=body.cellar,
        garage=body.garage,
        ownership=body.ownership,
        min_estate_area=body.min_estate_area,
        max_estate_area=body.max_estate_area,
        min_usable_area=body.min_usable_area,
        max_usable_area=body.max_usable_area,
        min_parking_lots=body.min_parking_lots,
        tom_days_min=body.tom_days_min,
        tom_days_max=body.tom_days_max,
        tom_observed_only=body.tom_observed_only,
        last_seen_min_days=body.last_seen_min_days,
        last_seen_max_days=body.last_seen_max_days,
        first_seen_min_days=body.first_seen_min_days,
        first_seen_max_days=body.first_seen_max_days,
    )


def _summary_line(data: dict[str, Any], radius_m: int) -> str:
    n = data.get("sample_size") or 0
    confidence = data.get("confidence") or "unknown"
    kind = data.get("estimate_kind") or "rent"
    if kind == "sale":
        point = data.get("estimated_sale_price_czk")
        point_part = (
            f" estimated sale price {point} CZK." if point is not None else ""
        )
    else:
        point = data.get("estimated_monthly_rent_czk")
        point_part = (
            f" estimated rent {point} CZK/mo." if point is not None else ""
        )
    return (
        f"Found {n} comparables in {radius_m}m radius, "
        f"{confidence} confidence.{point_part}"
    )


def _agent_summary_line(
    data: dict[str, Any], metadata: dict[str, Any],
) -> str:
    rent = data.get("estimated_monthly_rent_czk")
    confidence = data.get("confidence") or "unknown"
    iters = metadata.get("iterations") or 0
    stop = metadata.get("stop_reason") or "?"
    cost = metadata.get("total_cost_usd")
    cost_part = f" cost ${cost:.4f}" if isinstance(cost, (int, float)) else ""
    rent_part = (
        f" rent ~{rent} CZK/mo." if rent is not None else " no estimate."
    )
    return (
        f"agent {metadata.get('provider', '?')}/{metadata.get('skill', '?')} "
        f"after {iters} LLM turn{'s' if iters != 1 else ''} ({stop}){cost_part}"
        f" {confidence}{rent_part}".strip()
    )


def _run_agent_path(
    conn: "psycopg.Connection",
    sreality_client: "SrealityClient",
    llm_client: "LLMClient",
    run_id: int,
    body: s.CreateEstimationIn,
    *,
    resolution: _Resolution,
    target: TargetSpec,
    filters: ComparableFilters,
) -> None:
    """Agent-mode dispatch. The row is already INSERTed with
    status='running' by the caller; this drives the agent loop and
    UPDATEs to a terminal status. The early INSERT is what lets
    `llm_calls.estimation_run_id` attribute every per-turn call."""
    from api.agent import run_agent_estimation
    from api.skills import load_skill

    # Skill existence is validated by the caller before INSERT — we
    # re-load here because the background path opens a fresh connection.
    skill = load_skill(conn, body.skill)

    recorder = TraceRecorder()
    _record_yield_input_derivation(recorder, resolution)

    try:
        agent_result = run_agent_estimation(
            conn, sreality_client, llm_client,
            target, filters, body.purchase_price_czk,
            skill=skill, provider=body.provider,
            recorder=recorder, estimation_run_id=run_id,
            special_instructions=body.special_instructions,
            contextual_text=body.contextual_text,
            subject_condition=_load_subject_condition(
                conn, resolution.input_listing_id,
            ),
        )
    except Exception as exc:
        LOG.warning("agent run failed: %s", exc)
        trace = recorder.to_dict(f"agent failed: {type(exc).__name__}")
        _update_run_terminal(
            conn, run_id,
            status="failed",
            trace=trace,
            error_message=f"{type(exc).__name__}: {exc}"[:1000],
        )
        flush_trace_payloads(conn, run_id, recorder)
        return

    d = agent_result.data
    md = agent_result.metadata
    status = "success" if md.get("stop_reason") == "record_estimate" else "failed"
    reference_rent = (
        _reference_rent_for_run(conn, recorder, resolution, target, body)
        if status == "success"
        else None
    )
    trace = recorder.to_dict(_agent_summary_line(d, md))

    err: str | None = None
    if status == "failed":
        err = f"agent halted: {md.get('stop_reason')}"

    _update_run_terminal(
        conn, run_id,
        status=status,
        estimated_monthly_rent_czk=d.get("estimated_monthly_rent_czk"),
        rent_p25_czk=d.get("rent_p25_czk"),
        rent_p75_czk=d.get("rent_p75_czk"),
        gross_yield_pct=d.get("gross_yield_pct"),
        confidence=d.get("confidence"),
        comparables_used=d.get("comparables_used"),
        comparables_excluded=d.get("comparables_excluded") or None,
        trace=trace,
        warnings=d.get("warnings") or None,
        error_message=err,
        reference_rent=reference_rent,
    )
    flush_trace_payloads(conn, run_id, recorder)
    if status == "success":
        # Meter the successful agent run (no-op for admin/SYSTEM/deterministic).
        # A failed run consumes no quota and writes no ledger row.
        _record_usage(conn, run_id)


def _update_run_terminal(
    conn: "psycopg.Connection",
    run_id: int,
    **fields: Any,
) -> None:
    """Parameterised UPDATE that writes only the supplied columns."""
    for k in (
        "comparables_used", "comparables_excluded",
        "trace", "warnings", "reference_rent",
    ):
        if fields.get(k) is not None:
            fields[k] = Jsonb(fields[k])
    sets: list[str] = []
    params: dict[str, Any] = {"id": run_id}
    for col, val in fields.items():
        sets.append(f"{col} = %({col})s")
        params[col] = val
    sql = (
        f"UPDATE estimation_runs SET {', '.join(sets)} WHERE id = %(id)s"
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql, params)


def _insert_run(
    conn: "psycopg.Connection",
    *,
    gate: "_MeterDecision | None" = None,
    **fields: Any,
) -> int | None:
    """INSERT an estimation_runs row, returning its id.

    With `gate` (a metered submit), the INSERT is the ATOMIC enforcement point
    (A9): an `INSERT ... SELECT WHERE (monthly non-failed count) < quota AND
    (in-flight count) < cap ON CONFLICT (account_id, idempotency_key) DO NOTHING`
    — budget + concurrency + idempotency in one write, with zero check-then-act
    race. No row back (returns None) means the gate rejected it or an idempotent
    duplicate raced in; the caller resolves the winner or 429s. Without `gate`
    (admin/internal/deterministic) it's the plain VALUES insert, which must
    always return an id (raises otherwise, unchanged)."""
    for col in _INSERT_COLUMNS:
        fields.setdefault(col, None)
    for k in (
        "input_spec", "comparables_used", "comparables_excluded",
        "trace", "warnings",
        "scenario", "reference_rent",
    ):
        if fields.get(k) is not None:
            fields[k] = Jsonb(fields[k])
    cols = list(_INSERT_COLUMNS)
    values = [f"%({c})s" for c in cols]
    # Optional worker-lane execution snapshot (migration 349). Kept out of
    # _RUN_COLUMNS so it never rides the API read surface; only ever present when
    # the job lane is enabled, and cleared to NULL by the lane at terminal.
    if fields.get("job_payload") is not None:
        fields["job_payload"] = Jsonb(fields["job_payload"])
        cols.append("job_payload")
        values.append("%(job_payload)s")
    # Idempotency / single-in-flight key for metered submits (migration 355),
    # likewise out of _RUN_COLUMNS. NULL for ungated callers.
    if fields.get("idempotency_key") is not None:
        cols.append("idempotency_key")
        values.append("%(idempotency_key)s")
    cols_sql = ", ".join(cols)
    placeholders = ", ".join(values)
    if gate is None:
        sql = (
            f"INSERT INTO estimation_runs ({cols_sql}) "
            f"VALUES ({placeholders}) RETURNING id"
        )
    else:
        fields["_g_quota"] = gate.quota
        fields["_g_cap"] = gate.concurrency_cap
        sql = (
            f"INSERT INTO estimation_runs ({cols_sql}) "
            f"SELECT {placeholders} "
            "WHERE (SELECT count(*) FROM estimation_runs "
            "         WHERE account_id = %(account_id)s AND mode = 'agent' "
            "           AND status <> 'failed' "
            "           AND created_at >= date_trunc('month', now())) < %(_g_quota)s "
            "  AND (SELECT count(*) FROM estimation_runs "
            "         WHERE account_id = %(account_id)s AND mode = 'agent' "
            "           AND status IN ('pending', 'running')) < %(_g_cap)s "
            "ON CONFLICT (account_id, idempotency_key) "
            "  WHERE status IN ('pending', 'running') AND idempotency_key IS NOT NULL "
            "DO NOTHING "
            "RETURNING id"
        )
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql, fields)
        row = cur.fetchone()
        if row is None:
            if gate is not None:
                return None
            raise RuntimeError("INSERT did not return an id")
        return int(row[0])


def _fetch_run(
    conn: "psycopg.Connection", run_id: int
) -> dict[str, Any] | None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_RUN_PROJECTION} FROM {_RUN_FROM} WHERE er.id = %s",
                (run_id,),
            )
            row = cur.fetchone()
    except Exception as exc:
        LOG.warning(
            "_fetch_run failed for run %s: %s: %s",
            run_id, type(exc).__name__, exc,
        )
        return {"id": run_id, "status": "success"}
    if row is None:
        return None
    try:
        return _row_to_dict(_RUN_COLUMNS_OUT, row)
    except Exception as exc:
        LOG.warning(
            "_row_to_dict failed for run %s: %s: %s",
            run_id, type(exc).__name__, exc,
        )
        return {"id": run_id, "status": "success"}


def _row_to_dict(
    cols: tuple[str, ...] | list[str], row: tuple[Any, ...]
) -> dict[str, Any]:
    out: dict[str, Any] = dict(zip(cols, row))
    for k, v in list(out.items()):
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
    return out
