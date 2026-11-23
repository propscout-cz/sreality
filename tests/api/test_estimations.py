"""Tests for /estimations endpoints (POST, GET-by-id, list).

Hermetic — overrides the DB-conn, SrealityClient, and LLMClient
dependencies, and mocks the persistence helpers + the subject matcher +
estimate_yield so no real DB / HTTP / LLM is hit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import estimate_yield as ey
from api import estimation_runs as er
from api import main as api_main
from api import tenant_pool
from toolkit import subject

SYSTEM_ACCOUNT = deps.SYSTEM_ACCOUNT_ID
_ACCT = "22222222-2222-2222-2222-222222222222"
_CLAIMS = {
    "sub": "11111111-1111-1111-1111-111111111111",
    "app_metadata": {"is_admin": True},
}


@pytest.fixture()
def client(monkeypatch):
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.get_sreality_client] = (
        lambda: object()
    )
    api_main.app.dependency_overrides[deps.get_llm_client] = (
        lambda: object()
    )
    # POST /estimations stays on get_db_conn (Wave 1 W1-1: execution off the
    # request process is W1-3) but now also gates on verify_jwt to stamp
    # account_id; GET/PATCH moved fully onto the tenant pool. Both overridden
    # to a production-shaped admin JWT here (admin keeps these route tests off
    # the metering SQL) — auth correctness itself lives in tests/api/test_auth.py.
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: dict(_CLAIMS)
    monkeypatch.setattr(
        tenant_pool, "resolve_account_id", lambda conn, claims: _ACCT,
    )
    # Run scheduled BackgroundTasks synchronously inside the handler so the
    # response payload reflects the post-task state. Without this, the
    # handler returns the freshly-INSERTed 'pending' row and the heavy
    # work would run after res.json() is captured.
    from starlette.background import BackgroundTasks
    monkeypatch.setattr(
        BackgroundTasks, "add_task",
        lambda self, func, *args, **kwargs: func(*args, **kwargs),
    )
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


# ----------------------------------------------------------------------
# Helpers: in-memory persistence patch (one fake _insert_run that mints
# IDs, one fake _fetch_run that reads what was inserted).
# ----------------------------------------------------------------------

class _State:
    def __init__(self) -> None:
        self.inserts: dict[int, dict[str, Any]] = {}
        self.insert_args: dict[int, dict[str, Any]] = {}
        self.next_id = 1


def _patch_persistence(monkeypatch) -> _State:
    state = _State()

    def fake_insert(conn, **fields: Any) -> int:
        rid = state.next_id
        state.next_id += 1
        state.inserts[rid] = dict(fields)
        state.insert_args[rid] = dict(fields)
        return rid

    def fake_update(conn, run_id: int, **fields: Any) -> None:
        if run_id in state.inserts:
            state.inserts[run_id].update(fields)

    def fake_flush(conn, run_id: int, recorder) -> None:
        return None

    def fake_fetch(conn, run_id: int) -> dict[str, Any] | None:
        if run_id not in state.inserts:
            return None
        fields = state.inserts[run_id]
        return {
            "id": run_id,
            "created_at": "2026-05-04T10:00:00+00:00",
            **{
                k: fields.get(k)
                for k in (
                    "source", "mode", "status", "estimate_kind",
                    "input_url", "input_sreality_id", "input_listing_id",
                    "input_spec", "input_purchase_price_czk",
                    "estimated_monthly_rent_czk", "rent_p25_czk",
                    "rent_p75_czk",
                    "estimated_sale_price_czk", "sale_p25_czk",
                    "sale_p75_czk",
                    "gross_yield_pct", "confidence",
                    "comparables_used", "comparables_excluded",
                    "trace", "warnings",
                    "error_message", "parent_run_id", "rerun_reason",
                    "skill_name", "skill_version",
                )
            },
        }

    def fake_bg(*, run_id, body, resolution):
        target = er._build_target(resolution.target_spec, resolution.input_listing_id)
        filters = er._build_filters(body, er.load_filter_defaults(None))
        er._execute_estimation_run(
            object(), object(), object(), run_id,
            body=body, resolution=resolution,
            target=target, filters=filters,
        )

    monkeypatch.setattr(er, "_insert_run", fake_insert)
    monkeypatch.setattr(er, "_update_run_terminal", fake_update)
    monkeypatch.setattr(er, "flush_trace_payloads", fake_flush)
    monkeypatch.setattr(er, "_fetch_run", fake_fetch)
    monkeypatch.setattr(
        er, "_execute_estimation_run_background", fake_bg,
    )
    return state


def _patch_estimate(monkeypatch, exc: Exception | None = None,
                    data: dict[str, Any] | None = None) -> None:
    def fake(conn, target, filters, purchase_price_czk=None, *,
            estimate_kind="rent", expected_monthly_rent_czk=None,
            trace_recorder=None):
        if exc is not None:
            raise exc
        if trace_recorder is not None:
            with trace_recorder.tool_call(
                "find_comparables", input={}
            ) as h:
                h.set_summary({"result_count": 5})
        if data is not None:
            payload = data
        elif estimate_kind == "sale":
            payload = {
                "estimate_kind": "sale",
                "estimated_monthly_rent_czk": None,
                "rent_p25_czk": None,
                "rent_p75_czk": None,
                "estimated_sale_price_czk": 6_000_000,
                "sale_p25_czk": 5_750_000,
                "sale_p75_czk": 6_250_000,
                "gross_yield_pct": (
                    round((expected_monthly_rent_czk * 12) / 6_000_000 * 100, 2)
                    if expected_monthly_rent_czk else None
                ),
                "confidence": "high",
                "sample_size": 5,
                "comparables_used": [
                    {"sreality_id": 1, "snapshot_id": 11},
                ],
                "warnings": [],
            }
        else:
            payload = {
                "estimate_kind": "rent",
                "estimated_monthly_rent_czk": 20500,
                "rent_p25_czk": 19000,
                "rent_p75_czk": 22000,
                "estimated_sale_price_czk": None,
                "sale_p25_czk": None,
                "sale_p75_czk": None,
                "gross_yield_pct": 4.92,
                "confidence": "high",
                "sample_size": 5,
                "comparables_used": [
                    {"sreality_id": 1, "snapshot_id": 11},
                ],
                "warnings": [],
            }
        return {"data": payload, "metadata": {"tool": "estimate_yield"}}
    monkeypatch.setattr(ey, "estimate_yield", fake)


_SPEC = {"lat": 50.087, "lng": 14.42, "area_m2": 50.0, "disposition": "2+kk", "floor": 3}


def _patch_subject(
    monkeypatch, *, listing_id: int = 777, source: str = "idnes",
    native: str = "65f0c0ffee", sreality_id: int | None = None,
    spec: dict[str, Any] | None = None, price_czk: int | None = None,
    category_type: str | None = None,
) -> list[int]:
    """One ad we hold: a sreality_id body resolves to it, and the ONE matcher (keyed on
    listings.id) answers for it. Returns the ids the matcher was asked for."""
    asked: list[int] = []

    def fake_listing_id(conn, body):
        if body.listing_id is not None:
            return body.listing_id
        return listing_id if body.sreality_id == sreality_id else None

    def fake_match(conn, lid):
        asked.append(lid)
        if lid != listing_id:
            return None
        return {
            "listing_id": listing_id, "source": source, "source_id_native": native,
            "sreality_id": sreality_id, "spec": dict(spec or _SPEC),
            "price_czk": price_czk, "category_type": category_type,
        }

    monkeypatch.setattr(er, "_subject_listing_id", fake_listing_id)
    monkeypatch.setattr(er, "match_listing", fake_match)
    return asked


# ----------------------------------------------------------------------
# POST /estimations
# ----------------------------------------------------------------------

def test_post_returns_pending_when_background_deferred(monkeypatch):
    """POST /estimations must return the freshly-INSERTed 'pending' row
    immediately when the heavy work is deferred. Verifies the
    fast-respond contract — without the per-test BackgroundTasks
    synchronous override, the response should still be a valid 200
    with status='pending' so the browser can navigate to the detail
    page and poll.
    """
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.get_sreality_client] = (
        lambda: object()
    )
    api_main.app.dependency_overrides[deps.get_llm_client] = lambda: object()
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: dict(_CLAIMS)
    monkeypatch.setattr(
        tenant_pool, "resolve_account_id", lambda conn, claims: _ACCT,
    )
    try:
        state = _patch_persistence(monkeypatch)
        _patch_estimate(monkeypatch)

        # Replace the background entry point with a counter — we only
        # care that it was SCHEDULED, not that it ran.
        scheduled: list[int] = []
        monkeypatch.setattr(
            er, "_execute_estimation_run_background",
            lambda **kw: scheduled.append(kw["run_id"]),
        )

        client_local = TestClient(api_main.app)
        res = client_local.post(
            "/estimations",
            json={"spec": {"lat": 50.0, "lng": 14.0, "area_m2": 50.0}},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "pending"
        assert body["id"] == 1
        assert body["estimated_monthly_rent_czk"] is None
        assert scheduled == [1]
        # The persisted row was also pending at INSERT time.
        assert state.inserts[1]["status"] == "pending"
    finally:
        api_main.app.dependency_overrides.clear()


def test_post_with_spec_creates_success_row(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)

    res = client.post(
        "/estimations",
        json={
            "spec": {
                "lat": 50.087, "lng": 14.42, "area_m2": 50.0,
                "disposition": "2+kk",
            },
            "purchase_price_czk": 5_000_000,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["estimated_monthly_rent_czk"] == 20500
    assert body["confidence"] == "high"
    assert body["input_url"] is None
    assert body["input_sreality_id"] is None
    assert body["input_spec"]["lat"] == 50.087
    assert body["input_spec"]["lng"] == 14.42
    assert body["input_purchase_price_czk"] == 5_000_000
    assert len(state.inserts) == 1
    inserted = state.inserts[1]
    assert inserted["status"] == "success"
    assert inserted["source"] == "api"
    assert inserted["mode"] == "deterministic"
    assert inserted["trace"]["version"] == 2
    assert inserted["trace"]["steps"][0]["tool"] == "find_comparables"


def test_post_with_listing_id_resolves_and_runs(client, monkeypatch):
    """An estimation is of an ad we hold: {listing_id} names it, whatever its portal, and
    the run carries its listings.id from submit on (rule 12: never late-bound)."""
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)
    asked = _patch_subject(monkeypatch, listing_id=777, source="idnes")

    res = client.post("/estimations", json={"listing_id": 777})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["input_listing_id"] == 777
    assert body["input_sreality_id"] is None
    assert body["input_url"] is None
    assert body["input_spec"]["lat"] == 50.087
    assert asked == [777]
    assert state.inserts[1]["input_listing_id"] == 777
    assert "input_url" not in state.inserts[1]


def test_post_with_listing_id_excludes_target_from_comparables(client, monkeypatch):
    """The subject ad leaves its whole property out of the cohort (decision 13), so an
    estimate can never quote the target, or its sibling on another portal, as its own
    comparable."""
    _patch_persistence(monkeypatch)
    _patch_subject(monkeypatch, listing_id=777)

    captured: dict[str, Any] = {}

    def fake(conn, target, filters, purchase_price_czk=None, *,
             estimate_kind="rent", expected_monthly_rent_czk=None,
             trace_recorder=None):
        captured["exclude_listing_ids"] = list(target.exclude_listing_ids)
        return {
            "data": {
                "estimate_kind": "rent",
                "estimated_monthly_rent_czk": 20000,
                "rent_p25_czk": 19000, "rent_p75_czk": 21000,
                "estimated_sale_price_czk": None,
                "sale_p25_czk": None, "sale_p75_czk": None,
                "gross_yield_pct": None, "confidence": "medium",
                "sample_size": 5, "comparables_used": [], "warnings": [],
            },
            "metadata": {"tool": "estimate_yield"},
        }
    monkeypatch.setattr(ey, "estimate_yield", fake)

    res = client.post("/estimations", json={"listing_id": 777})
    assert res.status_code == 200
    assert captured["exclude_listing_ids"] == [777]


def test_post_with_listing_id_and_spec_overrides_merges(client, monkeypatch):
    _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)
    _patch_subject(monkeypatch)

    res = client.post(
        "/estimations",
        json={"listing_id": 777, "spec_overrides": {"area_m2": 60.0, "floor": 5}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["input_spec"]["area_m2"] == 60.0
    assert body["input_spec"]["floor"] == 5
    assert body["input_spec"]["lat"] == 50.087


def test_post_sale_estimate_persists_sale_columns(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)

    res = client.post(
        "/estimations",
        json={
            "estimate_kind": "sale",
            "spec": {
                "lat": 50.087, "lng": 14.42, "area_m2": 50.0,
                "disposition": "2+kk",
            },
            "expected_monthly_rent_czk": 25_000,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["estimate_kind"] == "sale"
    assert body["estimated_sale_price_czk"] == 6_000_000
    assert body["sale_p25_czk"] == 5_750_000
    assert body["sale_p75_czk"] == 6_250_000
    assert body["estimated_monthly_rent_czk"] is None
    assert body["gross_yield_pct"] == 5.0
    inserted = state.inserts[1]
    assert inserted["estimate_kind"] == "sale"
    assert inserted["estimated_sale_price_czk"] == 6_000_000


def test_post_default_estimate_kind_is_rent(client, monkeypatch):
    _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)

    res = client.post(
        "/estimations",
        json={
            "spec": {
                "lat": 50.087, "lng": 14.42, "area_m2": 50.0,
                "disposition": "2+kk",
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["estimate_kind"] == "rent"
    assert body["estimated_monthly_rent_czk"] == 20500
    assert body["estimated_sale_price_czk"] is None


def test_post_with_both_listing_id_and_spec_returns_422(client):
    res = client.post(
        "/estimations",
        json={"listing_id": 777, "spec": {"lat": 50.0, "lng": 14.0}},
    )
    assert res.status_code == 422


def test_post_with_no_subject_returns_422(client):
    res = client.post(
        "/estimations",
        json={"purchase_price_czk": 5_000_000},
    )
    assert res.status_code == 422


def test_post_with_url_returns_422(client, monkeypatch):
    """A pasted link becomes an ad through POST /listings/ingest first; the URL arm is gone."""
    state = _patch_persistence(monkeypatch)
    url = "https://reality.idnes.cz/detail/prodej/byt/x/abc/"
    for body in ({"url": url}, {"url": url, "listing_id": 7}, {"url": url, "sreality_id": 1}):
        res = client.post("/estimations", json=body)
        assert res.status_code == 422, body
        assert "no longer takes a url" in res.text
    assert state.inserts == {}


def test_post_when_estimate_yield_raises_persists_failed(
    client, monkeypatch
):
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch, exc=RuntimeError("DB connection lost"))

    res = client.post(
        "/estimations",
        json={"spec": {"lat": 50.0, "lng": 14.0, "area_m2": 50.0}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert "DB connection lost" in body["error_message"]
    assert body["estimated_monthly_rent_czk"] is None
    assert body["confidence"] is None
    inserted = state.inserts[1]
    assert inserted["status"] == "failed"
    assert "DB connection lost" in inserted["error_message"]


def test_post_with_parent_run_id_populates_fk(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)

    res = client.post(
        "/estimations",
        json={
            "spec": {"lat": 50.0, "lng": 14.0, "area_m2": 50.0},
            "parent_run_id": 7,
            "rerun_reason": "force refetch",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["parent_run_id"] == 7
    assert body["rerun_reason"] == "force refetch"
    # The INSERT itself carries the FK (rule #12), not a later UPDATE of the row.
    inserted = state.insert_args[1]
    assert (inserted["parent_run_id"], inserted["rerun_reason"]) == (7, "force refetch")


def test_post_with_mode_agent_does_not_500(client, monkeypatch):
    """Regression: the agent-path _insert_run used to omit estimate_kind
    and the three sale columns, KeyError-ing inside psycopg and bubbling
    out as a generic 500. POST should land a real row instead."""
    state = _patch_persistence(monkeypatch)

    def fake_update(conn, run_id: int, **fields: Any) -> None:
        state.inserts[run_id].update(fields)

    monkeypatch.setattr(er, "_update_run_terminal", fake_update)

    from api import agent as agent_mod
    from api import skills as sk

    monkeypatch.setattr(
        agent_mod, "run_agent_estimation",
        lambda *a, **kw: agent_mod.AgentResult(
            data={
                "estimated_monthly_rent_czk": 25000,
                "rent_p25_czk": 23000,
                "rent_p75_czk": 27000,
                "gross_yield_pct": None,
                "confidence": "medium",
                "comparables_used": [],
                "warnings": [],
            },
            metadata={
                "stop_reason": "record_estimate",
                "iterations": 1,
                "total_cost_usd": 0.0,
                "provider": "anthropic",
                "skill": "rental_estimator_v1",
            },
        ),
    )
    monkeypatch.setattr(
        sk, "load_skill",
        lambda conn, name: sk.Skill(
            name=name, description="", system_prompt="",
            allowed_tools=["record_estimate"],
            preferred_model={"anthropic": "x", "gemini": "y"},
            limits=sk.Limits(
                max_iterations=5, max_cost_usd=1.0,
                wall_clock_timeout_s=60.0,
            ),
        ),
    )

    res = client.post(
        "/estimations",
        json={
            "mode": "agent",
            "spec": {
                "lat": 50.0, "lng": 14.0, "area_m2": 50.0,
                "disposition": "2+kk",
            },
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["mode"] == "agent"
    assert body["estimate_kind"] == "rent"
    assert len(state.inserts) == 1
    inserted = state.inserts[1]
    assert inserted["estimate_kind"] == "rent"
    assert inserted["estimated_sale_price_czk"] is None


def test_agent_basis_refusal_lands_a_failed_run_not_a_500(client, monkeypatch):
    """The agent's basis gate must fail the RUN, the way the deterministic
    path's 422 does — a green run row with a mislabelled number is the outcome
    the gate exists to prevent, and a 500 would lose the reason."""
    state = _patch_persistence(monkeypatch)

    def fake_update(conn, run_id: int, **fields: Any) -> None:
        state.inserts[run_id].update(fields)

    monkeypatch.setattr(er, "_update_run_terminal", fake_update)

    from api import agent as agent_mod
    from api import skills as sk
    from toolkit.measures import MeasureBasisError

    def _raise(*a: Any, **kw: Any) -> Any:
        raise MeasureBasisError(
            "per-m² basis 'sale_capital_czk_m2' cannot be scaled into a rent figure"
        )

    monkeypatch.setattr(agent_mod, "run_agent_estimation", _raise)
    monkeypatch.setattr(
        sk, "load_skill",
        lambda conn, name: sk.Skill(
            name=name, description="", system_prompt="",
            allowed_tools=["record_estimate"],
            preferred_model={"anthropic": "x", "gemini": "y"},
            limits=sk.Limits(
                max_iterations=5, max_cost_usd=1.0,
                wall_clock_timeout_s=60.0,
            ),
        ),
    )

    res = client.post(
        "/estimations",
        json={
            "mode": "agent",
            "spec": {
                "lat": 50.0, "lng": 14.0, "area_m2": 50.0,
                "disposition": "2+kk",
            },
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "failed"
    inserted = state.inserts[1]
    assert inserted["status"] == "failed"
    assert "MeasureBasisError" in inserted["error_message"]
    assert inserted.get("estimated_monthly_rent_czk") is None


def test_post_rent_estimate_on_sale_listing_derives_purchase_price(
    client, monkeypatch,
):
    """Rent estimate against a SALE ad: the ad's price_czk auto-fills
    input_purchase_price_czk so yield gets computed without the operator
    typing a price."""
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)
    _patch_subject(monkeypatch, price_czk=8_500_000, category_type="prodej")

    res = client.post("/estimations", json={"listing_id": 777})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["input_purchase_price_czk"] == 8_500_000
    assert body["gross_yield_pct"] is not None
    inserted = state.inserts[1]
    assert inserted["input_purchase_price_czk"] == 8_500_000
    derivation_steps = [
        s for s in inserted["trace"]["steps"]
        if s.get("label") == "derive yield inputs from subject listing"
    ]
    assert len(derivation_steps) == 1
    summary = derivation_steps[0]["output_summary"]
    assert summary["field"] == "purchase_price_czk"
    assert summary["value"] == 8_500_000
    assert summary["source"] == "subject_listing.price_czk"
    assert summary["subject_category_type"] == "prodej"


def test_post_sale_estimate_on_rental_listing_derives_expected_rent(
    client, monkeypatch,
):
    """Sale estimate against a RENTAL ad: the ad's monthly rent auto-fills
    expected_monthly_rent_czk so yield gets computed without the operator
    typing a rent figure."""
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)
    _patch_subject(monkeypatch, price_czk=25_000, category_type="pronajem")

    res = client.post(
        "/estimations", json={"estimate_kind": "sale", "listing_id": 777},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["gross_yield_pct"] == 5.0  # (25000 * 12) / 6_000_000 * 100
    inserted = state.inserts[1]
    derivation_steps = [
        s for s in inserted["trace"]["steps"]
        if s.get("label") == "derive yield inputs from subject listing"
    ]
    assert len(derivation_steps) == 1
    summary = derivation_steps[0]["output_summary"]
    assert summary["field"] == "expected_monthly_rent_czk"
    assert summary["value"] == 25_000
    assert summary["subject_category_type"] == "pronajem"


def test_post_rent_estimate_on_rental_listing_skips_derivation(
    client, monkeypatch,
):
    """Negative case: rent-estimating a rental listing leaves
    input_purchase_price_czk null. A rental's price_czk is monthly rent,
    not a purchase price, so deriving it would yield nonsense."""
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)
    _patch_subject(monkeypatch, price_czk=25_000, category_type="pronajem")

    res = client.post("/estimations", json={"listing_id": 777})
    assert res.status_code == 200
    body = res.json()
    assert body["input_purchase_price_czk"] is None
    inserted = state.inserts[1]
    derivation_steps = [
        s for s in inserted["trace"]["steps"]
        if s.get("label") == "derive yield inputs from subject listing"
    ]
    assert derivation_steps == []


def test_post_operator_purchase_price_wins_over_listing(client, monkeypatch):
    """If the operator supplies purchase_price_czk explicitly, the
    auto-derivation must NOT overwrite it."""
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)
    _patch_subject(monkeypatch, price_czk=8_500_000, category_type="prodej")

    res = client.post(
        "/estimations", json={"listing_id": 777, "purchase_price_czk": 5_000_000},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["input_purchase_price_czk"] == 5_000_000
    inserted = state.inserts[1]
    derivation_steps = [
        s for s in inserted["trace"]["steps"]
        if s.get("label") == "derive yield inputs from subject listing"
    ]
    assert derivation_steps == []


def test_post_invalid_source_returns_422(client):
    res = client.post(
        "/estimations",
        json={
            "source": "wibble",
            "spec": {"lat": 50.0, "lng": 14.0},
        },
    )
    assert res.status_code == 422


# ----------------------------------------------------------------------
# GET /estimations/{id}
# ----------------------------------------------------------------------

def test_get_returns_row(client, monkeypatch):
    fake_row = {"id": 42, "status": "success"}
    monkeypatch.setattr(
        api_main, "get_estimation_run", lambda conn, rid: fake_row
    )
    res = client.get("/estimations/42")
    assert res.status_code == 200
    assert res.json()["id"] == 42


def test_get_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(
        api_main, "get_estimation_run", lambda conn, rid: None
    )
    res = client.get("/estimations/999")
    assert res.status_code == 404


# ----------------------------------------------------------------------
# PATCH /estimations/{id}/scenario
# ----------------------------------------------------------------------

def test_patch_scenario_updates_and_returns_row(client, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_update(
        conn, rid, *, rent_czk, fond_per_m2_czk, price_czk, renovation_czk,
    ):
        captured.update(
            {"rid": rid, "rent_czk": rent_czk,
             "fond_per_m2_czk": fond_per_m2_czk, "price_czk": price_czk,
             "renovation_czk": renovation_czk},
        )
        return {"id": rid, "status": "success",
                "scenario": {"rent_czk": rent_czk,
                             "fond_per_m2_czk": fond_per_m2_czk,
                             "price_czk": price_czk,
                             "renovation_czk": renovation_czk,
                             "updated_at": "2026-05-19T11:00:00.000+00:00"}}

    monkeypatch.setattr(api_main, "update_scenario", fake_update)

    res = client.patch(
        "/estimations/42/scenario",
        json={"rent_czk": 25000, "fond_per_m2_czk": 12, "price_czk": 7_500_000,
              "renovation_czk": 350_000},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == 42
    assert body["scenario"]["rent_czk"] == 25000
    assert body["scenario"]["renovation_czk"] == 350_000
    assert captured == {
        "rid": 42, "rent_czk": 25000, "fond_per_m2_czk": 12,
        "price_czk": 7_500_000, "renovation_czk": 350_000,
    }


def test_patch_scenario_404_when_missing(client, monkeypatch):
    monkeypatch.setattr(
        api_main, "update_scenario",
        lambda conn, rid, **_kw: None,
    )
    res = client.patch(
        "/estimations/999/scenario", json={"rent_czk": 25000},
    )
    assert res.status_code == 404


def test_patch_scenario_all_null_clears(client, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_update(
        conn, rid, *, rent_czk, fond_per_m2_czk, price_czk, renovation_czk,
    ):
        captured.update(
            {"rent_czk": rent_czk,
             "fond_per_m2_czk": fond_per_m2_czk,
             "price_czk": price_czk,
             "renovation_czk": renovation_czk},
        )
        return {"id": rid, "status": "success", "scenario": None}

    monkeypatch.setattr(api_main, "update_scenario", fake_update)

    res = client.patch(
        "/estimations/42/scenario",
        json={"rent_czk": None, "fond_per_m2_czk": None, "price_czk": None,
              "renovation_czk": None},
    )
    assert res.status_code == 200
    assert res.json()["scenario"] is None
    assert captured == {
        "rent_czk": None, "fond_per_m2_czk": None, "price_czk": None,
        "renovation_czk": None,
    }


def test_patch_scenario_empty_body_treated_as_clear(client, monkeypatch):
    """A POST with no fields set: ScenarioUpdateIn defaults all to None."""
    captured: dict[str, Any] = {}

    def fake_update(
        conn, rid, *, rent_czk, fond_per_m2_czk, price_czk, renovation_czk,
    ):
        captured.update(
            {"rent_czk": rent_czk,
             "fond_per_m2_czk": fond_per_m2_czk,
             "price_czk": price_czk,
             "renovation_czk": renovation_czk},
        )
        return {"id": rid, "status": "success", "scenario": None}

    monkeypatch.setattr(api_main, "update_scenario", fake_update)

    res = client.patch("/estimations/42/scenario", json={})
    assert res.status_code == 200
    assert captured == {
        "rent_czk": None, "fond_per_m2_czk": None, "price_czk": None,
        "renovation_czk": None,
    }


# ----------------------------------------------------------------------
# GET /estimations (list)
# ----------------------------------------------------------------------

def test_list_passes_filters_and_pagination(client, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_list(conn, **kw):
        captured.update(kw)
        return {"data": [], "total": 0, "limit": kw["limit"],
                "offset": kw["offset"]}

    monkeypatch.setattr(api_main, "list_estimation_runs", fake_list)

    res = client.get(
        "/estimations?source=ui&status=success"
        "&sreality_id=12345&limit=10&offset=20"
    )
    assert res.status_code == 200
    assert captured["source"] == "ui"
    assert captured["status"] == "success"
    assert captured["sreality_id"] == 12345
    assert captured["limit"] == 10
    assert captured["offset"] == 20


def test_list_listing_ids_csv_parsed(client, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_list(conn, **kw):
        captured.update(kw)
        return {"data": [], "total": 0, "limit": kw["limit"],
                "offset": kw["offset"]}

    monkeypatch.setattr(api_main, "list_estimation_runs", fake_list)

    res = client.get("/estimations?listing_ids=11,%2022,33")
    assert res.status_code == 200
    assert captured["listing_ids"] == [11, 22, 33]

    # ABSENT means "no listing filter" — the only case that may omit the predicate.
    res = client.get("/estimations")
    assert res.status_code == 200
    assert captured["listing_ids"] is None


def test_list_listing_ids_fails_closed_when_supplied_but_empty(client, monkeypatch):
    """The regression this endpoint shipped with: `ids[:50] or None` turned an
    empty parse into "no filter" and paged the WHOLE table. Supplied-but-empty
    must be a loud 400, never a silent unfiltered read. `[null].join(',')` in the
    SPA produced exactly this request."""
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        api_main, "list_estimation_runs",
        lambda conn, **kw: called.update(kw) or {
            "data": [], "total": 0, "limit": 50, "offset": 0,
        },
    )

    for qs in ("listing_ids=", "listing_ids=,", "listing_ids=%20,%20"):
        res = client.get(f"/estimations?{qs}")
        assert res.status_code == 400, qs
        assert "listing_ids" in res.json()["detail"]
    assert called == {}, "the query must never run for an empty id set"


def test_list_rejects_the_retired_sreality_ids_param(client, monkeypatch):
    """FastAPI silently DROPS undeclared query params, so simply renaming the
    parameter would have moved the fail-open rather than closed it: a stale SPA
    bundle (Railway deploys on merge; an open tab keeps its cached chunk) sends
    ?sreality_ids=<csv> and would have received an unfiltered page, rendering
    another property's valuation as this listing's. It is declared purely to be
    rejected."""
    called: dict[str, Any] = {}
    monkeypatch.setattr(
        api_main, "list_estimation_runs",
        lambda conn, **kw: called.update(kw) or {
            "data": [], "total": 0, "limit": 50, "offset": 0,
        },
    )
    res = client.get("/estimations?sreality_ids=11,22&limit=100")
    assert res.status_code == 400
    assert "listing_ids" in res.json()["detail"]
    assert called == {}, "the query must never run for the retired param"


def test_list_helper_emits_predicate_even_for_an_empty_id_list():
    """Defense in depth below the route: an EMPTY list must still produce
    `= ANY('{}')` (matching nothing), never a dropped predicate. Truthiness is
    exactly how the original fail-open was spelled."""
    conn = _FakeConn(results=[[], (0,)])
    er.list_estimation_runs(
        conn, listing_ids=[], limit=5, account_ids=[SYSTEM_ACCOUNT],
    )
    list_sql, list_params = conn.executions[0]
    assert "er.input_listing_id = ANY(%(listing_ids)s)" in list_sql
    assert list_params["listing_ids"] == []


def test_list_listing_ids_rejects_garbage_instead_of_dropping_it(client, monkeypatch):
    """A non-integer id used to be silently skipped, so `listing_ids=x` narrowed
    to nothing and then failed open. Unparseable input is a 400."""
    monkeypatch.setattr(
        api_main, "list_estimation_runs",
        lambda conn, **kw: {"data": [], "total": 0, "limit": 50, "offset": 0},
    )
    res = client.get("/estimations?listing_ids=11,x,33")
    assert res.status_code == 400
    assert "listing_ids" in res.json()["detail"]


def test_list_default_limit_50_offset_0(client, monkeypatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        api_main, "list_estimation_runs",
        lambda conn, **kw: captured.update(kw)
        or {"data": [], "total": 0, "limit": kw["limit"],
            "offset": kw["offset"]},
    )
    res = client.get("/estimations")
    assert res.status_code == 200
    assert captured["limit"] == 50
    assert captured["offset"] == 0
    assert captured["source"] is None
    assert captured["status"] is None
    assert captured["sreality_id"] is None


def test_list_limit_over_200_rejected(client):
    res = client.get("/estimations?limit=500")
    assert res.status_code == 422


def test_list_negative_offset_rejected(client):
    res = client.get("/estimations?offset=-1")
    assert res.status_code == 422


def test_list_invalid_status_rejected(client):
    res = client.get("/estimations?status=wibble")
    assert res.status_code == 422


# ----------------------------------------------------------------------
# list_estimation_runs SQL construction (fake conn)
# ----------------------------------------------------------------------

class _Cur:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = ()) -> None:
        self._conn.executions.append((sql, params))

    def fetchone(self) -> Any:
        return self._conn._results.pop(0) if self._conn._results else None

    def fetchall(self) -> Any:
        return self._conn._results.pop(0) if self._conn._results else []


class _FakeConn:
    def __init__(self, results: list[Any] | None = None) -> None:
        self.executions: list[tuple[str, Any]] = []
        self._results: list[Any] = list(results or [])

    def cursor(self) -> _Cur:
        return _Cur(self)


def test_list_no_filters_builds_naked_sql():
    """No FILTERS requested still carries the account predicate — scoping is not
    a filter the caller can decline. Only the optional predicates are absent."""
    conn = _FakeConn(results=[[], (0,)])
    res = er.list_estimation_runs(
        conn, limit=20, offset=5, account_ids=[SYSTEM_ACCOUNT],
    )
    assert res == {
        "data": [], "total": 0, "limit": 20, "offset": 5, "next_cursor": None,
    }
    list_sql, list_params = conn.executions[0]
    count_sql, count_params = conn.executions[1]
    # No outer WHERE clause — every filter predicate is gated behind
    # an "er.<col> = " prefix, none of which should appear here.
    for predicate in ("er.source =", "er.status =", "er.input_sreality_id ="):
        assert predicate not in list_sql
        assert predicate not in count_sql
    assert "ORDER BY er.created_at DESC" in list_sql
    assert "LIMIT %(limit)s OFFSET %(offset)s" in list_sql
    assert "cost_usd_total" in list_sql
    assert "FROM llm_calls WHERE estimation_run_id = er.id" in list_sql
    assert list_params == {
        "limit": 20, "offset": 5, "account_ids": [SYSTEM_ACCOUNT],
    }
    assert count_params == {"account_ids": [SYSTEM_ACCOUNT]}


def test_account_predicate_lands_in_both_list_and_count_sql():
    """The scope must ride `filter_sql`, which feeds the count(*) too. A predicate
    added only to the page query would leak the true CROSS-ACCOUNT total while
    showing a scoped page."""
    conn = _FakeConn(results=[[], (0,)])
    er.list_estimation_runs(conn, limit=5, account_ids=["acct-a", SYSTEM_ACCOUNT])
    list_sql, list_params = conn.executions[0]
    count_sql, count_params = conn.executions[1]
    pred = "er.account_id = ANY(%(account_ids)s::uuid[])"
    assert pred in list_sql
    assert pred in count_sql
    assert list_params["account_ids"] == ["acct-a", SYSTEM_ACCOUNT]
    assert count_params["account_ids"] == ["acct-a", SYSTEM_ACCOUNT]


def test_list_rejects_empty_account_scope():
    """An empty scope must never mean "everything" — the defect class this whole
    change exists to delete."""
    conn = _FakeConn(results=[[], (0,)])
    with pytest.raises(ValueError):
        er.list_estimation_runs(conn, limit=5, account_ids=[])
    assert conn.executions == []


def test_list_listing_ids_predicate_keys_on_surrogate_only():
    """No legacy fallback arm: input_sreality_id is NULL for every post-Gate-2
    non-sreality subject, so an OR arm on it can only re-admit the wrong rows."""
    conn = _FakeConn(results=[[], (0,)])
    er.list_estimation_runs(
        conn, listing_ids=[501, 502], account_ids=[SYSTEM_ACCOUNT],
    )
    list_sql, list_params = conn.executions[0]
    assert "er.input_listing_id = ANY(%(listing_ids)s)" in list_sql
    assert "er.input_sreality_id = ANY(" not in list_sql
    assert list_params["listing_ids"] == [501, 502]


def test_list_with_filters_builds_where_clause():
    conn = _FakeConn(results=[[], (3,)])
    er.list_estimation_runs(
        conn, source="ui", status="success",
        sreality_id=12345, limit=50, offset=0, account_ids=[SYSTEM_ACCOUNT],
    )
    list_sql, list_params = conn.executions[0]
    assert "er.source = %(source)s" in list_sql
    assert "er.status = %(status)s" in list_sql
    assert "er.input_sreality_id = %(sreality_id)s" in list_sql
    assert list_params["source"] == "ui"
    assert list_params["status"] == "success"
    assert list_params["sreality_id"] == 12345


def test_list_joins_the_subject_on_its_surrogate_only():
    """Every run of an ad carries its input_listing_id from submit, so the list's
    LEFT JOIN has no legacy sreality arm; it projects the subject ad's (source,
    native id), by which the SPA routes a run to its ad's page."""
    conn = _FakeConn(results=[[], (0,)])
    er.list_estimation_runs(conn, limit=1, account_ids=[SYSTEM_ACCOUNT])
    list_sql, _ = conn.executions[0]
    assert "LEFT JOIN listings l ON l.id = er.input_listing_id" in list_sql
    assert "input_listing_id IS NULL" not in list_sql
    assert "l.source AS subject_source" in list_sql
    assert "l.source_id_native AS subject_source_id_native" in list_sql
    assert "parsed_url_cache" not in list_sql
    assert "ll.obec_name AS locality_display" in list_sql


def test_get_estimation_run_returns_none_when_missing():
    conn = _FakeConn(results=[None])
    res = er.get_estimation_run(conn, run_id=999)
    assert res is None
    assert "WHERE er.id = %s" in conn.executions[0][0]
    assert "cost_usd_total" in conn.executions[0][0]


def test_get_estimation_run_projects_the_subject_ad():
    """The detail read runs on the tenant connection, where `listings` (RLS, no
    policy) returns nothing, so the subject ad is joined through the owner view."""
    row = [None] * len(er._RUN_COLUMNS_OUT)
    row[er._RUN_COLUMNS_OUT.index("id")] = 5
    row[er._RUN_COLUMNS_OUT.index("input_listing_id")] = 777
    row[er._RUN_COLUMNS_OUT.index("subject_source")] = "bezrealitky"
    row[er._RUN_COLUMNS_OUT.index("subject_source_id_native")] = "998877"
    conn = _FakeConn(results=[tuple(row)])
    run = er.get_estimation_run(conn, run_id=5)
    sql = conn.executions[0][0]
    assert "LEFT JOIN listings_public l ON l.id = er.input_listing_id" in sql
    assert run["subject_source"] == "bezrealitky"
    assert run["subject_source_id_native"] == "998877"
    assert run["input_listing_id"] == 777
    for gone in ("source_kind", "parse_confidence", "source_html", "subject_attributes"):
        assert gone not in run


# ----------------------------------------------------------------------
# The ONE subject matcher, keyed on listings.id. Numeric columns must not
# leak Decimal into the subject spec, which is later stored as jsonb
# (Jsonb -> json.dumps).
# ----------------------------------------------------------------------

def test_match_listing_keys_on_listings_id_and_casts_numerics():
    import json
    from decimal import Decimal

    # row: id, source, source_id_native, sreality_id, lat, lng, area_m2 (numeric),
    #      disposition, floor, price_czk, category_type
    row = (501, "remax", "123", None, 50.4208, 14.9016, Decimal("108.0"), "2+kk", 3,
           Decimal("6299000"), "prodej")
    conn = _FakeConn(results=[row])

    matched = subject.match_listing(conn, 501)

    sql, params = conn.executions[0]
    assert "WHERE l.id = %(id)s" in sql and params == {"id": 501}
    assert matched is not None
    assert (matched["listing_id"], matched["source"], matched["source_id_native"]) == (
        501, "remax", "123")
    assert matched["sreality_id"] is None
    area = matched["spec"]["area_m2"]
    assert isinstance(area, float) and area == 108.0
    assert matched["price_czk"] == 6_299_000
    json.dumps(matched["spec"])


def test_match_listing_unlocated_ad_has_no_point():
    row = (501, "remax", "123", None, None, None, None, "2+kk", 3, None, "prodej")
    matched = subject.match_listing(_FakeConn(results=[row]), 501)
    assert matched is not None
    assert matched["spec"]["lat"] is None and matched["spec"]["area_m2"] is None


def test_match_listing_missing_ad_is_none():
    assert subject.match_listing(_FakeConn(results=[None]), 501) is None


def test_subject_listing_id_resolves_a_sreality_body_to_its_ad():
    from api import schemas as s

    conn = _FakeConn(results=[(777,)])
    assert er._subject_listing_id(conn, s.CreateEstimationIn(sreality_id=12345)) == 777
    assert conn.executions == [
        ("SELECT id FROM listings WHERE sreality_id = %s", (12345,)),
    ]
    quiet = _FakeConn()
    assert er._subject_listing_id(quiet, s.CreateEstimationIn(listing_id=9)) == 9
    assert quiet.executions == []


# ----------------------------------------------------------------------
# Stuck-row sweep (startup recovery)
# ----------------------------------------------------------------------

def test_sweep_stuck_runs_marks_old_pending_rows_failed():
    """Capture the UPDATE that the lifespan startup sweep emits.

    The function takes a `psycopg.Connection`; for a tractable unit
    test we use a minimal fake that records the executed SQL + params
    and reports a row count via `fetchall`.
    """
    captured: dict[str, Any] = {}

    class _FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return None
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
        def fetchall(self):
            return [(1,), (2,), (3,)]

    class _FakeConn:
        def cursor(self): return _FakeCursor()
        def transaction(self):
            from contextlib import nullcontext
            return nullcontext()

    n = er.sweep_stuck_runs(_FakeConn(), older_than_minutes=15)
    assert n == 3
    assert "status IN ('pending', 'running')" in captured["sql"]
    assert "interrupted by server restart" in captured["sql"]
    assert captured["params"] == (15,)


# ----------------------------------------------------------------------
# POST /estimations with sreality_id (Browse card on-card estimate) +
# GET /estimations/latest-by-listing
# ----------------------------------------------------------------------

def test_post_with_sreality_id_resolves_through_the_same_matcher(client, monkeypatch):
    """A Browse card posts just {sreality_id}; it resolves to the ad's listings.id and
    then to the ONE matcher a {listing_id} body uses."""
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)
    asked = _patch_subject(
        monkeypatch, listing_id=777, source="sreality", native="12345",
        sreality_id=12345, price_czk=3_898_000, category_type="prodej",
        spec={"lat": 50.05, "lng": 14.40, "area_m2": 43.0, "disposition": "1+1", "floor": 2},
    )

    res = client.post("/estimations", json={"sreality_id": 12345})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["input_sreality_id"] == 12345
    assert body["input_listing_id"] == 777
    assert body["input_spec"]["lat"] == 50.05
    assert body["estimate_kind"] == "rent"
    # rent estimate on a 'prodej' listing derives purchase price from the
    # asking price so gross_yield_pct can be computed.
    assert body["input_purchase_price_czk"] == 3_898_000
    assert asked == [777]
    assert state.inserts[1]["input_sreality_id"] == 12345


def test_post_with_unknown_ad_is_422_and_writes_nothing(client, monkeypatch):
    state = _patch_persistence(monkeypatch)
    _patch_estimate(monkeypatch)
    _patch_subject(monkeypatch, listing_id=777, sreality_id=12345)

    res = client.post("/estimations", json={"sreality_id": 999})
    assert res.status_code == 422
    assert "999" in res.json()["detail"]
    res = client.post("/estimations", json={"listing_id": 778})
    assert res.status_code == 422
    assert "778" in res.json()["detail"]
    assert state.inserts == {}


def test_post_with_unlocated_ad_is_422_before_any_gate(client, monkeypatch):
    """The comparables search is spatial: an ad the resolver has not placed is refused
    with the cause, before the metered gate counts it or a row is written."""
    state = _patch_persistence(monkeypatch)
    _patch_subject(monkeypatch, spec={"lat": None, "lng": None, "area_m2": 50.0})
    monkeypatch.setattr(er, "_prepare_metered_submit", lambda *a, **k: pytest.fail(
        "an unlocated subject must be refused before the gate"))

    res = client.post("/estimations", json={"listing_id": 777})
    assert res.status_code == 422
    assert "needs a location" in res.json()["detail"]
    assert state.inserts == {}


def test_post_with_two_subject_handles_returns_422(client):
    res = client.post("/estimations", json={"sreality_id": 1, "listing_id": 2})
    assert res.status_code == 422


def test_latest_by_listing_route_parses_csv(client, monkeypatch):
    captured: dict[str, Any] = {}

    def fake(conn, ids, *, account_ids):
        captured["ids"] = list(ids)
        captured["account_ids"] = list(account_ids)
        return {
            1: {
                "sreality_id": 1, "run_id": 7, "status": "success",
                "estimate_kind": "rent", "gross_yield_pct": 4.2,
                "estimated_monthly_rent_czk": 18000,
                "created_at": "2026-05-04T10:00:00+00:00",
            },
        }

    monkeypatch.setattr(api_main, "latest_rent_estimations_by_listing", fake)
    res = client.get("/estimations/latest-by-listing?sreality_ids=1,2,abc,3")
    assert res.status_code == 200
    body = res.json()
    assert captured["ids"] == [1, 2, 3]
    # The Browse chip read is account-scoped too; the static test token is not
    # an identity, so it resolves to the SYSTEM cohort.
    assert captured["account_ids"] == [SYSTEM_ACCOUNT]
    # JSON object keys are strings
    assert body["estimates"]["1"]["gross_yield_pct"] == 4.2
    assert body["estimates"]["1"]["run_id"] == 7


# ----------------------------------------------------------------------
# latest_rent_estimations_by_listing — the Browse rent chip's data source,
# joined through listings on the surrogate input_listing_id only.
# ----------------------------------------------------------------------

def test_latest_rent_estimations_by_listing_query_keys_on_listing_id():
    conn = _FakeConn(results=[[]])
    er.latest_rent_estimations_by_listing(
        conn, [12345], account_ids=[SYSTEM_ACCOUNT],
    )
    sql, params = conn.executions[0]
    assert "ON er.input_listing_id = l.id" in sql
    assert "input_listing_id IS NULL" not in sql
    assert "FROM listings l" in sql
    assert "JOIN estimation_runs er" in sql
    assert params["ids"] == [12345]
    # The chip is account-scoped BEFORE DISTINCT ON picks, so it can never
    # select a hidden row and then blank it.
    assert "er.account_id = ANY(%(account_ids)s::uuid[])" in sql
    assert params["account_ids"] == [SYSTEM_ACCOUNT]


def test_latest_rent_estimations_by_listing_maps_row_to_dict():
    conn = _FakeConn(results=[[
        (
            12345, 7, "success", "rent", 5.2, 15000,
            datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc),
        ),
    ]])
    out = er.latest_rent_estimations_by_listing(
        conn, [12345], account_ids=[SYSTEM_ACCOUNT],
    )
    assert out == {
        12345: {
            "sreality_id": 12345,
            "run_id": 7,
            "status": "success",
            "estimate_kind": "rent",
            "gross_yield_pct": 5.2,
            "estimated_monthly_rent_czk": 15000,
            "created_at": "2026-07-23T10:00:00+00:00",
        },
    }


def test_latest_rent_estimations_by_listing_empty_ids_skips_query():
    conn = _FakeConn()
    assert er.latest_rent_estimations_by_listing(
        conn, [], account_ids=[SYSTEM_ACCOUNT],
    ) == {}
    assert conn.executions == []


def test_one_ad_has_one_idempotency_key_whichever_handle_names_it(monkeypatch):
    """listing_id and sreality_id bodies naming the same ad share the `ad:` key, so a
    double submit through either handle can never charge twice."""
    from api import schemas as s

    _patch_subject(monkeypatch, listing_id=777, source="sreality", native="12345",
                   sreality_id=12345)
    by_id = s.CreateEstimationIn(listing_id=777)
    by_sid = s.CreateEstimationIn(sreality_id=12345)
    k1 = er._idempotency_key(by_id, er._resolve_input(object(), by_id))
    k2 = er._idempotency_key(by_sid, er._resolve_input(object(), by_sid))
    assert k1 == k2 == "ad:sreality:12345"

    _patch_subject(monkeypatch, listing_id=901, source="bezrealitky", native="998877")
    other = s.CreateEstimationIn(listing_id=901)
    assert er._idempotency_key(other, er._resolve_input(object(), other)) == (
        "ad:bezrealitky:998877")

    spec = s.CreateEstimationIn(spec={"lat": 50.0, "lng": 14.0})
    assert er._idempotency_key(spec, er._resolve_input(object(), spec)).startswith("spec:")


def test_resolve_input_overrides_a_copy_of_the_ads_spec(monkeypatch):
    """The body's overrides land on a COPY of the matched spec; the ad's ids, price and
    category ride along for the yield derivation and the persisted row."""
    from api import schemas as s

    _patch_subject(monkeypatch, listing_id=9, price_czk=3_000_000, category_type="prodej")
    r = er._resolve_input(object(), s.CreateEstimationIn(
        listing_id=9, spec_overrides={"area_m2": 41.0}))
    assert r.target_spec["area_m2"] == 41.0 and _SPEC["area_m2"] == 50.0
    assert (r.input_listing_id, r.input_sreality_id) == (9, None)
    assert (r.subject_source, r.subject_source_id_native) == ("idnes", "65f0c0ffee")
    assert (r.subject_listing_price_czk, r.subject_listing_category_type) == (3_000_000, "prodej")


def test_load_subject_condition_keys_on_the_listing_id():
    conn = _FakeConn(results=[("po_rekonstrukci",)])
    out = er._load_subject_condition(conn, 777)
    sql, params = conn.executions[0]
    assert "WHERE id = %s" in sql and params == (777,)
    assert "condition_level" not in sql  # condition scoring is parked (rule 14)
    assert out == {"condition": "po_rekonstrukci"}
    assert er._load_subject_condition(_FakeConn(), None) is None
