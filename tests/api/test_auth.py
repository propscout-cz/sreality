"""Tests for the bearer gate: one credential, a Supabase JWT.

require_token admits any verified JWT and nothing else; it fails CLOSED (401 without
one, 503 with no verifier configured) UNLESS API_AUTH_OPTIONAL=1 — the explicit
local-dev opt-out, never set in production. conftest sets the opt-out for the suite;
the tests below delenv it to exercise the gate.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient
jwt = pytest.importorskip("jwt")  # PyJWT (api extra)

from api import curation as api_curation
from api import dependencies as deps
from tests.api._jwt import mint
from api import feedback as api_feedback
from api import main as api_main
from api import maps as api_maps
from api import pipeline as api_pipeline
from api import shares as api_shares
from api import tenant_pool
from scraper import url_parser as scraper_url_parser


@pytest.fixture()
def client(monkeypatch):
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.get_sreality_client] = lambda: object()
    api_main.app.dependency_overrides[deps.get_llm_client] = lambda: object()
    # tenant-pool routes: the CONNECTION is stubbed, but the stub keeps verify_jwt
    # as its OWN dependency, so the census still walks the real graph. Overriding
    # tenant_conn with a bare `lambda: object()` severed verify_jwt from the chain:
    # a route whose only JWT gate lives inside tenant_conn (or require_account_id)
    # then looked un-gated here, which is why W3 had to leave a declared-but-unread
    # `claims` on GET /pipeline/stages purely to keep this census honest. It no
    # longer does — the gate under test is the one production actually uses.
    def _stub_tenant_conn(claims: dict = fastapi.Depends(deps.verify_jwt)) -> object:
        return object()

    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = _stub_tenant_conn
    # A resolvable account: since W4 the write routes gate on
    # `require_account_id`, which 400s without one — and this census asserts
    # AUTHENTICATION (401 vs 200), not membership. `_ACCT` (defined below with
    # the account_scope tests) is this fixture's caller; the no-account posture
    # itself is asserted in test_pipeline.py / test_curation.py.
    monkeypatch.setattr(
        tenant_pool, "resolve_account_id", lambda conn, claims: _ACCT,
    )

    def fake_find(conn, target, filters):
        return {"data": {"listings": []}, "metadata": {"tool": "find_comparables"}}

    def fake_dist(listings, field):
        return {"data": {"n": 0}, "metadata": {"tool": "analyze_distribution"}}

    def fake_verify(conn, c, sid, max_age):
        return {"data": {"sreality_id": sid}, "metadata": {"tool": "verify_listing_freshness"}}

    def fake_compare(conn, sid, since, listing_id=None):
        return {"data": {"snapshot_count": 0}, "metadata": {"tool": "compare_snapshots"}}

    def fake_estimate(conn, target, filters, purchase_price_czk=None, **_kw):
        return {"data": {"sample_size": 0}, "metadata": {"tool": "estimate_yield"}}

    def fake_neighborhood(conn, **_kw):
        return {"data": {}, "metadata": {"tool": "describe_neighborhood"}}

    def fake_outliers(conn, listings, **_kw):
        return {"data": {"outliers": []}, "metadata": {"tool": "find_distribution_outliers"}}

    def fake_market_vel(conn, target, filters, lifecycle, trend_split_days):
        return {"data": {"cohort_size": 0}, "metadata": {"tool": "compute_market_velocity"}}

    def fake_listing_vel(conn, sreality_id, **_kw):
        return {"data": {"sreality_id": sreality_id, "found": False},
                "metadata": {"tool": "compute_listing_velocity"}}

    def fake_anchors(conn, **_kw):
        return {"data": {"categories": {}, "from_cache": {}},
                "metadata": {"tool": "find_anchor_amenities"}}

    def fake_create_run(conn, c, llm_client, body, background_tasks=None,
                        account_id=None, claims=None):  # POST /estimations keeps
        # its optional account: service-role connection + the SYSTEM arm (mig 291).
        return {"id": 1, "status": "success"}

    def fake_get_run(conn, run_id):
        return {"id": run_id, "status": "success"}

    def fake_list_runs(conn, **_kw):
        return {"data": [], "total": 0, "limit": 50, "offset": 0}

    def fake_update_scenario(conn, run_id, **_kw):
        return {"id": run_id, "status": "success"}

    def fake_parse_url(url, *, client, conn, persist=False):
        return {
            "sreality_id": 2836292428,
            "spec": {"sreality_id": 2836292428, "lat": 50.0, "lon": 14.0},
            "images": [],
            "fetched_at": "2026-05-04T10:00:00+00:00",
            "source_url": url,
            "in_database": False,
        }

    monkeypatch.setattr(api_main, "find_comparables", fake_find)
    monkeypatch.setattr(api_main, "analyze_distribution", fake_dist)
    monkeypatch.setattr(api_main, "verify_listing_freshness", fake_verify)
    monkeypatch.setattr(api_main, "compare_snapshots", fake_compare)
    monkeypatch.setattr(api_main, "estimate_yield", fake_estimate)
    monkeypatch.setattr(api_main, "describe_neighborhood", fake_neighborhood)
    monkeypatch.setattr(api_main, "find_distribution_outliers", fake_outliers)
    monkeypatch.setattr(api_main, "compute_market_velocity", fake_market_vel)
    monkeypatch.setattr(api_main, "compute_listing_velocity", fake_listing_vel)
    monkeypatch.setattr(api_main, "find_anchor_amenities", fake_anchors)
    monkeypatch.setattr(api_main, "create_estimation_run", fake_create_run)
    monkeypatch.setattr(api_main, "get_estimation_run", fake_get_run)
    monkeypatch.setattr(api_main, "list_estimation_runs", fake_list_runs)
    monkeypatch.setattr(api_main, "update_scenario", fake_update_scenario)
    monkeypatch.setattr(api_main, "estimation_run_visible", lambda conn, run_id: True)
    monkeypatch.setattr(api_main, "estimation_run_owned", lambda conn, run_id: True)
    # POST /estimations/{id}/feedback opens its own short tenant transaction in the
    # handler (verify_jwt stays its route dependency, so this census still gates it).
    @contextlib.contextmanager
    def _stub_tenant_transaction(claims: dict, *, act_as: object, method: str):
        yield object()

    monkeypatch.setattr(tenant_pool, "tenant_transaction", _stub_tenant_transaction)
    monkeypatch.setattr(
        api_main, "get_trace_payload",
        lambda conn, run_id, step_n: {"step_n": step_n, "full_output": {}, "captured_at": None},
    )
    monkeypatch.setattr(api_feedback, "list_feedback_for_run", lambda conn, run_id: [])
    monkeypatch.setattr(
        api_feedback, "insert_feedback",
        lambda conn, *, estimation_run_id, feedback_text, initial_status: {
            "id": 1, "estimation_run_id": estimation_run_id, "status": initial_status,
        },
    )
    monkeypatch.setattr(scraper_url_parser, "parse_sreality_url", fake_parse_url)

    # Curation endpoints — gated coverage only; functional tests live in
    # test_curation.py. Stub each handler to a constant successful dict.
    monkeypatch.setattr(
        api_curation, "create_collection",
        lambda conn, body, account_id: {
            "id": 1, "name": body.name, "listing_count": 0,
        },
    )
    monkeypatch.setattr(
        api_curation, "list_collections",
        lambda conn: {"data": [], "total": 0},
    )
    monkeypatch.setattr(
        api_curation, "get_collection",
        lambda conn, cid: {"collection": {"id": cid}, "properties": []},
    )
    monkeypatch.setattr(
        api_curation, "update_collection",
        lambda conn, cid, body: {"id": cid},
    )
    monkeypatch.setattr(
        api_curation, "delete_collection",
        lambda conn, cid: {"deleted": True},
    )
    monkeypatch.setattr(
        api_curation, "add_properties_to_collection",
        lambda conn, cid, body: {"added": len(body.property_ids), "skipped": 0},
    )
    monkeypatch.setattr(
        api_curation, "remove_property_from_collection",
        lambda conn, cid, pid: {"removed": True},
    )
    monkeypatch.setattr(
        api_curation, "create_share",
        lambda conn, identity_conn, cid, body: {"id": cid},
    )
    monkeypatch.setattr(
        api_curation, "delete_share",
        lambda conn, cid, account_id: {"deleted": True},
    )
    monkeypatch.setattr(
        api_curation, "list_notes",
        lambda conn, pid: {"data": []},
    )
    monkeypatch.setattr(
        api_curation, "create_note",
        lambda conn, pid, body, account_id: {
            "id": 1, "property_id": pid, "body": body.body,
        },
    )
    monkeypatch.setattr(
        api_curation, "list_tags",
        lambda conn: {"data": []},
    )
    monkeypatch.setattr(
        api_curation, "create_tag",
        lambda conn, body, account_id: {
            "id": 1, "name": body.name, "color": body.color, "listing_count": 0,
        },
    )
    monkeypatch.setattr(
        api_curation, "delete_tag",
        lambda conn, tid: {"deleted": True},
    )
    monkeypatch.setattr(
        api_curation, "update_tag",
        lambda conn, tid, body: {
            "id": tid, "name": body.name or "hot",
            "color": body.color or "brick", "listing_count": 0,
        },
    )
    monkeypatch.setattr(
        api_curation, "attach_tag",
        lambda conn, sid, body: {"attached": True},
    )
    monkeypatch.setattr(
        api_curation, "detach_tag",
        lambda conn, sid, tid: {"detached": True},
    )
    monkeypatch.setattr(
        api_pipeline, "list_stages",
        lambda conn: {"data": []},
    )
    monkeypatch.setattr(
        api_pipeline, "add_card",
        lambda conn, body, *, account_id: {
            "property_id": body.property_id, "added": True,
        },
    )
    monkeypatch.setattr(
        api_pipeline, "remove_card",
        lambda conn, pid, *, account_id: {"removed": True},
    )
    monkeypatch.setattr(
        api_pipeline, "move_card",
        lambda conn, pid, body, *, account_id: {
            "property_id": pid, "stage_id": body.stage_id,
        },
    )

    monkeypatch.setattr(
        api_shares, "list_pipeline_shares",
        lambda conn: {"shared_with": [], "shared_with_me": []},
    )
    monkeypatch.setattr(api_shares, "grant", lambda open_identity_conn, email, share: None)
    monkeypatch.setattr(
        api_shares, "revoke_pipeline", lambda conn, party, side: {"deleted": True},
    )

    monkeypatch.setattr(api_maps, "suggest", lambda *a, **kw: {"items": []})
    monkeypatch.setattr(
        api_maps,
        "resolve",
        lambda *a, **kw: {"level": None, "id": None, "name": None, "context": None},
    )

    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


_CAT = {"category_main": "byt", "category_type": "pronajem"}
_FIND_BODY = {"target": {"lat": 50.0, "lng": 14.0}, **_CAT}
_DIST_BODY = {"listings": [], "field": "price_per_m2"}
_VERIFY_BODY = {"sreality_id": 1, "max_age_hours": 24}
_COMPARE_BODY = {"sreality_id": 1}
_ESTIMATE_BODY = {"target": {"lat": 50.0, "lng": 14.0, "area_m2": 50.0}, **_CAT}
_NEIGHBORHOOD_BODY = {"lat": 50.0, "lng": 14.0, **_CAT}
_OUTLIERS_BODY = {"listings": []}
_MARKET_VEL_BODY = {"target": {"lat": 50.0, "lng": 14.0}, **_CAT}
_LISTING_VEL_BODY = {"sreality_id": 1}
_ANCHORS_BODY = {"lat": 50.0, "lng": 14.0}
_CREATE_ESTIMATION_BODY = {"spec": {"lat": 50.0, "lng": 14.0, "area_m2": 50.0}}
_RESOLVE_BODY = {"lat": 50.0, "lng": 14.0, "type": "regional.municipality", "name": "x"}
_CREATE_COLLECTION_BODY = {"name": "test"}
_PATCH_COLLECTION_BODY = {"name": "renamed"}
_ADD_PROPERTIES_BODY = {"property_ids": [1]}
_CREATE_NOTE_BODY = {"body": "smoke"}
_CREATE_TAG_BODY = {"name": "hot", "color": "brick"}
_PATCH_TAG_BODY = {"name": "renamed", "color": "sage"}
_ATTACH_TAG_BODY = {"tag_id": 1}
_PIPELINE_CARD_BODY = {"property_id": 1}
_FEEDBACK_BODY = {"feedback_text": "too broad", "kick_off_refinement": False}


def _gated_calls(client) -> list:
    return [
        ("POST", "/tools/find_comparables", _FIND_BODY),
        ("POST", "/tools/analyze_distribution", _DIST_BODY),
        ("POST", "/tools/verify_listing_freshness", _VERIFY_BODY),
        ("POST", "/tools/compare_snapshots", _COMPARE_BODY),
        ("POST", "/tools/describe_neighborhood", _NEIGHBORHOOD_BODY),
        ("POST", "/tools/find_distribution_outliers", _OUTLIERS_BODY),
        ("POST", "/tools/compute_market_velocity", _MARKET_VEL_BODY),
        ("POST", "/tools/compute_listing_velocity", _LISTING_VEL_BODY),
        ("POST", "/tools/find_anchor_amenities", _ANCHORS_BODY),
        ("POST", "/estimate_yield", _ESTIMATE_BODY),
        ("GET", "/estimations", None),
        ("GET", "/maps/suggest?query=foo", None),
        ("POST", "/maps/resolve", _RESOLVE_BODY),
        ("GET", "/estimations/preview?url=https://www.sreality.cz/detail/x/2836292428", None),
    ]


def _jwt_gated_calls() -> list:
    """Routes on verify_jwt / tenant_pool.tenant_conn (Phase 1): fail CLOSED
    even under the local-dev opt-out (API_AUTH_OPTIONAL=1 opens require_token
    routes only). The Wave 1 W1-1 batch (/estimations create/read/scenario,
    /collections list + /properties writes, /properties/{id}/notes) moved
    here from _gated_calls in lockstep with api/main.py.

    The curation stragglers — collection CRUD and every /tags route — followed
    in the hydration sprint's W-1c: on the service-role pair they were
    RLS-exempt behind a token that ships in the public SPA bundle, so any
    holder could read or delete another account's curation. api/curation.py's
    header states the rule; this list is its test-side twin. The estimation
    trace-payload + feedback routes followed on 2026-10-02 for the same reason
    (any account's trace, and refiner LLM spend on any run). Nothing in it may
    move back to _gated_calls."""
    return [
        ("GET",    "/pipeline/stages", None),
        ("POST",   "/pipeline/cards", _PIPELINE_CARD_BODY),
        ("PATCH",  "/pipeline/cards/1", {"stage_id": 1}),
        ("DELETE", "/pipeline/cards/1", None),
        ("GET",    "/pipeline/shares", None),
        ("POST",   "/pipeline/shares", {"email": "b@example.com", "mode": "read"}),
        ("DELETE", "/pipeline/shares/33333333-3333-3333-3333-333333333333?side=shared_with", None),
        ("POST",   "/estimations", _CREATE_ESTIMATION_BODY),
        ("GET",    "/estimations/1", None),
        ("PATCH",  "/estimations/1/scenario", {"rent_czk": 15000}),
        ("GET",    "/estimations/1/trace/1/payload", None),
        ("GET",    "/estimations/1/feedback", None),
        ("POST",   "/estimations/1/feedback", _FEEDBACK_BODY),
        ("GET",    "/collections", None),
        ("POST",   "/collections/1/properties", _ADD_PROPERTIES_BODY),
        ("DELETE", "/collections/1/properties/2", None),
        ("GET",    "/properties/1/notes", None),
        ("POST",   "/properties/1/notes", _CREATE_NOTE_BODY),
        ("POST",   "/collections", _CREATE_COLLECTION_BODY),
        ("GET",    "/collections/1", None),
        ("PATCH",  "/collections/1", _PATCH_COLLECTION_BODY),
        ("DELETE", "/collections/1", None),
        ("POST",   "/collections/1/shares", {"email": "b@example.com"}),
        ("DELETE", "/collections/1/shares/33333333-3333-3333-3333-333333333333", None),
        ("GET",    "/tags", None),
        ("POST",   "/tags", _CREATE_TAG_BODY),
        ("PATCH",  "/tags/1", _PATCH_TAG_BODY),
        ("DELETE", "/tags/1", None),
        ("POST",   "/properties/1/tags", _ATTACH_TAG_BODY),
        ("DELETE", "/properties/1/tags/1", None),
    ]


def _call(client, method: str, path: str, body, headers=None):
    if method == "POST":
        return client.post(path, json=body, headers=headers or {})
    if method == "PATCH":
        return client.patch(path, json=body, headers=headers or {})
    if method == "DELETE":
        return client.delete(path, headers=headers or {})
    return client.get(path, headers=headers or {})


def test_optout_opens_token_routes(client, monkeypatch):
    """Local-dev opt-out: API_AUTH_OPTIONAL=1 → every require_token route is open."""
    monkeypatch.setenv("API_AUTH_OPTIONAL", "1")

    res = client.get("/health")
    assert res.status_code == 200

    for method, path, body in _gated_calls(client):
        res = _call(client, method, path, body)
        assert res.status_code == 200, f"{path} should be open with the opt-out"


def test_unconfigured_fails_closed(client, monkeypatch):
    """No opt-out and no verifier (SUPABASE_URL unset) → every gated route 503, never
    open. /health stays open (it carries no gate)."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    assert client.get("/health").status_code == 200

    for method, path, body in _gated_calls(client):
        res = _call(client, method, path, body)
        assert res.status_code == 503, f"{path} must fail closed when auth is unconfigured"


def test_missing_header_rejects(client, monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)

    for method, path, body in _gated_calls(client):
        res = _call(client, method, path, body)
        assert res.status_code == 401, f"{path} should reject a missing bearer"


def test_wrong_bearer_rejects(client, monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    headers = {"Authorization": "Bearer wrong-token"}

    for method, path, body in _gated_calls(client):
        res = _call(client, method, path, body, headers=headers)
        assert res.status_code == 401, f"{path} should reject a non-JWT bearer"


def test_health_always_open(client, monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)

    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_malformed_authorization_header_rejected(client, monkeypatch):
    """Plain token (no `Bearer` prefix) is treated as missing."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    headers = {"Authorization": "secret-token-xyz"}

    res = client.post("/tools/find_comparables", json=_FIND_BODY, headers=headers)
    assert res.status_code == 401


def test_token_routes_accept_real_jwt(client, monkeypatch):
    """One caller, one credential: a human's session JWT passes the require_token
    routes in `_gated_calls` too, so the SPA sends it everywhere and a route moves
    between gates with no client change (the deploy-skew class behind the 2026-10-13
    "Invalid token" preset incident: a stale bundle met a route that had just moved
    to verify_jwt)."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    headers = {"Authorization": f"Bearer {mint({'sub': _ACCT})}"}

    for method, path, body in _gated_calls(client):
        res = _call(client, method, path, body, headers=headers)
        assert res.status_code == 200, f"{path} should accept a verified JWT"


def test_token_routes_reject_forged_jwt(client, monkeypatch):
    """A JWS-shaped bearer that fails verification is a 401 that names the reason
    (RFC 6750 challenge) — never a pass, never a 503. The forgery is a structurally
    valid ES256 token signed by a key the JWKS does not serve, so the failure is the
    SIGNATURE on every PyJWT version (byte-tampering the signature segment is
    classified `malformed` on some, `bad signature` on others)."""
    from cryptography.hazmat.primitives.asymmetric import ec

    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    forged = jwt.encode(
        {"aud": "authenticated", "sub": _ACCT},
        ec.generate_private_key(ec.SECP256R1()), algorithm="ES256",
    )
    headers = {"Authorization": f"Bearer {forged}"}

    for method, path, body in _gated_calls(client):
        res = _call(client, method, path, body, headers=headers)
        assert res.status_code == 401, f"{path} should reject a forged JWT"
        assert res.json()["detail"] == "Invalid token: bad signature"
        assert 'error="invalid_token"' in res.headers["WWW-Authenticate"]


def test_bearer_non_ascii_header_is_401(monkeypatch):
    """Starlette decodes headers as latin-1; a non-ASCII bearer must stay a clean 401
    through verify_jwt's decode, with an ASCII challenge."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    for bearer in ("Bearer sécret", "Bearer é.é.é"):
        with pytest.raises(fastapi.HTTPException) as exc:
            deps.require_token(authorization=bearer)
        assert exc.value.status_code == 401, bearer
        assert exc.value.headers["WWW-Authenticate"].isascii()


# --- verify_jwt-gated routes (Phase 1 tenant pool) --------------------------


def test_jwt_routes_fail_closed_without_token(client, monkeypatch):

    for method, path, body in _jwt_gated_calls():
        res = _call(client, method, path, body)
        assert res.status_code == 401, f"{path} must fail closed with no bearer"


def test_jwt_routes_reject_wrong_token(client, monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    headers = {"Authorization": "Bearer wrong-token"}

    for method, path, body in _jwt_gated_calls():
        res = _call(client, method, path, body, headers=headers)
        assert res.status_code == 401, f"{path} should reject a garbage non-JWT token"


def test_jwt_routes_accept_real_jwt(client, monkeypatch):
    """A real Supabase-signed JWT (no admin claim needed — these routes gate on
    identity via verify_jwt/tenant_conn, not require_admin) passes."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    headers = {"Authorization": f"Bearer {mint({'sub': '11111111-1111-1111-1111-111111111111'})}"}

    for method, path, body in _jwt_gated_calls():
        res = _call(client, method, path, body, headers=headers)
        assert res.status_code == 200, f"{path} should pass with a real JWT"


def test_a_configured_legacy_key_is_not_a_credential(client, monkeypatch):
    """The deleted static key must stay deleted: with API_TOKEN set in the
    environment (old bundles still carry such a string), presenting it is a 401 on
    every gate and never widens account_scope — a re-added static branch fails here."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.setenv("API_TOKEN", "legacy-static-key")
    headers = {"Authorization": "Bearer legacy-static-key"}

    for method, path, body in [*_gated_calls(client), *_jwt_gated_calls()]:
        res = _call(client, method, path, body, headers=headers)
        assert res.status_code == 401, f"{path} accepted the retired static key"
    with pytest.raises(fastapi.HTTPException) as exc:
        deps.account_scope(authorization="Bearer legacy-static-key", conn=object())
    assert exc.value.status_code == 401


# ----------------------------------------------------------------------
# deps.account_scope — the read-scope gate behind the account-scoped estimation
# reads (GET /estimations, GET /estimations/latest-by-listing).
#
# These routes serve every signed-in caller (the SPA, the extension, a machine
# signed in as its own Supabase user) over the single Authorization header.
# Scope is asserted on the RESOLVED ACCOUNT LIST, not on row counts: a
# mis-wire yields a shrunken-but-plausible page that no human would notice.
# ----------------------------------------------------------------------

_ACCT = "e93e2b77-56f1-4a56-903b-204e4f0e4e90"


def test_account_scope_optout_is_system_only(monkeypatch):
    """The local-dev opt-out is NOT an identity, so it may never widen scope beyond
    the shared SYSTEM cohort."""
    monkeypatch.setenv("API_AUTH_OPTIONAL", "1")
    assert deps.account_scope(authorization=None, conn=object()) == [deps.SYSTEM_ACCOUNT_ID]


def test_account_scope_jwt_adds_own_account_and_keeps_system(monkeypatch):
    """SYSTEM stays in scope alongside the caller's account, mirroring the
    estimation_runs_tenant_read policy (migration 291). Dropping it would hide
    the operator's own watchdog-produced runs, which carry the SYSTEM default."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.setattr(
        tenant_pool, "resolve_account_id", lambda conn, claims: _ACCT,
    )
    tok = mint({"sub": "11111111-1111-1111-1111-111111111111"})
    assert deps.account_scope(
        authorization=f"Bearer {tok}", conn=object(),
    ) == [_ACCT, deps.SYSTEM_ACCOUNT_ID]


def test_account_scope_unresolvable_jwt_falls_back_to_system_only(monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.setattr(
        tenant_pool, "resolve_account_id", lambda conn, claims: None,
    )
    tok = mint({"sub": "11111111-1111-1111-1111-111111111111"})
    assert deps.account_scope(
        authorization=f"Bearer {tok}", conn=object(),
    ) == [deps.SYSTEM_ACCOUNT_ID]


def test_account_scope_never_returns_an_empty_scope(monkeypatch):
    """An empty list would reach `ANY('{}')` and match nothing — a silent blank
    page. Every branch must return at least SYSTEM or raise."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    for resolved in (None, _ACCT):
        monkeypatch.setattr(
            tenant_pool, "resolve_account_id", lambda conn, claims, r=resolved: r,
        )
        tok = mint({"sub": "11111111-1111-1111-1111-111111111111"})
        assert deps.account_scope(authorization=f"Bearer {tok}", conn=object())


def test_account_scope_wrong_bearer_is_401(monkeypatch):
    """A bearer that is not a verifiable JWT is a bad credential (401), never a pass
    and never a widened scope."""
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    for bearer in ("Bearer wrong-token", "Bearer a.b.c"):
        with pytest.raises(fastapi.HTTPException) as exc:
            deps.account_scope(authorization=bearer, conn=object())
        assert exc.value.status_code == 401, bearer


def test_account_scope_fails_closed_when_gate_unconfigured(monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    with pytest.raises(fastapi.HTTPException) as exc:
        deps.account_scope(authorization="Bearer anything", conn=object())
    assert exc.value.status_code == 503


def test_account_scope_missing_header_is_401(monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    with pytest.raises(fastapi.HTTPException) as exc:
        deps.account_scope(authorization=None, conn=object())
    assert exc.value.status_code == 401


def test_tenant_conn_without_pool_dsn_raises(monkeypatch):
    """No fallback connection exists any more: an unset TENANT_POOL_DB_URL must
    fail loudly. The retired legacy branch used to absorb a bad/absent DSN into
    a silently unscoped service-role connection, which is how the bad-DSN
    incident stayed invisible (roadmap/public-release-track.md)."""
    monkeypatch.delenv("TENANT_POOL_DB_URL", raising=False)
    gen = tenant_pool.tenant_conn(claims={"sub": _ACCT}, act_as=None, method="GET")
    with pytest.raises(RuntimeError, match="TENANT_POOL_DB_URL"):
        next(gen)
