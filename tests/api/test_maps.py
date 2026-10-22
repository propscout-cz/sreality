"""Tests for the Mapy.cz proxy endpoints. Hermetic — all HTTP mocked."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api import http_json, maps
from location_data.name_index import normalize_street_name
from location_data.resolver.resolve_db import _STREET_POINT_SQL, _STREETS_IN_OBEC_SQL


# Mirrors Mapy.cz's actual /v1/suggest shape: `label` is the result-class
# tag ("Ulice ", "Část obce "), NOT the human-readable place name. The
# specific name lives in `name`; the address with city/region context lives
# in `location`. See scraper/geocoding.py:13–38 for the schema notes.
_MOCK_SUGGEST = {
    "items": [
        {
            "name": "Vinohrady",
            "label": "Část obce ",
            "location": "Vinohrady, Praha 2, Hlavní město Praha",
            "type": "regional.municipality_part",
            "position": {"lon": 14.441, "lat": 50.077},
            "regionalStructure": [
                {"name": "Vinohrady", "type": "regional.municipality_part"},
                {"name": "Praha 2", "type": "regional.municipality"},
                {"name": "Hlavní město Praha", "type": "regional.region"},
                {"name": "Česko", "type": "regional.country"},
            ],
        },
        {
            "name": "Vinohradská",
            "label": "Ulice ",
            "location": "Vinohradská, Praha 2, Hlavní město Praha",
            "type": "regional.street",
            "position": {"lon": 14.45, "lat": 50.08},
            "regionalStructure": [],
        },
    ],
}


@pytest.fixture()
def client(monkeypatch):
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.get_sreality_client] = lambda: object()
    monkeypatch.setenv("MAPY_CZ_API_KEY", "test-key")
    maps.clear_suggest_cache()
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()
    maps.clear_suggest_cache()


def _mock_http(monkeypatch, payload: dict[str, Any], counter: list[int] | None = None):
    def fake(method: str, url: str, **kw: Any) -> dict[str, Any]:
        if counter is not None:
            counter.append(1)
        return payload
    monkeypatch.setattr(http_json, "fetch_json", fake)


def test_suggest_returns_items(client, monkeypatch):
    _mock_http(monkeypatch, _MOCK_SUGGEST)
    res = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res.status_code == 200
    body = res.json()
    assert "items" in body
    assert len(body["items"]) == 2
    assert body["items"][0]["name"] == "Vinohrady"


def test_suggest_503_when_key_unset(client, monkeypatch):
    monkeypatch.delenv("MAPY_CZ_API_KEY", raising=False)
    res = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res.status_code == 503
    assert "geocoding not configured" in res.json()["detail"]


def test_suggest_503_when_mapy_rejects_key(client, monkeypatch):
    # A suspended / invalid key makes Mapy reject the call (e.g. 403). The proxy
    # must return 503 (graceful frontend fallback), not a raw 500 (silent empty
    # dropdown).
    def _reject(method: str, url: str, **kw: Any) -> dict[str, Any]:
        raise _http_error(403)

    monkeypatch.setattr(http_json, "fetch_json", _reject)
    res = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res.status_code == 503
    assert "unavailable" in res.json()["detail"]


def test_suggest_503_when_mapy_unreachable(client, monkeypatch):
    def _down(method: str, url: str, **kw: Any) -> dict[str, Any]:
        raise http_json.HttpJsonError("api.mapy.cz", "unreachable")

    monkeypatch.setattr(http_json, "fetch_json", _down)
    res = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res.status_code == 503


def test_suggest_caches_within_ttl(client, monkeypatch):
    counter: list[int] = []
    _mock_http(monkeypatch, _MOCK_SUGGEST, counter=counter)

    res1 = client.get("/maps/suggest", params={"query": "Vinohrady"})
    res2 = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert len(counter) == 1, "second identical query should be served from cache"


def test_suggest_distinct_queries_not_cached_together(client, monkeypatch):
    counter: list[int] = []
    _mock_http(monkeypatch, _MOCK_SUGGEST, counter=counter)

    client.get("/maps/suggest", params={"query": "Vinohrady"})
    client.get("/maps/suggest", params={"query": "Smichov"})
    assert len(counter) == 2


def test_suggest_query_required(client):
    res = client.get("/maps/suggest")
    assert res.status_code == 422


def test_suggest_limit_bounds(client, monkeypatch):
    """Mapy caps `limit` at 15; a larger one came back as a 503 (R2-M5)."""
    _mock_http(monkeypatch, _MOCK_SUGGEST)
    assert client.get("/maps/suggest", params={"query": "x", "limit": 0}).status_code == 422
    assert client.get("/maps/suggest", params={"query": "x", "limit": 16}).status_code == 422
    assert client.get("/maps/suggest", params={"query": "x", "limit": 15}).status_code == 200


def test_suggest_asks_mapy_for_regional_items_only(client, monkeypatch):
    """A POI has no register unit, so it can never be a chip: the suggestions
    are `type=regional` (streets and addresses included)."""
    seen: list[dict[str, Any]] = []

    def fake(method: str, url: str, **kw: Any) -> dict[str, Any]:
        seen.append(kw["params"])
        return _MOCK_SUGGEST

    monkeypatch.setattr(http_json, "fetch_json", fake)
    assert client.get("/maps/suggest", params={"query": "Vinohrady"}).status_code == 200
    assert seen == [{
        "query": "Vinohrady", "limit": 10, "lang": "cs", "type": "regional",
        "apikey": "test-key",
    }]


# ---------------- backup-key failover (MAPY2_CZ_API_KEY) ----------------


def _http_error(status: int) -> http_json.HttpJsonError:
    return http_json.HttpJsonError("api.mapy.cz", "status", status)


def _mock_http_sequence(monkeypatch, outcomes: list[Any]) -> dict[str, Any]:
    """Mock the JSON fetch to yield each outcome (payload or Exception) per call,
    recording the apikey used each time."""
    state: dict[str, Any] = {"i": 0, "keys": []}

    def fake(method: str, url: str, **kw: Any) -> dict[str, Any]:
        state["keys"].append(kw["params"].get("apikey"))
        outcome = outcomes[state["i"]]
        state["i"] += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(http_json, "fetch_json", fake)
    return state


@pytest.mark.parametrize("bad_status", [401, 403, 429])
def test_suggest_fails_over_to_backup_key(client, monkeypatch, bad_status):
    monkeypatch.setenv("MAPY2_CZ_API_KEY", "backup-key")
    state = _mock_http_sequence(monkeypatch, [_http_error(bad_status), _MOCK_SUGGEST])
    res = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res.status_code == 200
    assert len(res.json()["items"]) == 2
    assert state["keys"] == ["test-key", "backup-key"]


def test_suggest_no_failover_on_server_error(client, monkeypatch):
    # A 500 is a Mapy outage, not a key problem: 503 immediately, backup untouched.
    monkeypatch.setenv("MAPY2_CZ_API_KEY", "backup-key")
    state = _mock_http_sequence(monkeypatch, [_http_error(500), _MOCK_SUGGEST])
    res = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res.status_code == 503
    assert state["keys"] == ["test-key"]


def test_suggest_both_keys_rejected_returns_503(client, monkeypatch):
    monkeypatch.setenv("MAPY2_CZ_API_KEY", "backup-key")
    state = _mock_http_sequence(monkeypatch, [_http_error(403), _http_error(403)])
    res = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res.status_code == 503
    assert state["keys"] == ["test-key", "backup-key"]


def test_suggest_uses_backup_when_primary_unset(client, monkeypatch):
    monkeypatch.delenv("MAPY_CZ_API_KEY", raising=False)
    monkeypatch.setenv("MAPY2_CZ_API_KEY", "backup-key")
    state = _mock_http_sequence(monkeypatch, [_MOCK_SUGGEST])
    res = client.get("/maps/suggest", params={"query": "Vinohrady"})
    assert res.status_code == 200
    assert state["keys"] == ["backup-key"]


# ---------------- /maps/resolve ----------------


@dataclass(frozen=True)
class _Expect:
    """One scripted answer that also PINS the statement it answers: the cursor
    fails the test unless the code under test issues exactly this SQL with
    exactly these params. `answer` is a tuple for a `fetchone` read, a list for
    a `fetchall` one."""

    sql: str
    params: Any
    answer: Any


class _FakeCursor:
    def __init__(self, scripted: list[_Expect], executed: list[str]):
        self._scripted = scripted
        self._executed = executed
        self._next: Any = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def execute(self, sql: str, params: Any = None) -> None:
        self._executed.append(sql)
        assert self._scripted, f"unscripted statement:\n{sql}"
        entry = self._scripted.pop(0)
        assert sql == entry.sql, f"unexpected statement:\n{sql}"
        assert params == entry.params, f"unexpected params {params!r}"
        self._next = entry.answer

    def fetchone(self) -> Any:
        return self._next

    def fetchall(self) -> Any:
        return self._next if isinstance(self._next, list) else []


class _FakeConn:
    def __init__(self, scripted: list[_Expect]):
        self._scripted = scripted
        self.executed: list[str] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._scripted, self.executed)


def _override_conn(scripted: list[_Expect]) -> _FakeConn:
    conn = _FakeConn(scripted)
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: conn
    return conn


# The scripted sequence for a resolvable point, every statement pinned to the SQL
# the code really issues:
#   1. the mirror probe + the current registry version (`_registry_version`);
#   2. the containing-obec PIP + its parent chain (`_chain`): the resolver's own
#      CONTAINING_OBEC_SQL, ONE point in its arrays, at the current version;
# then, for a část obce, one name-index read per name variant (`_parts`), and for
# a street the ONE register read `SqlRegistryView.streets_in_obec` issues
# (`_streets_of`) and, once a street binds, its `street_point` read (`_point_of`).
#
# Codes, names, chains and street points are REAL register rows (ruian_admin_units,
# ruian_streets, ruian_address_points; read 2026-10-16). Chain surrogate ids other
# than Praha's (3214) are stand-ins: only a část obce lookup reads one.
_VERSION = 7
_MIRROR_PRESENT = [
    _Expect("SELECT to_regclass('public.ruian_admin_units') IS NOT NULL", None, (True,)),
    _Expect(maps._CURRENT_VERSION_SQL, None, (_VERSION,)),
]
_JIHLAVA = [
    (9001, "obec", 586846, "Jihlava"),
    (9002, "okres", 3707, "Jihlava"),
    (9003, "kraj", 108, "Kraj Vysočina"),
    (9004, "stat", 1, "Česká republika"),
]
_PARDUBICE = [
    (9101, "obec", 555134, "Pardubice"),
    (9102, "okres", 3606, "Pardubice"),
    (9103, "kraj", 94, "Pardubický kraj"),
    (9004, "stat", 1, "Česká republika"),
]
_NYMBURK = [
    (9201, "obec", 537004, "Nymburk"),
    (9202, "okres", 3208, "Nymburk"),
    (9203, "kraj", 27, "Středočeský kraj"),
    (9004, "stat", 1, "Česká republika"),
]
# Praha's obec hangs straight off its kraj: the register has no okres in its chain.
_PRAHA = [
    (3214, "obec", 554782, "Praha"),
    (9303, "kraj", 19, "Hlavní město Praha"),
    (9004, "stat", 1, "Česká republika"),
]
_KLADNO = [
    (9401, "obec", 532053, "Kladno"),
    (9402, "okres", 3203, "Kladno"),
    (9203, "kraj", 27, "Středočeský kraj"),
    (9004, "stat", 1, "Česká republika"),
]
_OSTROVACICE = [
    (9501, "obec", 583600, "Ostrovačice"),
    (9502, "okres", 3703, "Brno-venkov"),
    (9503, "kraj", 116, "Jihomoravský kraj"),
    (9004, "stat", 1, "Česká republika"),
]
_HRADEC = [
    (4398, "obec", 569810, "Hradec Králové"),
    (9603, "kraj", 86, "Královéhradecký kraj"),
    (9004, "stat", 1, "Česká republika"),
]


def _chain(payload: dict[str, Any], rows: list[tuple]) -> _Expect:
    return _Expect(
        maps._CONTAINING_CHAIN_SQL,
        ([0], [payload["lat"]], [payload["lng"]], _VERSION),
        rows,
    )


def _parts(name_norm: str, obec_unit_id: int, *rows: tuple) -> _Expect:
    return _Expect(
        maps._PART_LOOKUP_SQL,
        {"version": _VERSION, "name_norm": name_norm, "obec_unit_id": obec_unit_id},
        list(rows),
    )


@dataclass(frozen=True)
class _Row:
    """One `ruian_streets` row: code, name, surrogate id, obec code, and the
    street's point (centroid lat/lon + farthest-door extent), None for no doors."""

    code: int
    name: str
    id: int
    obec: int
    point: tuple[float, float, float] | None = None


def _streets_of(*rows: _Row) -> _Expect:
    """`streets_in_obec` as `SqlRegistryView` reads it (`_STREETS_IN_OBEC_SQL`:
    code, name, name_norm, obec code, id) — keyed on the obec CODE, asked ONCE
    per pick (the request's `CachedRegistryView` serves the verbatim step and
    `match_streets`' index from one answer); name_norm as the loader builds it."""
    return _Expect(
        _STREETS_IN_OBEC_SQL, (rows[0].obec,),
        [(r.code, r.name, normalize_street_name(r.name), r.obec, r.id) for r in rows],
    )


def _point_of(row: _Row) -> _Expect:
    """`SqlRegistryView.street_point`'s read for the bound street: its id, then
    the version twice (the KÚ door rule). No doors -> no row."""
    answer = [] if row.point is None else [(*row.point, 9, None, [None])]
    return _Expect(_STREET_POINT_SQL, (row.id, _VERSION, _VERSION), answer)


# Pardubice (obec 555134)
NA_RYBNICKACH = _Row(666742, "Na Rybníčkách", 50655, 555134, (50.023654, 15.830813, 171.4))
HUSOVA = _Row(372897, "Husova", 28031, 555134, (50.043047, 15.792167, 772.0))
NAM_REPUBLIKY = _Row(373907, "náměstí Republiky", 19637, 555134, (50.037945, 15.777215, 109.3))
PERNSTYNSKA = _Row(374156, "Pernštýnská", 38410, 555134, (50.039379, 15.779511, 58.6))
TRIDA_MIRU = _Row(375039, "třída Míru", 75664, 555134, (50.036959, 15.773918, 251.1))
_STREETS_PARDUBICE = (NA_RYBNICKACH, HUSOVA, NAM_REPUBLIKY, PERNSTYNSKA, TRIDA_MIRU)
# Nymburk (obec 537004): one street and six siblings whose names END IN A NUMBER.
NA_HROUDACH = _Row(801259, "Na Hroudách", 17240, 537004, (50.183743, 15.017480, 253.7))
NA_HROUDACH_3 = _Row(802034, "Na Hroudách 3", 39997, 537004, (50.182882, 15.016648, 49.1))
_STREETS_NYMBURK = (
    NA_HROUDACH,
    _Row(801267, "Na Hroudách 2", 76200, 537004),
    NA_HROUDACH_3,
    _Row(808130, "Na Hroudách 4", 54520, 537004),
    _Row(808148, "Na Hroudách 5", 6798, 537004),
    _Row(834491, "Na Hroudách 6", 157, 537004),
    _Row(844420, "Na Hroudách 1", 75026, 537004),
)
# Praha (obec 554782): one deaccented key, two streets 10 km apart; and a street
# beside its own "ulička".
HANUSOVA = _Row(447340, "Hanusova", 67330, 554782, (50.049055, 14.447154, 187.5))
HANUSOVA_CZ = _Row(447358, "Hanušova", 43703, 554782, (50.137172, 14.545849, 27.1))
TYNSKA = _Row(472778, "Týnská", 1285, 554782, (50.088360, 14.423187, 108.3))
TYNSKA_ULICKA = _Row(472786, "Týnská ulička", 10816, 554782, (50.088368, 14.422374, 39.6))
# Kladno (obec 532053): a type-word pair.
NAM_SVOBODY = _Row(210978, "náměstí Svobody", 7129, 532053, (50.144226, 14.094298, 83.5))
SVOBODY = _Row(211664, "Svobody", 8825, 532053, (50.136114, 14.113764, 221.2))
# Ostrovačice (obec 583600): one of the five current streets with no address point.
CIKANKA = _Row(41971, "Cikánka", 9894, 583600)


def _street_pick(name: str, at: _Row, type_: str = "regional.street") -> dict[str, Any]:
    """A street pick placed ON `at` (its own centroid), as the SPA sends it."""
    assert at.point is not None
    return {"name": name, "lat": at.point[0], "lng": at.point[1], "type": type_}


_OBEC_SUGGESTION = {
    "name": "Jihlava", "lat": 49.3961, "lng": 15.5912, "type": "regional.municipality",
}
_OKRES_SUGGESTION = {
    "name": "okres Jihlava", "lat": 49.40, "lng": 15.60, "type": "regional.region.district",
}
_REGION_SUGGESTION = {
    "name": "Kraj Vysočina", "lat": 49.40, "lng": 15.60, "type": "regional.region",
}
_PART_SUGGESTION = {
    "name": "Vinohrady", "lat": 50.077, "lng": 14.441, "type": "regional.municipality_part",
}
_STREET_SUGGESTION = _street_pick("Na Rybníčkách", NA_RYBNICKACH)
_ADDRESS_SUGGESTION = {
    "name": "Na Rybníčkách 1234",
    "lat": 50.0237,
    "lng": 15.8310,
    "type": "regional.address",
    "regional_structure": [
        {"name": "1234", "type": "regional.address"},
        {"name": "Na Rybníčkách", "type": "regional.street"},
        {"name": "Pardubice", "type": "regional.municipality"},
        {"name": "Pardubický kraj", "type": "regional.region"},
        {"name": "Česko", "type": "regional.country", "isoCode": "CZ"},
    ],
}

_NO_CODE = {"id": None, "name": None}


def _resolve_logged(
    client, payload: dict[str, Any], scripted: list[_Expect]
) -> tuple[dict[str, Any], list[str]]:
    """Every scripted answer must be consumed: a statement the code no longer
    issues is a script that lies about the request."""
    conn = _override_conn(scripted)
    res = client.post("/maps/resolve", json=payload)
    assert res.status_code == 200, res.text
    assert scripted == [], f"{len(scripted)} scripted answer(s) never asked for"
    return res.json(), conn.executed


def _resolve(client, payload: dict[str, Any], scripted: list[_Expect]) -> dict[str, Any]:
    return _resolve_logged(client, payload, scripted)[0]


def _street_resolve(client, payload: dict[str, Any], chain: list[tuple],
                    streets: tuple[_Row, ...], bound: _Row | None) -> dict[str, Any]:
    """A street pick: the chain, the obec's ONE street read, and — only when a
    street binds — that street's point read."""
    scripted = [*_MIRROR_PRESENT, _chain(payload, chain), _streets_of(*streets)]
    if bound is not None:
        scripted.append(_point_of(bound))
    return _resolve(client, payload, scripted)


def _bound(row: _Row, context: str) -> dict[str, Any]:
    return {"level": "ulice", "id": row.code, "name": row.name, "context": context}


def _unbound(context: str) -> dict[str, Any]:
    return {"level": "ulice", **_NO_CODE, "context": context}


def test_resolve_obec_matches_the_obec_code(client):
    # An obec pick resolves to the obec code — NOT the same-named okres.
    body = _resolve(client, _OBEC_SUGGESTION, [
        *_MIRROR_PRESENT, _chain(_OBEC_SUGGESTION, _JIHLAVA),
    ])
    assert body == {"level": "obec", "id": 586846, "name": "Jihlava", "context": None}


def test_resolve_okres_matches_the_okres_code(client):
    # Same point, but an okres-level pick resolves to the okres code (3707), so
    # picking "Okres Jihlava" and obec "Jihlava" are distinct units.
    body = _resolve(client, _OKRES_SUGGESTION, [
        *_MIRROR_PRESENT, _chain(_OKRES_SUGGESTION, _JIHLAVA),
    ])
    assert body == {"level": "okres", "id": 3707, "name": "Jihlava", "context": None}


@pytest.mark.parametrize("name", ["okres Jihlava", "Okres Jihlava", "Jihlava"])
def test_an_okres_pick_binds_the_okres_it_names(client, name):
    body = _resolve(client, {**_OKRES_SUGGESTION, "name": name}, [
        *_MIRROR_PRESENT, _chain(_OKRES_SUGGESTION, _JIHLAVA),
    ])
    assert body == {"level": "okres", "id": 3707, "name": "Jihlava", "context": None}


def test_an_okres_pick_that_does_not_name_its_okres_binds_nothing(client):
    body = _resolve(client, {**_OKRES_SUGGESTION, "name": "okres Třebíč"}, [
        *_MIRROR_PRESENT, _chain(_OKRES_SUGGESTION, _JIHLAVA),
    ])
    assert body == {"level": "okres", **_NO_CODE, "context": None}


def test_a_prague_district_typed_as_a_municipality_binds_nothing(client, caplog):
    """Mapy types "Praha 2" `regional.municipality` (the shape `_MOCK_SUGGEST`
    mirrors); it is a městská část, which no chip serves, and no unit of Praha's
    chain has its name — the point alone would bind ALL of Praha."""
    pick = {"name": "Praha 2", "lat": 50.075, "lng": 14.437, "type": "regional.municipality"}
    with caplog.at_level("INFO", logger="api.maps"):
        body = _resolve(client, pick, [*_MIRROR_PRESENT, _chain(pick, _PRAHA)])
    assert body == {"level": "obec", **_NO_CODE, "context": None}
    assert len([r for r in caplog.records if "no code" in r.getMessage()]) == 1


def test_a_municipality_whose_point_lies_in_another_obec_binds_nothing(client):
    """A town's point that PIPs over a border would otherwise bind the neighbour."""
    pick = {**_OBEC_SUGGESTION, "name": "Pardubice"}
    body = _resolve(client, pick, [*_MIRROR_PRESENT, _chain(pick, _JIHLAVA)])
    assert body == {"level": "obec", **_NO_CODE, "context": None}


@pytest.mark.parametrize("type_,name,chain,bound", [
    ("regional.region", "okres Jihlava", _JIHLAVA, ("okres", 3707, "Jihlava")),
    ("regional.municipality", "Jihlava", _JIHLAVA, ("obec", 586846, "Jihlava")),
    ("regional.region.district", "Jihlava", _JIHLAVA, ("okres", 3707, "Jihlava")),
    ("regional.region", "Hlavní město Praha", _PRAHA, ("kraj", 19, "Hlavní město Praha")),
    ("regional.region.district", "Praha", _PRAHA, ("obec", 554782, "Praha")),
    ("regional.region.district", "okres Hlavní město Praha", _PRAHA,
     ("kraj", 19, "Hlavní město Praha")),
], ids=["okres typed region", "obec+okres namesake typed municipality",
        "obec+okres namesake typed okres", "kraj", "Praha has no okres: its obec",
        "Praha's okres name is its kraj's"])
def test_the_name_picks_the_unit_and_the_type_only_breaks_a_tie(client, type_, name, chain,
                                                                 bound):
    """D5 as a rule: the units of the point's chain that the pick NAMES are the
    candidates; one binds at its own level, whatever Mapy typed (its enum has no
    okres type); a namesake pair goes to the typed level, or to Mapy's "okres "
    prefix, which names the level outright."""
    pick = {"name": name, "lat": 49.40, "lng": 15.60, "type": type_}
    body = _resolve(client, pick, [*_MIRROR_PRESENT, _chain(pick, chain)])
    level, code, register_name = bound
    assert body == {"level": level, "id": code, "name": register_name, "context": None}


def test_a_region_pick_binds_the_kraj_it_names(client):
    body = _resolve(client, _REGION_SUGGESTION, [
        *_MIRROR_PRESENT, _chain(_REGION_SUGGESTION, _JIHLAVA),
    ])
    assert body == {"level": "kraj", "id": 108, "name": "Kraj Vysočina", "context": None}


@pytest.mark.parametrize("name", ["Jihlava", "Vysočina", "   "])
def test_a_region_pick_that_names_no_single_unit_binds_nothing(client, name):
    """"Jihlava" names the obec AND the okres and the type (kraj) is neither: no
    guess. "Vysočina" is not the register's name; a blank names nothing."""
    body = _resolve(client, {**_REGION_SUGGESTION, "name": name}, [
        *_MIRROR_PRESENT, _chain(_REGION_SUGGESTION, _JIHLAVA),
    ])
    assert body == {"level": "kraj", **_NO_CODE, "context": None}


def test_resolve_cast_obce_is_placed_by_name_inside_the_pipped_obec(client):
    """RÚIAN publishes no part-of-municipality polygon, so a quarter can never be
    point-in-polygon'd: the POINT places the obec and the NAME places the part
    inside it, and its obec's register name comes back as the context."""
    body = _resolve(client, _PART_SUGGESTION, [
        *_MIRROR_PRESENT,
        _chain(_PART_SUGGESTION, _PRAHA),
        _parts("vinohrady", 3214, (490229, "Vinohrady")),
    ])
    assert body == {"level": "cast_obce", "id": 490229, "name": "Vinohrady", "context": "Praha"}


def test_resolve_composite_quarter_name_retries_on_its_last_segment(client):
    """Mapy names a quarter the way a person says it — "Hradec Králové - Třebeš"
    — while RÚIAN stores the bare part ("Třebeš")."""
    pick = {**_PART_SUGGESTION, "name": "Hradec Králové - Třebeš", "lat": 50.2, "lng": 15.85}
    body = _resolve(client, pick, [
        *_MIRROR_PRESENT,
        _chain(pick, _HRADEC),
        _parts("hradec kralove trebes", 4398),
        _parts("trebes", 4398, (409847, "Třebeš")),
    ])
    assert body == {
        "level": "cast_obce", "id": 409847, "name": "Třebeš", "context": "Hradec Králové",
    }


def test_an_unplaceable_quarter_gets_NO_CODE_never_the_whole_town(client):
    """"Praha 2" is a městská část (`momc`), which the answer table has no column
    for. Falling back to the containing obec would turn "this quarter" into
    "this whole city" — in a watchdog, a mail about every listing in Prague."""
    pick = {**_PART_SUGGESTION, "name": "Praha 2"}
    body = _resolve(client, pick, [
        *_MIRROR_PRESENT, _chain(pick, _PRAHA), _parts("praha 2", 3214),
    ])
    assert body == {"level": "cast_obce", **_NO_CODE, "context": "Praha"}


def test_an_ambiguous_quarter_name_is_refused_rather_than_guessed(client):
    body = _resolve(client, _PART_SUGGESTION, [
        *_MIRROR_PRESENT,
        _chain(_PART_SUGGESTION, _PRAHA),
        _parts("vinohrady", 3214, (490229, "Vinohrady"), (490230, "Vinohrady")),
    ])
    assert body == {"level": "cast_obce", **_NO_CODE, "context": "Praha"}


# --- the street: the chip binder (D1) + the border guard (D2) -----------------


def test_a_street_binds_its_own_code_never_its_town(client):
    """THE defect of 2026-10-02: a street pick became its whole obec under an
    `Obec` badge. It is the fifth level now: the register's street code + name,
    and its obec's register name as the context."""
    body = _street_resolve(client, _STREET_SUGGESTION, _PARDUBICE, _STREETS_PARDUBICE,
                           NA_RYBNICKACH)
    assert body == _bound(NA_RYBNICKACH, "Pardubice")


def test_a_name_ending_in_a_number_binds_that_street_not_its_sibling(client):
    """Nymburk holds "Na Hroudách" AND "Na Hroudách 1".."6". The listing matcher
    reads a trailing number as a house number and drops it (R2-B1); the verbatim
    step binds the street whose name it is."""
    body = _street_resolve(client, _street_pick("Na Hroudách 3", NA_HROUDACH_3),
                           _NYMBURK, _STREETS_NYMBURK, NA_HROUDACH_3)
    assert body == _bound(NA_HROUDACH_3, "Nymburk")


def test_a_trailing_number_the_register_does_not_spell_is_refused(client):
    """No "Na Hroudách 7" exists. Without the refusal the number would be split
    off and the pick would bind "Na Hroudách" — a street the operator did not
    pick. A street pick never carries a house number."""
    body = _street_resolve(client, _street_pick("Na Hroudách 7", NA_HROUDACH),
                           _NYMBURK, _STREETS_NYMBURK, None)
    assert body == _unbound("Nymburk")


@pytest.mark.parametrize("row", [HANUSOVA, HANUSOVA_CZ], ids=["Hanusova", "Hanušova"])
def test_a_diacritic_pair_binds_the_street_spelled(client, row):
    """Praha's "Hanusova" and "Hanušova" (10 km apart) share one deaccented key,
    so the listing matcher's exact tier reads both and refuses. The verbatim step
    keeps the diacritics that tell them apart (R2-I2)."""
    body = _street_resolve(client, _street_pick(row.name, row), _PRAHA,
                           (HANUSOVA, HANUSOVA_CZ), row)
    assert body == _bound(row, "Praha")


def test_a_spelling_neither_homonym_has_is_refused(client):
    """"Hanušová" is spelled like neither street, and the deaccented exact tier
    reaches both: two answers, no chip."""
    body = _street_resolve(client, _street_pick("Hanušová", HANUSOVA), _PRAHA,
                           (HANUSOVA, HANUSOVA_CZ), None)
    assert body == _unbound("Praha")


def test_two_rows_spelled_alike_are_refused(client):
    """Two register streets of one obec under one spelling are two answers. The
    register holds no such pair today (0 on 2026-10-16), so the twin is synthetic."""
    twin = _Row(372898, "Husova", 28032, 555134)
    body = _street_resolve(client, _street_pick("Husova", HUSOVA), _PARDUBICE,
                           (HUSOVA, twin), None)
    assert body == _unbound("Pardubice")


def test_two_current_rows_of_one_code_are_one_street(client):
    """Keyed by the street CODE, as `match_streets` keys its tiers: two current
    register rows of one code (a re-versioned obec) are one street, not two."""
    twin = _Row(NA_RYBNICKACH.code, NA_RYBNICKACH.name, 50656, 555134)
    body = _street_resolve(client, _STREET_SUGGESTION, _PARDUBICE,
                           (NA_RYBNICKACH, twin, HUSOVA), NA_RYBNICKACH)
    assert body == _bound(NA_RYBNICKACH, "Pardubice")


@pytest.mark.parametrize("row", [NAM_SVOBODY, SVOBODY], ids=["náměstí Svobody", "Svobody"])
def test_a_type_word_pair_binds_the_street_spelled(client, row):
    body = _street_resolve(client, _street_pick(row.name, row), _KLADNO,
                           (NAM_SVOBODY, SVOBODY), row)
    assert body == _bound(row, "Kladno")


@pytest.mark.parametrize("name", ["Republiky", "Míru"])
def test_the_type_word_tolerant_tier_never_binds_a_chip(client, name):
    """"Republiky" reaches Pardubice's "náměstí Republiky", and "Míru" its "třída
    Míru", only by dropping the type word. The listing matcher accepts that tier;
    a chip does not (R2-I3: in Kladno it reads "náměstí Svobody" and "Svobody" as
    one name)."""
    body = _street_resolve(client, {**_STREET_SUGGESTION, "name": name}, _PARDUBICE,
                           _STREETS_PARDUBICE, None)
    assert body == _unbound("Pardubice")


@pytest.mark.parametrize("name,row", [("ul. Husova", HUSOVA),
                                      ("nám. Republiky", NAM_REPUBLIKY)])
def test_the_exact_tier_binds_a_spelling_the_register_writes_out(client, name, row):
    """Neither is the register's spelling, but `match_streets`' EXACT tier reads
    "ul. Husova" as "Husova" (the `ul.` wrapper is meaningless) and "nám." as
    "náměstí" (`fold_abbreviations`, on both sides, 0 new collisions): one row."""
    body = _street_resolve(client, _street_pick(name, row), _PARDUBICE,
                           _STREETS_PARDUBICE, row)
    assert body == _bound(row, "Pardubice")


def test_an_unbindable_street_binds_nothing(client):
    body = _street_resolve(client, {**_STREET_SUGGESTION, "name": "U Kulaťáku"},
                           _PARDUBICE, _STREETS_PARDUBICE, None)
    assert body == _unbound("Pardubice")


@pytest.mark.parametrize("name", ["", "   ", "12", "12/3"])
def test_a_name_with_no_street_in_it_reads_nothing(client, name):
    """No register street is spelled without a letter, so a blank or bare-number
    name binds nothing with no register read at all (R2-M2)."""
    body, executed = _resolve_logged(client, {**_STREET_SUGGESTION, "name": name}, [
        *_MIRROR_PRESENT, _chain(_STREET_SUGGESTION, _PARDUBICE),
    ])
    assert body == _unbound("Pardubice")
    assert _STREETS_IN_OBEC_SQL not in executed


def test_a_same_named_street_far_from_the_pick_is_refused(client):
    """THE BORDER GUARD (D2). Only the PIP'd obec is searched, and a point beside
    a border road can PIP into the neighbour, whose namesake street then binds.
    Here a "Hanušova" pick stands on Hanusova: the verbatim step binds Hanušova,
    whose doors all lie ~12 km away — refused."""
    body = _street_resolve(client, _street_pick("Hanušova", HANUSOVA), _PRAHA,
                           (HANUSOVA, HANUSOVA_CZ), HANUSOVA_CZ)
    assert body == _unbound("Praha")


def test_the_border_guard_allows_the_street_extent_plus_slack(client):
    """Husova's farthest door is 772 m from its centroid: a pick 900 m out is
    still ON the street (extent + 200 m), one 1,100 m out is not."""
    centre_lat, centre_lng, _extent = HUSOVA.point
    for metres, binds in ((900, True), (1_100, False)):
        pick = {**_street_pick("Husova", HUSOVA),
                "lat": centre_lat + metres / 111_195.0, "lng": centre_lng}
        body = _street_resolve(client, pick, _PARDUBICE, _STREETS_PARDUBICE, HUSOVA)
        assert (body["id"] == HUSOVA.code) is binds, metres


def test_a_street_without_doors_has_no_point_and_binds(client):
    """`street_point` is derived from the street's address points; a street with
    none has no position, and the guard cannot judge it — it binds."""
    pick = {"name": "Cikánka", "lat": 49.19, "lng": 16.42, "type": "regional.street"}
    body = _street_resolve(client, pick, _OSTROVACICE, (CIKANKA,), CIKANKA)
    assert body == _bound(CIKANKA, "Ostrovačice")


def test_an_address_binds_the_street_its_regional_structure_names(client):
    """An address filters at its STREET, the one Mapy's `regionalStructure` names
    — never a name parsed out of the address label."""
    body = _street_resolve(client, _ADDRESS_SUGGESTION, _PARDUBICE, _STREETS_PARDUBICE,
                           NA_RYBNICKACH)
    assert body == _bound(NA_RYBNICKACH, "Pardubice")


def test_an_address_binds_its_street_entry_never_its_house_number(client):
    """"Týnská ulička 610/7": the house number never reaches the binder; the
    street entry binds by the verbatim step beside "Týnská" (R2-M4)."""
    pick = {
        "name": "Týnská ulička 610/7", "type": "regional.address",
        "lat": TYNSKA_ULICKA.point[0], "lng": TYNSKA_ULICKA.point[1],
        "regional_structure": [
            {"name": "610/7", "type": "regional.address"},
            {"name": "Týnská ulička", "type": "regional.street"},
            {"name": "Praha", "type": "regional.municipality"},
        ],
    }
    body = _street_resolve(client, pick, _PRAHA, (TYNSKA, TYNSKA_ULICKA), TYNSKA_ULICKA)
    assert body == _bound(TYNSKA_ULICKA, "Praha")


def test_an_address_without_a_street_entry_binds_nothing(client):
    """A village address whose regionalStructure has no street: its name must
    NOT be matched against the register — "Husova" may well be a street there."""
    village = {
        **_ADDRESS_SUGGESTION,
        "name": "Husova 12",
        "regional_structure": [
            {"name": "12", "type": "regional.address"},
            {"name": "Pardubice", "type": "regional.municipality"},
        ],
    }
    body, executed = _resolve_logged(
        client, village, [*_MIRROR_PRESENT, _chain(village, _PARDUBICE)],
    )
    assert body == _unbound("Pardubice")
    assert _STREETS_IN_OBEC_SQL not in executed


def test_a_malformed_request_is_a_422_not_a_500(client):
    """R2-M1: the regionalStructure entries are typed at the boundary, and the
    pick's point, type and name are required."""
    _override_conn([])
    for bad in (
        {**_ADDRESS_SUGGESTION, "regional_structure": [{"type": "regional.street", "name": 5}]},
        {**_ADDRESS_SUGGESTION, "regional_structure": ["Na Rybníčkách"]},
        {**_OBEC_SUGGESTION, "lat": None},
        {key: v for key, v in _OBEC_SUGGESTION.items() if key != "type"},
        {key: v for key, v in _OBEC_SUGGESTION.items() if key != "name"},
    ):
        assert client.post("/maps/resolve", json=bad).status_code == 422, bad


def test_resolve_without_the_mirror_keeps_the_level_with_no_code(client):
    body = _resolve(client, _OBEC_SUGGESTION, [
        _Expect("SELECT to_regclass('public.ruian_admin_units') IS NOT NULL", None, (None,)),
    ])
    assert body == {"level": "obec", **_NO_CODE, "context": None}


def test_resolve_no_current_registry_version_binds_nothing(client):
    body = _resolve(client, _OBEC_SUGGESTION, [
        _MIRROR_PRESENT[0], _Expect(maps._CURRENT_VERSION_SQL, None, None),
    ])
    assert body == {"level": "obec", **_NO_CODE, "context": None}


def test_a_registry_read_error_is_a_500_never_no_code(client):
    """A DB error must reach the SPA as a failure ("couldn't verify this place"),
    never as `id: None`, which it would show as "RÚIAN doesn't know this place"."""
    class _Broken(_FakeCursor):
        def execute(self, sql: str, params: Any = None) -> None:
            raise RuntimeError("connection reset")

    class _BrokenConn(_FakeConn):
        def cursor(self) -> _FakeCursor:
            return _Broken([], [])

    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: _BrokenConn([])
    res = TestClient(api_main.app, raise_server_exceptions=False).post(
        "/maps/resolve", json=_OBEC_SUGGESTION,
    )
    assert res.status_code == 500


def test_resolve_foreign_point_binds_nothing(client):
    body = _resolve(client, _OBEC_SUGGESTION, [
        *_MIRROR_PRESENT, _chain(_OBEC_SUGGESTION, []),
    ])
    assert body == {"level": "obec", **_NO_CODE, "context": None}


@pytest.mark.parametrize("type_", ["regional.country", "regional.unknown_thing", "poi"])
def test_a_country_or_unknown_type_has_no_level(client, type_):
    """A country narrows nothing and a POI has no register unit: level None, and
    no statement is issued at all."""
    body = _resolve(client, {"lat": 50.0, "lng": 14.0, "type": type_, "name": "Česko"}, [])
    assert body == {"level": None, **_NO_CODE, "context": None}


def test_an_old_bundle_fails_closed(client):
    """Deploy skew: an SPA built before this contract. Its extra `label` / `raw`
    keys are ignored (never a 422), but the answer carries no `kind` and no `lat`,
    which the old bundle reads as unresolved; its name-reader call then gets a
    404, because the reader is gone — so its chip stays code-less and matches
    nothing rather than every unit its name means."""
    body = _resolve(
        client,
        {**_OBEC_SUGGESTION, "label": "Jihlava, okres Jihlava", "raw": {"x": 1},
         "regional_structure": []},
        [*_MIRROR_PRESENT, _chain(_OBEC_SUGGESTION, _JIHLAVA)],
    )
    assert body == {"level": "obec", "id": 586846, "name": "Jihlava", "context": None}
    _override_conn([])
    res = client.post("/maps/resolve-names", json={"chips": [{"name": "Pardubice"}]})
    assert res.status_code == 404


def test_the_level_map_speaks_the_five_chip_levels():
    from api.location_filter import LEVEL_ORDER

    assert set(maps._TYPE_LEVEL.values()) == set(LEVEL_ORDER)
    assert "poi" not in maps._TYPE_LEVEL


# ---------------- containing_obec_kod (the MF hand-off for a typed point) ----------------


def _pip_at(lat: float, lng: float, rows: list[tuple]) -> list[_Expect]:
    return [
        _Expect(maps._CURRENT_VERSION_SQL, None, (_VERSION,)),
        _Expect(maps._CONTAINING_CHAIN_SQL, ([0], [lat], [lng], _VERSION), rows),
    ]


def test_containing_obec_kod_answers_the_pipped_obec_or_none():
    assert maps.containing_obec_kod(
        _FakeConn(_pip_at(49.4, 15.59, _JIHLAVA)), lat=49.4, lng=15.59,
    ) == 586846
    assert maps.containing_obec_kod(
        _FakeConn(_pip_at(41.4, 2.17, [])), lat=41.4, lng=2.17,
    ) is None
    no_version = [_Expect(maps._CURRENT_VERSION_SQL, None, None)]
    assert maps.containing_obec_kod(_FakeConn(no_version), lat=49.4, lng=15.59) is None
    assert maps.containing_obec_kod(_FakeConn([]), lat=None, lng=15.59) is None


def test_containing_obec_kod_raises_a_query_error_instead_of_reading_it_as_no_obec():
    """A "no obec" answer becomes mf_reference()'s location_unknown -- a confident note a
    run would freeze. A failed query must reach the caller's own guard instead."""
    class _Broken(_FakeCursor):
        def execute(self, sql: str, params: Any = None) -> None:
            raise RuntimeError("statement timeout")

    class _BrokenConn(_FakeConn):
        def cursor(self) -> _FakeCursor:
            return _Broken([], [])

    with pytest.raises(RuntimeError):
        maps.containing_obec_kod(_BrokenConn([]), lat=49.4, lng=15.59)
