"""POST /listings/ingest + GET /listings/ingest/{source}/{native_id} — a pasted ad link →
ONE ingest state (api/ingest.py, docs/design/new-property-from-link.md §3.2).

A stateful fake connection plays the three tables the route touches (`listings`,
`listing_detail_queue`, `detail_queue_completions`) behind the REAL `scraper.db` readers and
writers, so each test drives the route as the drain would see it: enqueue, give up, re-arm,
gone, written. Callers are real JWTs (the conftest's JWKS stub): admins only in v1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from scraper import db  # noqa: E402
from tests.api._jwt import mint  # noqa: E402

IDNES = "https://reality.idnes.cz/detail/prodej/byt/praha-5/6ad7ca9d45dd870c0a0b152a/"
IDNES_ID = "6ad7ca9d45dd870c0a0b152a"
SREALITY = "https://www.sreality.cz/detail/prodej/byt/2+kk/praha/2557083724"
KEYS = {"state", "message_cs", "source", "source_id_native", "listing_id", "is_active",
        "property_id", "queue"}
CLAIMED = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


class _Store:
    def __init__(self) -> None:
        # (id, is_active, property_id[, source_url])
        self.listings: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.queue: dict[tuple[str, str], dict[str, Any]] = {}
        self.completions: list[tuple[str, str, str]] = []
        self.writes: list[str] = []


class _Cur:
    def __init__(self, store: _Store) -> None:
        self.s, self._rows, self.rowcount = store, [], 0

    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(sql.split())
        s = self.s
        self._rows, self.rowcount = [], 0
        if text.startswith("SELECT id, is_active, property_id, source_url FROM listings"):
            row = s.listings.get(tuple(params))
            self._rows = [(row + (None,))[:4]] if row else []
        elif text.startswith("SELECT attempts, claimed_at, given_up FROM listing_detail_queue"):
            q = s.queue.get(tuple(params))
            self._rows = [(q["attempts"], q["claimed_at"], q["given_up"])] if q else []
        elif text.startswith("SELECT outcome FROM detail_queue_completions"):
            hits = [o for src, nid, o in s.completions if (src, nid) == tuple(params)]
            self._rows = [(hits[-1],)] if hits else []
        elif text.startswith("INSERT INTO listing_detail_queue"):
            s.writes.append("enqueue")
            for nid, ref, prio in zip(params["nids"], params["refs"], params["prios"]):
                key = (params["source"], nid)
                q = s.queue.get(key)
                if q is None:
                    s.queue[key] = {"attempts": 0, "claimed_at": None, "given_up": False,
                                    "priority": prio, "detail_ref": ref}
                    self.rowcount += 1
                elif q["claimed_at"] is None:
                    q.update(detail_ref=ref, priority=max(q["priority"], prio))
                    self.rowcount += 1
        elif text.startswith("UPDATE listing_detail_queue SET priority = %s"):
            prio, source, ids, _floor = params
            for nid in ids:
                q = s.queue.get((source, nid))
                if q and q["claimed_at"] is None and not q["given_up"] and q["priority"] < prio:
                    q["priority"] = prio
                    self.rowcount += 1
            if self.rowcount:
                s.writes.append("raise")
        elif text.startswith("UPDATE listing_detail_queue SET given_up = false"):
            s.writes.append("rearm")
            prio, source, ids = params
            for nid in ids:
                q = s.queue.get((source, nid))
                if q and q["given_up"]:
                    q.update(given_up=False, attempts=0, priority=max(q["priority"], prio))
                    self.rowcount += 1
        else:
            raise AssertionError(f"unexpected SQL: {text}")

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _Conn:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def cursor(self) -> _Cur:
        return _Cur(self.store)


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {mint({'sub': 'op', 'app_metadata': {'is_admin': True}})}"}


@pytest.fixture()
def store() -> _Store:
    return _Store()


@pytest.fixture()
def client(store: _Store):
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: _Conn(store)
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def _post(client: Any, text: str) -> dict[str, Any]:
    res = client.post("/listings/ingest", json={"url": text}, headers=_admin())
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == KEYS
    return body


def _get(client: Any, source: str, native: str) -> dict[str, Any]:
    res = client.get(f"/listings/ingest/{source}/{native}", headers=_admin())
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == KEYS
    return body


# --- refused ------------------------------------------------------------------

@pytest.mark.parametrize("text, sentence", [
    ("dobrý den", "V textu není žádný odkaz."),
    ("https://auto.bazos.cz/inzerat/187654321/skoda.php", "Tuto stránku zatím nečteme."),
    ("https://www.sreality.cz/hledani/prodej/byty", "Tento odkaz nevede na jeden inzerát."),
    ("https://www.sreality.cz/detail/prodej/byt/x/2557083724?" + "a" * 600, "Odkaz je příliš dlouhý."),
])
def test_a_refusal_writes_nothing_and_says_one_sentence(client, store, text, sentence) -> None:
    body = _post(client, text)
    assert body["state"] == "refused"
    assert body["message_cs"] == sentence
    assert body["source"] is None and body["listing_id"] is None and body["queue"] is None
    assert store.writes == []


# --- queued -------------------------------------------------------------------

def test_an_ad_we_lack_is_queued_at_new_with_its_url(client, store) -> None:
    body = _post(client, f"Mrkni na tohle {IDNES}#foto, je to 2+kk.")
    assert body["state"] == "queued"
    assert (body["source"], body["source_id_native"]) == ("idnes", IDNES_ID)
    assert body["message_cs"] is None and body["listing_id"] is None
    assert body["queue"] == {"attempts": 0, "claimed_at": None, "given_up": False}
    q = store.queue[("idnes", IDNES_ID)]
    assert q["priority"] == db.QUEUE_PRIORITY_NEW
    assert q["detail_ref"] == IDNES


def test_any_other_site_is_queued_as_the_tenth_portal_under_its_canonical_url(client, store) -> None:
    body = _post(client, "https://www.RK-Novak.cz/byty/15/?utm_source=fb&id=2#foto")
    assert body["state"] == "queued"
    assert (body["source"], body["source_id_native"]) == ("web", "https://www.rk-novak.cz/byty/15?id=2")
    assert store.queue[("web", "https://www.rk-novak.cz/byty/15?id=2")]["detail_ref"] == (
        "https://www.rk-novak.cz/byty/15/?id=2")
    # The SPA polls with encodeURIComponent: a web id carries `/` and `?`.
    from urllib.parse import quote

    assert _get(client, "web", quote("https://www.rk-novak.cz/byty/15?id=2", safe=""))["state"] == "queued"


def test_sreality_is_queued_by_id_never_by_url(client, store) -> None:
    assert _post(client, SREALITY)["state"] == "queued"
    assert store.queue[("sreality", "2557083724")]["detail_ref"] is None


def test_the_post_is_idempotent_and_leaves_a_live_row_alone(client, store) -> None:
    _post(client, IDNES)
    store.queue[("idnes", IDNES_ID)].update(claimed_at=CLAIMED, attempts=1)
    store.writes.clear()
    body = _post(client, IDNES)
    assert body["state"] == "queued"
    assert body["queue"]["attempts"] == 1 and body["queue"]["claimed_at"] is not None
    assert store.writes == []


def test_a_live_row_a_presence_check_queued_is_lifted_to_new_and_nothing_else(client, store) -> None:
    store.queue[("idnes", IDNES_ID)] = {"attempts": 1, "claimed_at": None, "given_up": False,
                                        "priority": db.QUEUE_PRIORITY_VERIFY, "detail_ref": "/walk/ref/"}
    body = _post(client, IDNES)
    assert body["state"] == "queued" and body["queue"]["attempts"] == 1
    q = store.queue[("idnes", IDNES_ID)]
    assert q["priority"] == db.QUEUE_PRIORITY_NEW and q["detail_ref"] == "/walk/ref/"
    assert store.writes == ["raise"]


def test_a_given_up_row_is_re_armed_by_a_re_paste(client, store) -> None:
    store.queue[("idnes", IDNES_ID)] = {"attempts": 5, "claimed_at": None, "given_up": True,
                                        "priority": db.QUEUE_PRIORITY_VERIFY, "detail_ref": None}
    body = _post(client, IDNES)
    assert body["state"] == "queued"
    assert body["queue"] == {"attempts": 0, "claimed_at": None, "given_up": False}
    q = store.queue[("idnes", IDNES_ID)]
    assert q["priority"] == db.QUEUE_PRIORITY_NEW and q["detail_ref"] == IDNES
    assert store.writes == ["enqueue", "rearm"]


# --- held ---------------------------------------------------------------------

def test_an_active_ad_we_hold_answers_held_and_writes_nothing(client, store) -> None:
    store.listings[("idnes", IDNES_ID)] = (123, True, 456)
    body = _post(client, IDNES)
    assert body["state"] == "held"
    assert (body["listing_id"], body["is_active"], body["property_id"]) == (123, True, 456)
    assert body["message_cs"] is None
    assert store.writes == []


def test_a_held_ad_before_its_property_is_born_says_so(client, store) -> None:
    store.listings[("idnes", IDNES_ID)] = (123, True, None)
    body = _get(client, "idnes", IDNES_ID)
    assert body["state"] == "held" and body["property_id"] is None


def test_an_inactive_ad_we_hold_is_queued_for_a_re_check(client, store) -> None:
    store.listings[("idnes", IDNES_ID)] = (123, False, 456)
    body = _post(client, IDNES)
    assert body["state"] == "held" and body["is_active"] is False
    assert store.queue[("idnes", IDNES_ID)]["priority"] == db.QUEUE_PRIORITY_NEW
    assert body["queue"] == {"attempts": 0, "claimed_at": None, "given_up": False}


def test_an_inactive_ad_is_re_checked_at_the_walks_url_not_the_pasted_one(client, store) -> None:
    walked = "https://reality.idnes.cz/detail/prodej/byt/praha-5-smichov/6ad7ca9d45dd870c0a0b152a/"
    store.listings[("idnes", IDNES_ID)] = (123, False, 456, walked)
    _post(client, f"{IDNES}?ref=mail")
    assert store.queue[("idnes", IDNES_ID)]["detail_ref"] == walked


def test_held_wins_over_a_stale_gone_verdict(client, store) -> None:
    store.listings[("idnes", IDNES_ID)] = (123, True, 456)
    store.completions.append(("idnes", IDNES_ID, "gone"))
    assert _post(client, IDNES)["state"] == "held"


# --- gone ---------------------------------------------------------------------

def test_a_never_held_ad_whose_page_was_gone_answers_gone(client, store) -> None:
    store.completions.append(("idnes", IDNES_ID, "gone"))
    for body in (_post(client, IDNES), _get(client, "idnes", IDNES_ID)):
        assert body["state"] == "gone"
        assert body["message_cs"] == "Inzerát na portálu už není."
        assert body["queue"] is None
    assert store.writes == []


def test_a_waiting_row_beats_an_older_gone_verdict(client, store) -> None:
    store.completions.append(("idnes", IDNES_ID, "gone"))
    store.queue[("idnes", IDNES_ID)] = {"attempts": 0, "claimed_at": None, "given_up": False,
                                        "priority": 0, "detail_ref": IDNES}
    assert _get(client, "idnes", IDNES_ID)["state"] == "queued"
    assert _post(client, IDNES)["state"] == "queued"


# --- failed -------------------------------------------------------------------

def test_the_get_reports_a_given_up_row_as_failed_and_writes_nothing(client, store) -> None:
    store.queue[("idnes", IDNES_ID)] = {"attempts": 5, "claimed_at": None, "given_up": True,
                                        "priority": 0, "detail_ref": IDNES}
    body = _get(client, "idnes", IDNES_ID)
    assert body["state"] == "failed"
    assert body["message_cs"] == "Stránku se nepodařilo načíst, zkuste to později."
    assert body["queue"]["given_up"] is True
    assert store.writes == []


def test_the_get_never_polls_forever_on_an_ad_nobody_queued(client, store) -> None:
    body = _get(client, "idnes", IDNES_ID)
    assert body["state"] == "failed"
    # Not "try later": a page the drain read but did not land answers the same on a re-paste.
    assert body["message_cs"] == "Tento inzerát neumíme přidat."
    assert store.writes == []


# --- the poll, end to end -----------------------------------------------------

def test_the_poll_follows_the_drain_from_queued_to_held(client, store) -> None:
    assert _post(client, IDNES)["state"] == "queued"
    store.queue[("idnes", IDNES_ID)]["claimed_at"] = CLAIMED
    assert _get(client, "idnes", IDNES_ID)["state"] == "queued"
    store.listings[("idnes", IDNES_ID)] = (123, True, None)
    del store.queue[("idnes", IDNES_ID)]
    store.completions.append(("idnes", IDNES_ID, "written"))
    assert _get(client, "idnes", IDNES_ID)["property_id"] is None
    store.listings[("idnes", IDNES_ID)] = (123, True, 456)
    body = _get(client, "idnes", IDNES_ID)
    assert (body["state"], body["property_id"]) == ("held", 456)


def test_the_get_refuses_a_source_we_do_not_read(client, store) -> None:
    body = _get(client, "example", "1")
    assert body["state"] == "refused" and body["message_cs"] == "Tuto stránku zatím nečteme."


# --- the gate -----------------------------------------------------------------

def test_admins_only(client, store) -> None:
    user = {"Authorization": f"Bearer {mint({'sub': 'someone'})}"}
    assert client.post("/listings/ingest", json={"url": IDNES}).status_code == 401
    assert client.post("/listings/ingest", json={"url": IDNES}, headers=user).status_code == 403
    assert client.get(f"/listings/ingest/idnes/{IDNES_ID}").status_code == 401
    assert client.get(f"/listings/ingest/idnes/{IDNES_ID}", headers=user).status_code == 403
    assert store.writes == [] and store.queue == {}


def test_the_answer_carries_no_per_account_field() -> None:
    from api import schemas as s

    assert set(s.IngestState.model_fields) == KEYS
    banned = ("pipeline", "collection", "tag", "dismiss", "account", "stage", "note")
    assert not [k for k in KEYS if any(b in k for b in banned)]


# --- the scraper.db helpers the route relies on --------------------------------

def test_rearm_given_up_resets_only_given_up_rows() -> None:
    store = _Store()
    store.queue[("bazos", "1")] = {"attempts": 5, "claimed_at": None, "given_up": True,
                                   "priority": -2, "detail_ref": None}
    store.queue[("bazos", "2")] = {"attempts": 3, "claimed_at": None, "given_up": False,
                                   "priority": 1, "detail_ref": None}
    assert db.rearm_given_up(_Conn(store), "bazos", ["1", "2"]) == 1
    assert store.queue[("bazos", "1")]["priority"] == db.QUEUE_PRIORITY_NEW
    assert store.queue[("bazos", "2")]["attempts"] == 3
    assert db.rearm_given_up(_Conn(store), "bazos", []) == 0


def test_the_queue_helpers_speak_the_queue_schema() -> None:
    from tests.api._script_conn import ScriptConn

    conn = ScriptConn([(lambda t: True, [(2, None, False)])])
    db.rearm_given_up(conn, "bazos", ["1"])
    assert db.queue_state(conn, "bazos", "1") == {"attempts": 2, "claimed_at": None, "given_up": False}
    db.latest_completion_outcome(conn, "bazos", "1")
    db.raise_unclaimed_priority(conn, "bazos", ["1"], db.QUEUE_PRIORITY_NEW)
    assert db.raise_unclaimed_priority(conn, "bazos", [], db.QUEUE_PRIORITY_NEW) == 0
    rearm, state, outcome, lift = (sql for sql, _ in conn.executed)
    assert lift.endswith("AND claimed_at IS NULL AND NOT given_up AND priority < %s")
    assert "detail_ref" not in lift and "index_price_czk" not in lift
    assert "given_up = false, attempts = 0, last_error = NULL" in rearm
    assert "GREATEST(priority, %s)" in rearm and rearm.endswith("AND given_up")
    assert conn.executed[0][1] == (db.QUEUE_PRIORITY_NEW, "bazos", ["1"])
    assert "FROM listing_detail_queue WHERE source = %s AND native_id = %s" in state
    assert "ORDER BY completed_at DESC, id DESC LIMIT 1" in outcome
