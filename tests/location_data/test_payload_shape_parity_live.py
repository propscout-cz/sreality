"""The payload-shape predicates EXECUTED against their Python twins (Health dashboard sprint wave 6).

location_data.claims_common spells each payload portal's shape twice: PAYLOAD_SHAPE_READERS in Python and
PAYLOAD_SHAPE_PREDICATES in SQL, which the parsing alarm runs over new ads. The retired SQL was called
parity-tested, yet no test compared it with its reader after 5d3fc621 (and the one deleted there compared key
lists, never executed SQL), so this file sends every predicate over every fixture payload through the database and
asserts it answers what the reader answers, never NULL, and what the retired arms answered on sreality and
bezrealitky (mmreality's predicate is new).
The rails that need no database run in every lane; the executed ones run in CI's migrations lane
(`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`) and skip locally.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from typing import Any

import pytest

from location_data import claims_intake, contracts
from location_data.claims_common import (
    BEZREALITKY_PAYLOAD_KEYS,
    MMREALITY_PAYLOAD_KEYS,
    PAYLOAD_SHAPE_PREDICATES,
    PAYLOAD_SHAPE_READERS,
    PAYLOAD_SHAPE_REMEDIES,
    SREALITY_LEGACY_KEYS,
    SREALITY_POST_CUTOVER_KEYS,
    SUBSTRATE_PAYLOAD,
)
from tests.location_data import claim_intake_fixtures as fx

_DB_URL = os.environ.get("TEST_DATABASE_URL")
_REQUIRED = os.environ.get("DB_RAILS_REQUIRED") == "1"
live = pytest.mark.skipif(not _DB_URL and not _REQUIRED,
                          reason="TEST_DATABASE_URL not set — the executed parity runs in CI's migrations lane")

# Where each portal's shape keys sit in its payload, and which key marks a shape without being read by any entry.
_KEYS = {"sreality": SREALITY_POST_CUTOVER_KEYS, "bezrealitky": BEZREALITKY_PAYLOAD_KEYS,
         "mmreality": MMREALITY_PAYLOAD_KEYS}
_UNDER = {"sreality": "/locality/", "bezrealitky": "/", "mmreality": "/"}
_SHAPE_MARKERS = {"entity_type"}

# The retired check's two arms (scripts/verify_pipeline.py until wave 6), frozen as the fold-equivalence reference.
_RETIRED = {
    "sreality": """(CASE
      WHEN jsonb_typeof(raw_json->'locality') IS DISTINCT FROM 'object' THEN 'absent'
      WHEN raw_json->'locality' ?| array['gps_lat','gps_lon','entity_type','inaccuracy_type','city','citypart'] THEN 'post_cutover'
      WHEN raw_json->'locality' ?| array['name','value','accuracy'] THEN 'legacy'
      ELSE 'absent'
    END) = 'post_cutover'""",
    "bezrealitky": "raw_json ? 'ruianId'",
}


def _without(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k != key}


# Every payload the claim-intake tests read, as stored, plus the traps a key test falls into: a key sent null
# (content, so readable), the right key one level off, a list or a string naming the keys (`?` and `?|` match
# array elements and scalars, so only the object test keeps them unreadable), and payloads that are no object.
_STORED = [(name.lower(), value) for name, value in sorted(vars(fx).items())
           if name.isupper() and not name.startswith("_") and isinstance(value, dict)]
_TRAPS: list[tuple[str, Any]] = [
    *((f"sreality {key} alone", {"locality": {key: 1}}) for key in SREALITY_POST_CUTOVER_KEYS),
    *((f"sreality legacy {key} alone", {"locality": {key: "Adresa"}}) for key in SREALITY_LEGACY_KEYS),
    ("sreality city sent null", {"locality": {"city": None}}),
    ("sreality empty locality", {"locality": {}}),
    ("sreality locality null", {"locality": None}),
    ("sreality locality a string", {"locality": "Praha"}),
    ("sreality locality a list of its keys", {"locality": list(SREALITY_POST_CUTOVER_KEYS)}),
    ("sreality keys at the top level", {key: 1 for key in SREALITY_POST_CUTOVER_KEYS}),
    ("bezrealitky ruianId sent null", {**fx.BEZREALITKY, "ruianId": None}),
    ("bezrealitky without ruianId", _without(fx.BEZREALITKY, "ruianId")),
    ("bezrealitky ruianId nested", {"gps": {"ruianId": "22698884"}}),
    ("bezrealitky ruianid in lower case", {"ruianid": "22698884"}),
    ("mmreality without municipality", _without(fx.MMREALITY_ACCURATE, "municipality")),
    ("mmreality without point", _without(fx.MMREALITY_ACCURATE, "point")),
    ("mmreality both sent null", {"municipality": None, "point": None}),
    ("an empty object", {}),
    ("a list naming every key", [*SREALITY_POST_CUTOVER_KEYS, *BEZREALITKY_PAYLOAD_KEYS,
                                 *MMREALITY_PAYLOAD_KEYS, "locality"]),
    ("a string naming a key", "ruianId"),
    ("a number", 1),
    ("JSON null", None),
]
_PAYLOADS = _STORED + _TRAPS


# --- the rails that need no database --------------------------------------------------------------------------------


def test_each_payload_portal_has_one_reader_one_predicate_and_one_remedy() -> None:
    assert PAYLOAD_SHAPE_READERS.keys() == PAYLOAD_SHAPE_PREDICATES.keys() == PAYLOAD_SHAPE_REMEDIES.keys()
    assert set(PAYLOAD_SHAPE_READERS) == set(_KEYS)


def _reads_payload(entry: contracts.ContractEntry) -> bool:
    spec = claims_intake.READERS.get(str(entry.locator.get("reader")))
    return spec is not None and spec.substrate == SUBSTRATE_PAYLOAD


def test_the_portals_with_a_predicate_are_exactly_those_whose_contract_reads_the_payload() -> None:
    """A portal whose contract gains a payload entry gets a predicate, or the parsing alarm skips it silently."""
    reads_payload = {c.source for c in contracts.load_all() if any(map(_reads_payload, c.entries))}
    assert reads_payload == set(PAYLOAD_SHAPE_PREDICATES)


def test_every_key_a_predicate_requires_is_one_its_contract_reads() -> None:
    """A contract re-pointed at a new path takes its shape keys along in the same change."""
    by_portal = {c.source: c for c in contracts.load_all()}
    for portal, keys in _KEYS.items():
        pointers = {str(value) for e in by_portal[portal].entries
                    for read in (e.locator, *(e.locator.get("fallback") or ()))
                    for name, value in read.items() if name.endswith("pointer")}
        under = _UNDER[portal]
        read_keys = {p[len(under):].split("/")[0] for p in pointers if p.startswith(under)}
        assert set(keys) - _SHAPE_MARKERS <= read_keys, portal


def test_each_predicate_renders_its_own_key_list_and_nothing_to_quote() -> None:
    for portal, keys in _KEYS.items():
        assert keys and all(re.fullmatch(r"[A-Za-z_]+", key) for key in keys), portal
        arrays = [re.findall(r"'([A-Za-z_]+)'", body)
                  for body in re.findall(r"array\[(.*?)\]", PAYLOAD_SHAPE_PREDICATES[portal])]
        assert arrays == [list(keys)], portal
        assert "%" not in PAYLOAD_SHAPE_PREDICATES[portal], "psycopg reads % as a placeholder"


def test_the_readers_answer_the_known_payloads_and_every_trap() -> None:
    readable = {name for name, raw in _PAYLOADS if PAYLOAD_SHAPE_READERS["sreality"](raw)}
    assert {"sreality_post_cutover", "sreality_zip_sentinel", "sreality city sent null"} <= readable
    assert not readable & {"sreality_legacy", "sreality_truncated", "sreality locality a list of its keys",
                           "sreality keys at the top level"}
    assert PAYLOAD_SHAPE_READERS["bezrealitky"](fx.BEZREALITKY)
    assert PAYLOAD_SHAPE_READERS["bezrealitky"]({"ruianId": None})
    assert not PAYLOAD_SHAPE_READERS["bezrealitky"](["ruianId"])
    assert all(PAYLOAD_SHAPE_READERS["mmreality"](p) for p in (fx.MMREALITY_ACCURATE, fx.MMREALITY_NOT_ACCURATE))
    assert not PAYLOAD_SHAPE_READERS["mmreality"](_without(fx.MMREALITY_ACCURATE, "point"))


def test_the_fixture_payloads_are_json() -> None:
    assert json.loads(json.dumps([raw for _, raw in _PAYLOADS])) == [raw for _, raw in _PAYLOADS]


# --- executed -------------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn() -> Iterator[Any]:
    """One connection; the statements read no table and write nothing."""
    if not _DB_URL:
        pytest.fail("DB_RAILS_REQUIRED=1 but TEST_DATABASE_URL is not set — the migrations lane is misconfigured "
                    "and this rail would otherwise have skipped green.")
    import psycopg

    connection = psycopg.connect(_DB_URL)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _answers(conn: Any, predicate: str, payloads: list[Any]) -> list[bool | None]:
    """The predicate over `l.raw_json`, one answer per payload in order, as the database computes it."""
    from psycopg.types.json import Jsonb

    rows = conn.execute(
        f"select l.ord, {predicate} from jsonb_array_elements(%s) with ordinality as l(raw_json, ord) order by l.ord",
        (Jsonb(payloads),)).fetchall()
    return [answer for _, answer in rows]


@live
@pytest.mark.parametrize("portal", sorted(PAYLOAD_SHAPE_PREDICATES))
def test_each_predicate_answers_what_its_reader_answers_and_never_null(conn: Any, portal: str) -> None:
    names = [name for name, _ in _PAYLOADS]
    payloads = [raw for _, raw in _PAYLOADS]
    answers = _answers(conn, PAYLOAD_SHAPE_PREDICATES[portal], payloads)
    assert len(answers) == len(payloads)
    assert [name for name, answer in zip(names, answers) if answer is None] == [], "a NULL reads readable"
    reader = PAYLOAD_SHAPE_READERS[portal]
    disagree = [(name, sql, reader(raw)) for name, raw, sql in zip(names, payloads, answers) if sql != reader(raw)]
    assert disagree == []
    assert set(answers) == {True, False}, "the corpus holds both answers, so agreement is never vacuous"


@live
@pytest.mark.parametrize("portal", sorted(PAYLOAD_SHAPE_PREDICATES))
def test_a_null_payload_reads_unreadable_never_null(conn: Any, portal: str) -> None:
    (answer,) = conn.execute(
        f"select {PAYLOAD_SHAPE_PREDICATES[portal]} from (select null::jsonb as raw_json) l").fetchone()
    assert answer is False


@live
@pytest.mark.parametrize("portal", sorted(_RETIRED))
def test_sreality_and_bezrealitky_read_as_the_retired_arms_did_on_every_stored_shape(conn: Any, portal: str) -> None:
    """Fold equivalence over object payloads, the only shape a stored ad has: on a list or a string naming the key
    the retired bezrealitky arm read readable, and on SQL NULL it read NULL (counted readable)."""
    objects = [raw for _, raw in _PAYLOADS if isinstance(raw, dict)]
    assert _answers(conn, PAYLOAD_SHAPE_PREDICATES[portal], objects) == _answers(conn, _RETIRED[portal], objects)
