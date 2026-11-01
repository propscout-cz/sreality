"""valuo_valuations (migrations 637 + 646 + 710) and api/valuo.py's statements, against the replayed schema: the row is
saved `pending` before the paid call and finished once, an identical request reuses its answer or meets
its running twin (the double submit pays once), a refusal writes nothing, the CHECKs refuse a priceless
success and an unexplained failure, the comparables' photos land beside the answer, a sandbox answer is
stored as sandbox whatever the token's name (and meets its stored twin through the real index), history is
the caller's own across every ad of the property, and no browser role can read the table. Valuo is a fake.
Runs in CI's migrations job (`TEST_DATABASE_URL`, DB_RAILS_REQUIRED=1); every test rolls back.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from api import valuo, valuo_client
from tests._live_property import REQUIRED_DB, _account, _advert, _property, _recompute

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


def _value(flat: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    return valuo.new_valuation(flat["conn"], flat["pid"], overrides or {}, account_id=flat["account"], cred=CRED)


def test_the_row_is_saved_before_the_call_and_finished_once(cur, flat, fake_valuo):
    out = _value(flat)
    assert out["reused"] is False and out["valuation"]["status"] == "success"
    assert fake_valuo.pending_at_call == [1], "the pending row must exist when Valuo is called"
    [(status, env, sha, request, response, error)] = _rows(cur, flat["account"])
    assert (status, env, sha, error) == ("success", "sandbox", CRED.token_sha, None)
    assert request == out["valuation"]["request"] and request["rating"] == "good"
    assert response == PRICED
    assert out["valuation"]["listing_id"] == flat["lid"]
    assert out["valuation"]["ppm2_basis"] == "sale_capital_czk_m2"


def test_an_identical_request_is_reused_not_paid_again(cur, flat, fake_valuo):
    first = _value(flat)["valuation"]
    again = _value(flat)
    assert again["reused"] is True and again["valuation"]["id"] == first["id"]
    assert len(fake_valuo.pending_at_call) == 1 and len(_rows(cur, flat["account"])) == 1


def test_a_double_submit_meets_its_running_twin_and_pays_once(cur, flat, fake_valuo):
    subject, location = valuo._load(flat["conn"], flat["pid"])
    request, provenance = valuo.build_request(subject, location, {})
    [twin] = valuo._rows(flat["conn"], valuo._INSERT_SQL, (flat["account"], flat["lid"], CRED.environment,
                                                            CRED.token_sha, Jsonb(request), Jsonb(provenance), None))
    assert twin["status"] == "pending"
    with pytest.raises(HTTPException) as err:
        _value(flat)
    assert err.value.status_code == 409
    assert fake_valuo.pending_at_call == [] and len(_rows(cur, flat["account"])) == 1


def test_a_stale_pending_row_is_abandoned_before_the_insert(cur, flat, fake_valuo):
    subject, location = valuo._load(flat["conn"], flat["pid"])
    request, provenance = valuo.build_request(subject, location, {})
    cur.execute("INSERT INTO valuo_valuations (account_id, listing_id, environment, token_sha, request, "
                "provenance, created_at) VALUES (%s, %s, 'sandbox', 'x', %s, %s, now() - interval '3 minutes')",
                (flat["account"], flat["lid"], Jsonb(request), Jsonb(provenance)))
    assert _value(flat)["valuation"]["status"] == "success"
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
    _value(flat)
    pid = _property(cur)
    lid = _advert(cur, pid, source="idnes", condition="dobry")
    _located(cur, lid)
    _recompute(cur, pid)
    out = valuo.new_valuation(flat["conn"], pid, {}, account_id=flat["account"], cred=CRED)
    assert out["reused"] is False and out["valuation"]["listing_id"] == lid
    assert len(fake_valuo.pending_at_call) == 2 and len(_rows(cur, flat["account"])) == 2
    assert [r[3] for r in _rows(cur, flat["account"])][0] == out["valuation"]["request"]


def test_a_valuo_failure_is_a_failed_row_with_valuos_answer(cur, flat, fake_valuo):
    body = {"success": False, "errors": ["Adresa nenalezena"], "form_errors": []}
    fake_valuo.answer = valuo_client.ValuoError("no_price", 200, body)
    out = _value(flat)
    assert out["valuation"]["status"] == "failed"
    assert out["valuation"]["error"] == {"code": "no_price", "message": "Valuo cenu nevrátilo."}
    [(status, _env, _sha, _req, response, error)] = _rows(cur, flat["account"])
    assert status == "failed" and response == body and error["http_status"] == 200


def test_the_comparables_photos_land_beside_the_answer(cur, flat, fake_valuo):
    fake_valuo.answer = PRICED | {"result": PRICED["result"] | {"records": [{"id": 7, "price": 1}]}}
    fake_valuo.adverts = [{"id": 7, "images": ["a/b.jpg"]}]
    [record] = _value(flat)["valuation"]["records"]
    assert record["photos"] == [{"thumb": "https://pics.valuo.cz/pt/a/b.jpg",
                                 "full": "https://pics.valuo.cz/p/a/b.jpg"}]
    cur.execute("SELECT adverts FROM valuo_valuations WHERE account_id = %s", (flat["account"],))
    assert cur.fetchone()[0] == fake_valuo.adverts


def test_a_sandbox_answer_is_sandbox_whatever_the_tokens_name(cur, flat, fake_valuo):
    fake_valuo.answer = PRICED | {"ver": "3.0.0-sandbox"}
    first = _value(flat)["valuation"]
    assert first["environment"] == "sandbox"
    named = valuo_client.Credential("production", "live-test-token")
    again = valuo.new_valuation(flat["conn"], flat["pid"], {}, account_id=flat["account"], cred=named)
    assert again["reused"] is True and again["valuation"]["id"] == first["id"]
    assert [(r[0], r[1], (r[5] or {}).get("code")) for r in _rows(cur, flat["account"])] == [
        ("success", "sandbox", None), ("failed", "sandbox", "duplicate")]


def test_a_refusal_writes_no_row(cur, flat, fake_valuo):
    cur.execute("UPDATE listings SET category_main = 'ostatni' WHERE id = %s", (flat["lid"],))
    _recompute(cur, flat["pid"])
    with pytest.raises(HTTPException) as err:
        _value(flat)
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
    _value(flat)
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


# --- a run's call: the monthly cap, against the real ledger (#2067; two runs racing: tests/test_agent_runs_live.py) ---

RUN = 41


def _by_run(flat: dict[str, Any], *, cap: float = 5, account: Any = None, cred: valuo_client.Credential = CRED,
            run: int = RUN) -> tuple[dict[str, Any], list[int]]:
    slots: list[int] = []
    out = valuo.new_valuation(flat["conn"], flat["pid"], {}, account_id=account or flat["account"], cred=cred,
                              run=valuo.RunCall(run, cap, lambda: slots.append(1)))
    return out, slots


def _refused_by_cap(flat: dict[str, Any], **kw: Any) -> None:
    with pytest.raises(HTTPException) as err:
        _by_run(flat, **kw)
    assert err.value.status_code == 429 and err.value.detail["code"] == valuo.MONTHLY_CAP


def _spent(cur: Any, flat: dict[str, Any], *, status: str = "success", environment: str = "sandbox",
           at: str = "now()", account: Any = None) -> None:
    """A row of the ledger, as any path left it."""
    cur.execute(
        "INSERT INTO valuo_valuations (account_id, listing_id, environment, token_sha, request, provenance, status, "
        f"response, error, created_at) VALUES (%s, %s, %s, 'x', %s, '{{}}', %s, %s, %s, {at})",  # noqa: S608 - fixed
        (account or flat["account"], flat["lid"], environment, Jsonb({"n": uuid.uuid4().hex}), status,
         Jsonb(PRICED) if status == "success" else None,
         Jsonb({"code": "timeout", "message": "m"}) if status == "failed" else None))


def test_a_runs_row_names_its_run_and_the_websites_names_none(cur, flat, fake_valuo):
    out, slots = _by_run(flat)
    assert out["valuation"]["status"] == "success" and slots == [1]
    assert fake_valuo.pending_at_call == [1], "the row is saved and committed before the paid call"
    assert valuo.calls_on_run(flat["conn"], RUN) == 1
    pid = _property(cur)
    lid = _advert(cur, pid, source="idnes", condition="dobry")
    _located(cur, lid)
    _recompute(cur, pid)
    valuo.new_valuation(flat["conn"], pid, {}, account_id=flat["account"], cred=CRED)
    cur.execute("SELECT agent_run_id FROM valuo_valuations WHERE account_id = %s ORDER BY id", (flat["account"],))
    assert [r[0] for r in cur.fetchall()] == [RUN, None]


def test_a_call_past_the_cap_makes_no_paid_request_and_leaves_no_row(cur, flat, fake_valuo):
    _spent(cur, flat)
    _refused_by_cap(flat, cap=1)
    assert fake_valuo.pending_at_call == [], "no paid request"
    assert len(_rows(cur, flat["account"])) == 1, "no pending row"


@pytest.mark.parametrize("status", ["success", "pending", "failed"])
def test_every_row_that_may_have_cost_money_counts(cur, flat, fake_valuo, status):
    _spent(cur, flat, status=status)
    _refused_by_cap(flat, cap=1)
    assert fake_valuo.pending_at_call == []


def test_the_last_slot_can_be_taken_and_a_failure_keeps_it(cur, flat, fake_valuo):
    fake_valuo.answer = valuo_client.ValuoError("unavailable", 503)
    out, slots = _by_run(flat, cap=1)
    assert out["valuation"]["status"] == "failed" and slots == [1]
    assert valuo.calls_on_run(flat["conn"], RUN) == 1
    fake_valuo.answer = PRICED
    _refused_by_cap(flat, cap=1)
    assert len(fake_valuo.pending_at_call) == 1


def test_a_cap_of_zero_allows_nothing(cur, flat, fake_valuo):
    _refused_by_cap(flat, cap=0)
    assert fake_valuo.pending_at_call == [] and _rows(cur, flat["account"]) == []


def test_only_this_accounts_rows_in_this_environment_count(cur, flat, fake_valuo):
    _spent(cur, flat, account=_account(cur))
    _spent(cur, flat, environment="production")
    out, _ = _by_run(flat, cap=1)
    assert out["valuation"]["status"] == "success", "another account's and the other credential's rows are not this cap's"


def test_the_month_is_the_calendar_month_in_prague(cur, flat, fake_valuo):
    month = "date_trunc('month', now(), 'Europe/Prague')"
    _spent(cur, flat, at=f"{month} - interval '1 second'")
    out, _ = _by_run(flat, cap=1)
    assert out["valuation"]["status"] == "success", "the month before does not count"
    cur.execute("DELETE FROM valuo_valuations WHERE account_id = %s", (flat["account"],))
    _spent(cur, flat, at=f"{month} + interval '1 second'")
    _refused_by_cap(flat, cap=1)


def test_the_cap_counts_the_websites_calls_but_never_stops_the_website(cur, flat, fake_valuo):
    _spent(cur, flat)
    _refused_by_cap(flat, cap=1)
    assert _value(flat)["valuation"]["status"] == "success", "the website's POST is not bound"
    assert len(fake_valuo.pending_at_call) == 1


def test_an_answer_already_stored_is_free_at_a_full_cap_and_takes_no_slot(cur, flat, fake_valuo):
    first = _value(flat)["valuation"]
    out, slots = _by_run(flat, cap=1)
    assert out["reused"] is True and out["valuation"]["id"] == first["id"]
    assert slots == [] and len(fake_valuo.pending_at_call) == 1


def test_a_running_twin_takes_no_slot(cur, flat, fake_valuo):
    subject, location = valuo._load(flat["conn"], flat["pid"])
    request, provenance = valuo.build_request(subject, location, {})
    valuo._rows(flat["conn"], valuo._INSERT_SQL, (flat["account"], flat["lid"], CRED.environment, CRED.token_sha,
                                                  Jsonb(request), Jsonb(provenance), None))
    slots: list[int] = []
    with pytest.raises(HTTPException) as err:
        valuo.new_valuation(flat["conn"], flat["pid"], {}, account_id=flat["account"], cred=CRED,
                            run=valuo.RunCall(RUN, 5, lambda: slots.append(1)))
    assert err.value.status_code == 409 and slots == [] and fake_valuo.pending_at_call == []


def test_the_existing_refusals_cost_nothing_and_take_no_slot(cur, flat, fake_valuo):
    slots: list[int] = []
    run = valuo.RunCall(RUN, 5, lambda: slots.append(1))
    with pytest.raises(HTTPException) as unconfigured:
        valuo.new_valuation(flat["conn"], flat["pid"], {}, account_id=flat["account"], cred=None, run=run)
    assert unconfigured.value.status_code == 503
    with pytest.raises(HTTPException) as missing:
        valuo.new_valuation(flat["conn"], 2_000_000_000, {}, account_id=flat["account"], cred=CRED, run=run)
    assert missing.value.status_code == 404
    cur.execute("UPDATE listings SET condition = NULL WHERE id = %s", (flat["lid"],))
    _recompute(cur, flat["pid"])
    with pytest.raises(HTTPException) as blocked:
        valuo.new_valuation(flat["conn"], flat["pid"], {}, account_id=flat["account"], cred=CRED, run=run)
    assert blocked.value.detail["code"] == "missing_required" and blocked.value.detail["fields"] == ["rating"]
    assert slots == [] and fake_valuo.pending_at_call == [] and _rows(cur, flat["account"]) == []


@pytest.mark.parametrize(("stored", "read"), [(None, None), ("null", None), ('"10"', None), ("true", None), ("3", 3),
                                              ("0", 0), ("2.5", 2.5)])
def test_the_cap_is_a_number_or_nothing(cur, flat, stored, read):
    cur.execute("DELETE FROM app_settings WHERE key = %s", (valuo.CAP_SETTING,))
    if stored is not None:
        cur.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s::jsonb)", (valuo.CAP_SETTING, stored))
    assert valuo.monthly_cap(flat["conn"]) == read


def test_the_migration_lands_no_cap_so_no_agent_can_call_valuo_until_the_operator_says() -> None:
    sql = Path(__file__).resolve().parents[1].joinpath("migrations/710_valuo_valuations_agent_run.sql")
    assert f"('{valuo.CAP_SETTING}', 'null'," in sql.read_text(encoding="utf-8")
