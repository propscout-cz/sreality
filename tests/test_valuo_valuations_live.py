"""valuo_valuations (migrations 637 + 646) and api/valuo.py's statements, against the replayed schema: the row is
saved `pending` before the paid call and finished once, an identical request reuses its answer or meets
its running twin (the double submit pays once), a refusal writes nothing, the CHECKs refuse a priceless
success and an unexplained failure, the comparables' photos land beside the answer, a sandbox answer is
stored as sandbox whatever the token's name (and meets its stored twin through the real index), history is
the caller's own across every ad of the property, and no browser role can read the table. Valuo is a fake.
Runs in CI's migrations job (`TEST_DATABASE_URL`, DB_RAILS_REQUIRED=1); every test rolls back.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from api import valuo, valuo_client
from tests._live_property import REQUIRED_DB, _account, _advert, _property, _recompute, cur  # noqa: F401

pytestmark = REQUIRED_DB

CRED = valuo_client.Credential("sandbox", "live-test-token")
PRICED = {"success": True, "ignored_ids": [], "result": {"avg_price": 6_000_000, "avg_price_m2": 85_700}}


def _located(cur: Any, listing_id: int) -> None:
    cur.execute(
        "INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, "
        "  uncertainty_radius_m, country_status, resolver_version, claim_set_hash, "
        "  registry_version) VALUES (%s, ST_SetSRID(ST_MakePoint(14.42, 50.08), 4326), "
        "  'exact', 'building', 5, 'cz', 'test', '\\x00'::bytea, 'test')", (listing_id,))


@pytest.fixture()
def flat(cur: Any) -> dict[str, Any]:
    """A located flat (condition stated, so nothing blocks) and an account to value it for."""
    pid = _property(cur)
    lid = _advert(cur, pid, source="idnes", condition="dobry")
    _located(cur, lid)
    _recompute(cur, pid)
    return {"pid": pid, "lid": lid, "account": _account(cur), "conn": cur.connection}


class _Valuo:
    """The fake: `answer` is returned (or raised); `pending_at_call` counts the pending rows each call saw;
    `adverts` answers the photo call."""

    def __init__(self) -> None:
        self.answer: Any = PRICED
        self.adverts: Any = []
        self.pending_at_call: list[int] = []


@pytest.fixture()
def fake_valuo(monkeypatch, cur: Any) -> _Valuo:
    fake = _Valuo()

    def calculate(cred: valuo_client.Credential, request: dict[str, Any]) -> dict[str, Any]:
        cur.execute("SELECT count(*) FROM valuo_valuations WHERE status = 'pending'")
        fake.pending_at_call.append(int(cur.fetchone()[0]))
        if isinstance(fake.answer, Exception):
            raise fake.answer
        return fake.answer

    monkeypatch.setattr(valuo_client, "calculate", calculate)
    monkeypatch.setattr(valuo_client, "advert_details", lambda cred, ids: fake.adverts)
    return fake


def _rows(cur: Any, account: Any) -> list[tuple[Any, ...]]:
    cur.execute("SELECT status, environment, token_sha, request, response, error FROM valuo_valuations "
                "WHERE account_id = %s ORDER BY id", (account,))
    return cur.fetchall()


def _run(flat: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    return valuo.run(flat["conn"], flat["pid"], overrides or {}, account_id=flat["account"], cred=CRED)


def test_the_row_is_saved_before_the_call_and_finished_once(cur, flat, fake_valuo):
    out = _run(flat)
    assert out["reused"] is False and out["valuation"]["status"] == "success"
    assert fake_valuo.pending_at_call == [1], "the pending row must exist when Valuo is called"
    [(status, env, sha, request, response, error)] = _rows(cur, flat["account"])
    assert (status, env, sha, error) == ("success", "sandbox", CRED.token_sha, None)
    assert request == out["valuation"]["request"] and request["rating"] == "good"
    assert response == PRICED
    assert out["valuation"]["listing_id"] == flat["lid"]
    assert out["valuation"]["ppm2_basis"] == "sale_capital_czk_m2"


def test_an_identical_request_is_reused_not_paid_again(cur, flat, fake_valuo):
    first = _run(flat)["valuation"]
    again = _run(flat)
    assert again["reused"] is True and again["valuation"]["id"] == first["id"]
    assert len(fake_valuo.pending_at_call) == 1 and len(_rows(cur, flat["account"])) == 1


def test_a_double_submit_meets_its_running_twin_and_pays_once(cur, flat, fake_valuo):
    subject, location = valuo._load(flat["conn"], flat["pid"])
    request, provenance = valuo.build_request(subject, location, {})
    [twin] = valuo._rows(flat["conn"], valuo._INSERT_SQL, (flat["account"], flat["lid"], CRED.environment,
                                                            CRED.token_sha, Jsonb(request), Jsonb(provenance)))
    assert twin["status"] == "pending"
    with pytest.raises(HTTPException) as err:
        _run(flat)
    assert err.value.status_code == 409
    assert fake_valuo.pending_at_call == [] and len(_rows(cur, flat["account"])) == 1


def test_a_stale_pending_row_is_abandoned_before_the_insert(cur, flat, fake_valuo):
    subject, location = valuo._load(flat["conn"], flat["pid"])
    request, provenance = valuo.build_request(subject, location, {})
    cur.execute("INSERT INTO valuo_valuations (account_id, listing_id, environment, token_sha, request, "
                "provenance, created_at) VALUES (%s, %s, 'sandbox', 'x', %s, %s, now() - interval '3 minutes')",
                (flat["account"], flat["lid"], Jsonb(request), Jsonb(provenance)))
    assert _run(flat)["valuation"]["status"] == "success"
    statuses = [(r[0], (r[5] or {}).get("code")) for r in _rows(cur, flat["account"])]
    assert statuses == [("failed", "abandoned"), ("success", None)]


def test_the_page_settles_a_call_the_api_died_during(cur, flat):
    cur.execute("INSERT INTO valuo_valuations (account_id, listing_id, environment, token_sha, request, "
                "provenance, created_at) VALUES (%s, %s, 'sandbox', 'x', '{}', '{}', now() - interval '3 minutes')",
                (flat["account"], flat["lid"]))
    [row] = valuo.page(flat["conn"], flat["pid"], account_id=flat["account"], cred=CRED)["history"]
    assert (row["status"], row["error"]["code"]) == ("failed", "abandoned")


def test_another_propertys_identical_request_is_its_own_row(cur, flat, fake_valuo):
    """The ad is in the dedupe key: a byte-identical request (same pin, area, rating) for a second property
    is paid and stored for it, never answered with a row its own history cannot show."""
    _run(flat)
    pid = _property(cur)
    lid = _advert(cur, pid, source="idnes", condition="dobry")
    _located(cur, lid)
    _recompute(cur, pid)
    out = valuo.run(flat["conn"], pid, {}, account_id=flat["account"], cred=CRED)
    assert out["reused"] is False and out["valuation"]["listing_id"] == lid
    assert len(fake_valuo.pending_at_call) == 2 and len(_rows(cur, flat["account"])) == 2
    assert [r[3] for r in _rows(cur, flat["account"])][0] == out["valuation"]["request"]


def test_a_valuo_failure_is_a_failed_row_with_valuos_answer(cur, flat, fake_valuo):
    body = {"success": False, "errors": ["Adresa nenalezena"], "form_errors": []}
    fake_valuo.answer = valuo_client.ValuoError("no_price", 200, body)
    out = _run(flat)
    assert out["valuation"]["status"] == "failed"
    assert out["valuation"]["error"] == {"code": "no_price", "message": "Valuo cenu nevrátilo."}
    [(status, _env, _sha, _req, response, error)] = _rows(cur, flat["account"])
    assert status == "failed" and response == body and error["http_status"] == 200


def test_the_comparables_photos_land_beside_the_answer(cur, flat, fake_valuo):
    fake_valuo.answer = PRICED | {"result": PRICED["result"] | {"records": [{"id": 7, "price": 1}]}}
    fake_valuo.adverts = [{"id": 7, "images": ["a/b.jpg"]}]
    [record] = _run(flat)["valuation"]["records"]
    assert record["photos"] == [{"thumb": "https://pics.valuo.cz/pt/a/b.jpg",
                                 "full": "https://pics.valuo.cz/p/a/b.jpg"}]
    cur.execute("SELECT adverts FROM valuo_valuations WHERE account_id = %s", (flat["account"],))
    assert cur.fetchone()[0] == fake_valuo.adverts


def test_a_sandbox_answer_is_sandbox_whatever_the_tokens_name(cur, flat, fake_valuo):
    fake_valuo.answer = PRICED | {"ver": "3.0.0-sandbox"}
    first = _run(flat)["valuation"]
    assert first["environment"] == "sandbox"
    named = valuo_client.Credential("production", "live-test-token")
    again = valuo.run(flat["conn"], flat["pid"], {}, account_id=flat["account"], cred=named)
    assert again["reused"] is True and again["valuation"]["id"] == first["id"]
    assert [(r[0], r[1], (r[5] or {}).get("code")) for r in _rows(cur, flat["account"])] == [
        ("success", "sandbox", None), ("failed", "sandbox", "duplicate")]


def test_a_refusal_writes_no_row(cur, flat, fake_valuo):
    cur.execute("UPDATE listings SET category_main = 'ostatni' WHERE id = %s", (flat["lid"],))
    _recompute(cur, flat["pid"])
    with pytest.raises(HTTPException) as err:
        _run(flat)
    assert err.value.status_code == 422 and err.value.detail["code"] == "unsupported_type"
    assert _rows(cur, flat["account"]) == [] and fake_valuo.pending_at_call == []


@pytest.mark.parametrize(("status", "response", "error", "environment"), [
    ("success", {"result": {}}, None, "sandbox"),
    ("failed", None, None, "sandbox"),
    ("pending", None, {"code": "x"}, "sandbox"),
    ("pending", None, None, "staging"),
])
def test_the_checks_refuse_an_incoherent_row(cur, flat, status, response, error, environment):
    with pytest.raises(psycopg.errors.CheckViolation), flat["conn"].transaction():
        cur.execute("INSERT INTO valuo_valuations (account_id, listing_id, environment, token_sha, request, "
                    "provenance, status, response, error) VALUES (%s, %s, %s, 'x', '{}', '{}', %s, %s, %s)",
                    (flat["account"], flat["lid"], environment, status,
                     None if response is None else Jsonb(response), None if error is None else Jsonb(error)))


def test_history_is_the_callers_own_across_every_ad(cur, flat, fake_valuo):
    other_ad = _advert(cur, flat["pid"], source="sreality", condition="dobry")
    other_account = _account(cur)
    _run(flat)
    for account, lid in ((flat["account"], other_ad), (other_account, flat["lid"])):
        cur.execute("INSERT INTO valuo_valuations (account_id, listing_id, environment, token_sha, request, "
                    "provenance, status, error) VALUES (%s, %s, 'sandbox', 'x', %s, '{}', 'failed', %s)",
                    (account, lid, Jsonb({"n": lid}), Jsonb({"code": "timeout", "message": "m"})))
    page = valuo.page(flat["conn"], flat["pid"], account_id=flat["account"], cred=CRED)
    assert [h["listing_id"] for h in page["history"]] == [other_ad, flat["lid"]]
    assert page["refusal"] is None and page["environment"] == "sandbox"


def test_no_browser_role_reads_the_table(cur):
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.valuo_valuations'::regclass")
    assert cur.fetchone()[0] is True
    for role in ("anon", "authenticated"):
        cur.execute("SELECT has_table_privilege(%s, 'public.valuo_valuations', 'SELECT')", (role,))
        assert cur.fetchone()[0] is False
