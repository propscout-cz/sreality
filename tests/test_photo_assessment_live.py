"""The photo assessment against the replayed schema (#2066): its SQL picks the photo set, a second request is served
from the real cache with no model call, the model call lands on the ledger migration 705 admits, and the browser's
roles read nothing of the cache. Runs in CI's migrations job (`TEST_DATABASE_URL`)."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from api.capabilities import registry, run_scope
from api.capabilities.entry import Caller
from api.capabilities.errors import ToolError, ToolErrorCode
from api.llm_client import LLMClient
from api.providers import ModelPrice
from scraper import image_storage
from tests._live_property import REQUIRED_DB, _advert, _property
from tests.api._agent_fixtures import reply
from tests.api._fakes import _ScriptedProvider
from toolkit import photo_assessment as pa

pytestmark = REQUIRED_DB

NAME = "assess_property_visuals"
MODEL = pa.DEFAULT_MODEL
PRICE = ModelPrice(4.0, 20.0, 0.2, 5.0)


def _image(cur: Any, listing_id: int, sequence: int | None, *, stored: bool = True, tag: str | None = None,
           unavailable: str | None = None, rendition: str | None = None) -> int:
    path = f"img/{listing_id}/{uuid.uuid4().hex}.jpg" if stored else None
    cur.execute("INSERT INTO images (listing_id, sequence, sreality_url, storage_path, unavailable_reason, rendition, "
                "stored_width, stored_height) VALUES (%s, %s, %s, %s, %s, %s, 800, 600) RETURNING id",
                (listing_id, sequence, f"https://cdn.test/{uuid.uuid4().hex}.jpg", path, unavailable, rendition))
    image_id = int(cur.fetchone()[0])
    if tag:
        cur.execute("INSERT INTO image_clip_tags (image_id, fine_tag, logical_tag, confidence, model) "
                    "VALUES (%s, %s, %s, 0.9, 'test')", (image_id, tag, tag))
    return image_id


def _canonical(cur: Any, pid: int, listing_id: int) -> None:
    cur.execute("UPDATE properties SET repr_listing_ref_id = %s, location_listing_ref_id = %s WHERE id = %s",
                (listing_id, listing_id, pid))


def _placed(cur: Any) -> tuple[int, int]:
    pid = _property(cur)
    ad = _advert(cur, pid, source="bazos")
    _canonical(cur, pid, ad)
    return pid, ad


def test_a_photo_set_is_the_canonical_ads_stored_pictures_in_its_order(cur: Any) -> None:
    pid, ad = _placed(cur)
    other = _advert(cur, pid, source="idnes")
    _image(cur, other, 1)
    third = _image(cur, ad, 3)
    first = _image(cur, ad, 1)
    plan = _image(cur, ad, 2, tag="floor_plan")
    _image(cur, ad, 4, stored=False)
    _image(cur, ad, 5, stored=False, unavailable="gone")
    photos = pa.photo_set(cur.connection, pid)
    assert photos.listing_id == ad
    assert [p.image_id for p in photos.photos] == [first, third]
    assert [p.image_id for p in photos.plans] == [plan]
    assert photos.stored == 3 and photos.pending == 1


def test_a_photo_is_on_its_way_only_on_a_live_ad_and_below_the_attempts_the_download_gives_up_at(cur: Any) -> None:
    pid, ad = _placed(cur)
    _image(cur, ad, 1)
    waiting = _image(cur, ad, 2, stored=False)
    assert pa.photo_set(cur.connection, pid).pending == 1
    cur.execute("UPDATE images SET download_attempts = %s WHERE id = %s", (pa.DOWNLOAD_GIVES_UP_AT, waiting))
    assert pa.photo_set(cur.connection, pid).pending == 0, "given up: it is never coming"
    cur.execute("UPDATE images SET download_attempts = 0 WHERE id = %s", (waiting,))
    cur.execute("UPDATE listings SET is_active = false WHERE id = %s", (ad,))
    assert pa.photo_set(cur.connection, pid).pending == 0, "an ended ad holds nothing back"


def test_two_tagging_models_tied_on_time_give_one_answer(cur: Any) -> None:
    pid, ad = _placed(cur)
    image = _image(cur, ad, 1)
    for model, tag in (("b", "kitchen"), ("a", "floor_plan")):
        cur.execute("INSERT INTO image_clip_tags (image_id, fine_tag, logical_tag, confidence, model, tagged_at) "
                    "VALUES (%s, %s, %s, 0.9, %s, '2026-10-01T00:00:00Z')", (image, tag, tag, model))
    assert [p.image_id for p in pa.photo_set(cur.connection, pid).plans] == [image]


def test_the_newest_tag_decides_a_plan_and_a_new_stored_photo_is_a_new_set(cur: Any) -> None:
    pid, ad = _placed(cur)
    image = _image(cur, ad, 1, tag="floor_plan")
    cur.execute("INSERT INTO image_clip_tags (image_id, fine_tag, logical_tag, confidence, model, tagged_at) "
                "VALUES (%s, 'kitchen', 'kitchen', 0.9, 'older', now() - interval '1 day')", (image,))
    before = pa.photo_set(cur.connection, pid)
    assert [p.image_id for p in before.plans] == [image] and pa.photo_set(cur.connection, pid).hash == before.hash
    _image(cur, ad, 2)
    assert pa.photo_set(cur.connection, pid).hash != before.hash


def test_a_re_mastered_photo_is_a_new_set(cur: Any) -> None:
    pid, ad = _placed(cur)
    image = _image(cur, ad, 1)
    before = pa.photo_set(cur.connection, pid).hash
    cur.execute("UPDATE images SET rendition = 'sreality-1800-fit', stored_width = 1800, stored_height = 1350 "
                "WHERE id = %s", (image,))
    assert pa.photo_set(cur.connection, pid).hash != before


def test_a_canonical_ad_without_photos_gives_way_to_the_live_ad_that_has_them(cur: Any) -> None:
    pid, ad = _placed(cur)
    ended = _advert(cur, pid, source="idnes", active=False)
    live = _advert(cur, pid, source="remax")
    _image(cur, ended, 1)
    kept = _image(cur, live, 1)
    photos = pa.photo_set(cur.connection, pid)
    assert photos.listing_id == live and [p.image_id for p in photos.photos] == [kept]


def test_no_stored_picture_is_an_empty_set_that_counts_what_is_downloading(cur: Any) -> None:
    pid, ad = _placed(cur)
    _image(cur, ad, 1, stored=False)
    photos = pa.photo_set(cur.connection, pid)
    assert not photos.usable and photos.pending == 1 and photos.listing_id == ad


def test_a_merged_away_id_follows_its_survivor_and_an_unknown_id_is_none(cur: Any) -> None:
    survivor, ad = _placed(cur)
    image = _image(cur, ad, 1)
    gone = _property(cur)
    cur.execute("UPDATE properties SET status = 'merged_away', merged_into = %s WHERE id = %s", (survivor, gone))
    photos = pa.photo_set(cur.connection, gone)
    assert photos.property_id == survivor and [p.image_id for p in photos.photos] == [image]
    assert pa.photo_set(cur.connection, 2_000_000_000) is None


class _R2:
    def download_bytes(self, key: str) -> bytes:
        return b"bytes"


def _good() -> dict[str, Any]:
    return {"facets": {k: {"score": 4, "evidence": f"{k} vidět"} for k in pa.load_facets().keys}}


def _run(conn: Any, *replies: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[run_scope.RunScope, _ScriptedProvider]:
    monkeypatch.setattr(image_storage, "is_configured", lambda: True)
    monkeypatch.setattr(image_storage.R2Client, "from_env", staticmethod(lambda: _R2()))
    provider = _ScriptedProvider("anthropic", list(replies), prices={MODEL: PRICE})
    return run_scope.RunScope(llm=LLMClient(conn, providers={"anthropic": provider}), left_usd=lambda: 20.0), provider


def _call(conn: Any, scope: run_scope.RunScope | None, pid: int) -> dict[str, Any]:
    with run_scope.running(scope):
        return registry.call(Caller(market=conn), NAME, {"property_id": pid})


def test_a_second_request_is_served_from_the_cache_and_the_ledger_holds_one_call(
        cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    pid, ad = _placed(cur)
    _image(cur, ad, 1)
    conn = cur.connection
    scope, provider = _run(conn, reply((pa.TOOL_NAME, _good()), usage=(10_000, 1_000), model=MODEL), monkeypatch=monkeypatch)
    first = _call(conn, scope, pid)
    second = _call(conn, None, pid)
    assert first["data"]["cache_hit"] is False and second["data"]["cache_hit"] is True
    assert second["data"]["facets"] == _good()["facets"] and second["data"]["cost_usd"] == 0
    assert second["data"]["model"] == MODEL and second["data"]["assessed_at"] and second["metadata"]["data_freshness"]
    assert len(provider.calls) == 1
    version, photo_set_hash = pa.load_facets().version, first["data"]["photo_set_hash"]
    cur.execute("SELECT model, photos_used, plans_used, cost_usd FROM photo_set_assessments "
                "WHERE photo_set_hash = %s AND prompt_version = %s", (photo_set_hash, version))
    model, photos_used, plans_used, cost = cur.fetchone()
    assert (model, photos_used, plans_used) == (MODEL, 1, 0) and float(cost) == pytest.approx(first["data"]["cost_usd"])
    llm_call = scope.settle()[1]
    cur.execute("SELECT called_for, model FROM llm_calls WHERE id = %s", (llm_call,))
    assert cur.fetchone() == (pa.CALLED_FOR, MODEL)


def test_the_first_judgement_stands_when_two_runs_race(cur: Any) -> None:
    pid, ad = _placed(cur)
    _image(cur, ad, 1)
    conn = cur.connection
    photos, facets = pa.photo_set(conn, pid), pa.load_facets()
    one = pa.Judgement(facets={"view": {"score": 1, "evidence": "a"}}, model=MODEL, cost_usd=0.1, llm_call_id=1)
    two = pa.Judgement(facets={"view": {"score": 5, "evidence": "b"}}, model=MODEL, cost_usd=0.1, llm_call_id=2)
    pa._store(conn, photos, facets, one)
    pa._store(conn, photos, facets, two)
    assert pa._lookup(conn, photos.hash, facets.version)["facets"] == one.facets


def test_a_set_with_no_picture_writes_no_row_and_pays_nothing(cur: Any) -> None:
    pid, _ = _placed(cur)
    out = _call(cur.connection, None, pid)
    assert out["data"]["status"] == "no_photos"
    assert out["metadata"]["result_count"] == 0


def test_an_unknown_property_is_not_found(cur: Any) -> None:
    with pytest.raises(ToolError) as refused:
        _call(cur.connection, None, 2_000_000_000)
    assert refused.value.code is ToolErrorCode.NOT_FOUND


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_the_browsers_roles_read_and_write_nothing_of_the_cache(cur: Any, role: str) -> None:
    cur.execute("SAVEPOINT s")
    cur.execute(f"SET LOCAL ROLE {role}")
    for statement in ("SELECT 1 FROM photo_set_assessments LIMIT 1",
                      "INSERT INTO photo_set_assessments (photo_set_hash, prompt_version, facets, model, photos_used, "
                      "plans_used, cost_usd) VALUES ('h', 'v', '{}', 'm', 0, 0, 0)"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(statement)
        cur.execute("ROLLBACK TO SAVEPOINT s")
        cur.execute(f"SET LOCAL ROLE {role}")
    cur.execute("RESET ROLE")


def test_rls_is_on_with_no_policy_so_a_future_grant_still_reads_nothing(cur: Any) -> None:
    cur.execute("SELECT relrowsecurity, (SELECT count(*) FROM pg_policies WHERE tablename = 'photo_set_assessments') "
                "FROM pg_class WHERE oid = 'public.photo_set_assessments'::regclass")
    assert cur.fetchone() == (True, 0)
