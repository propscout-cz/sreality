"""Migration 636's rails (ONE PIN W4): the property readers re-keyed from the canonical ad to the location ad, and the CHECK."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
BROWSE_SOURCE = MIGRATIONS / "625_tom_observed.sql"
PUBLIC_SOURCE = MIGRATIONS / "617_measure_tom_days.sql"
PROXIMITY_SOURCE = MIGRATIONS / "508_location_w4c_legacy_drops.sql"

# Read live 2026-10-20 (W4): md5(pg_get_viewdef) of the two views (625's and 617's) and md5(prosrc) of 508's function.
LIVE_BROWSE_PROJECTION = "80fde37bd22abb86d5e93bcc0c41fa38"
LIVE_PROPERTIES_PUBLIC = "600434e950ee7db543213167e9e069f7"
LIVE_CITY_PROXIMITY = "6b7931e8b358e78e1b784d05b50ff982"
CONSTRAINT = "properties_location_ad_present"
OLD_JOIN = "left join listing_location ll on ll.listing_id=p.repr_listing_ref_id"
NEW_JOIN = "left join listing_location ll on ll.listing_id=p.location_listing_ref_id"
OLD_PROXIMITY_JOIN = "join listing_location ll on ll.listing_id=p2.repr_listing_ref_id"
NEW_PROXIMITY_JOIN = "join listing_location ll on ll.listing_id=p2.location_listing_ref_id"
_NOISE = re.compile(r"--[^\n]*|'(?:[^']|'')*'")
_QUOTE = re.compile(r"\bas\s+(\$[a-z_]*\$)", re.I)


def _one(pattern: str) -> Path:
    found = sorted(MIGRATIONS.glob(pattern))
    assert len(found) == 1, f"expected one migrations/{pattern}, found {[p.name for p in found]}"
    return found[0]


def _migration() -> str:
    return _one("636_*.sql").read_text(encoding="utf-8")


def _revert() -> str:
    return _one("reverts/636_revert_*.sql").read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Comments gone, lower case, one space between words and none around punctuation: layout never decides."""
    code = " ".join(re.sub(r"--[^\n]*", "", text).split()).lower()
    return re.sub(r"\s*([(),;=<>|:]+)\s*", r"\1", code)


def _masked(text: str) -> str:
    """Comments and string literals blanked, offsets kept: prose never reads as DDL."""
    return _NOISE.sub(lambda m: " " * len(m.group(0)), text).lower()


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _view(text: str, name: str) -> str:
    """The one `create [or replace] view <name> as ... ;` statement, flattened, the schema prefix dropped."""
    masked = _masked(text)
    hits = list(re.finditer(rf"\bcreate\s+(?:or\s+replace\s+)?view\s+(?:public\.)?{name}\s+as\b", masked))
    assert len(hits) == 1, f"expected one CREATE VIEW {name}, found {len(hits)}"
    start = hits[0].start()
    statement = _flat(text[start: masked.index(";", start) + 1])
    return re.sub(r"^create (?:or replace )?view (?:public\.)?", "create or replace view ", statement)


def _function(text: str, name: str) -> tuple[str, str]:
    """The CREATE's header (flattened) and its body exactly as pg_proc.prosrc stores it."""
    masked = _masked(text)
    hits = list(re.finditer(rf"\bcreate\s+or\s+replace\s+function\s+(?:public\.)?{name}\s*\(", masked))
    assert len(hits) == 1, f"expected one CREATE FUNCTION {name}, found {len(hits)}"
    quote = _QUOTE.search(text, hits[0].end())
    assert quote, f"{name} has no dollar-quoted body"
    header = re.sub(r"function public\.", "function ", _flat(text[hits[0].start(): quote.start()]))
    return header, text[quote.end(): text.index(quote.group(1), quote.end())]


def _block(text: str, tag: str) -> str:
    m = re.search(rf"do \${tag}\$(.*?)\${tag}\$\s*;", text, re.I | re.S)
    assert m, f"no `do ${tag}$ ... ${tag}$;` block"
    return m.group(1)


def _differs(expected: str, actual: str) -> str:
    """Where two flattened statements part, with context."""
    i = next((n for n, (a, b) in enumerate(zip(expected, actual)) if a != b), min(len(expected), len(actual)))
    return f"expected ...{expected[max(0, i - 60): i + 60]!r}...\n  actual ...{actual[max(0, i - 60): i + 60]!r}..."


def _rekeyed_browse() -> str:
    old = _view(BROWSE_SOURCE.read_text(encoding="utf-8"), "browse_projection")
    assert old.count(OLD_JOIN) == 1
    return old.replace(OLD_JOIN, NEW_JOIN)


def _rekeyed_public() -> tuple[str, str]:
    """617's properties_public with the place join re-keyed, and the same with the pointer appended (bare or aliased)."""
    old = _view(PUBLIC_SOURCE.read_text(encoding="utf-8"), "properties_public")
    assert old.count(OLD_JOIN) == 1 and old.count(" from properties p ") == 1
    rekeyed = old.replace(OLD_JOIN, NEW_JOIN)
    return old, rekeyed


def _appended(view: str) -> set[str]:
    return {view.replace(" from properties p ", f",{c} from properties p ", 1)
            for c in ("p.location_listing_ref_id", "p.location_listing_ref_id as location_listing_ref_id")}


def test_the_file_and_its_revert_exist() -> None:
    _migration()
    _revert()


def test_the_gated_function_md5_is_508s_body() -> None:
    """The LIVE pin is the body 508 created, not a transcription."""
    assert _md5(_function(PROXIMITY_SOURCE.read_text(encoding="utf-8"), "recompute_city_proximity")[1]) == (
        LIVE_CITY_PROXIMITY)


def test_the_precondition_gates_each_restated_body_and_the_pointer_pair() -> None:
    """RED by: a gate missing a live md5 (drift overwritten), or a CHECK added over a row with one pointer."""
    text = _migration()
    pre = _block(text, "pre")
    for md5 in (LIVE_BROWSE_PROJECTION, LIVE_PROPERTIES_PUBLIC, LIVE_CITY_PROXIMITY):
        assert md5 in pre, f"$pre$ does not gate on {md5}"
    assert "pg_get_viewdef" in pre.lower() and "prosrc" in pre.lower()
    flat = _flat(pre)
    assert re.search(r"\((?:\w+\.)?(repr|location)_listing_ref_id is null\)(?:<>|!=| ?is distinct from ?)"
                     r"\((?:\w+\.)?(?!\1)(?:repr|location)_listing_ref_id is null\)", flat), (
        "$pre$ does not refuse a row whose two pointers disagree on NULL")
    masked = _masked(text)
    change = re.search(r"\b(?:create\s+(?:or\s+replace\s+)?(?:view|function)|alter\s+table|"
                       r"update\s+(?:public\.)?properties)\b", masked)
    assert change and masked.index("$pre$;") < change.start(), "the gate runs after the first change"


def test_the_check_is_added_not_valid_behind_a_lock_limit_then_validated() -> None:
    """NOT VALID takes the lock for a catalog write only; VALIDATE scans without blocking reads or writes."""
    flat = _flat(_migration())
    add = re.search(rf"alter table (?:only )?(?:public\.)?properties add constraint {CONSTRAINT} "
                    r"check\(\(repr_listing_ref_id is null\)=\(location_listing_ref_id is null\)\) ?not valid;", flat)
    assert add, f"no `add constraint {CONSTRAINT} check ((repr is null) = (location is null)) not valid`"
    assert re.search(r"set (?:local )?lock_timeout(?:=| to )'[^']+';", flat[: add.start()]), (
        "the ALTER is not guarded by a lock_timeout")
    validate = re.search(rf"alter table (?:only )?(?:public\.)?properties validate constraint {CONSTRAINT};", flat)
    assert validate and validate.start() > add.end(), "the CHECK is never validated after it is added"


def test_browse_projection_is_625s_with_only_the_place_join_rekeyed() -> None:
    """listing_id and source_id_native stay the canonical ad's link; the column list is 625's, so browse_list
    needs no bridge; the MF lateral follows the place because it reads `ll`."""
    expected, actual = _rekeyed_browse(), _view(_migration(), "browse_projection")
    assert actual == expected, _differs(expected, actual)


def test_properties_public_is_617s_with_the_place_join_rekeyed_and_the_pointer_appended() -> None:
    """The ad-facts join stays canonical; `location_listing_ref_id` is appended for the watchdog's served rule."""
    _, rekeyed = _rekeyed_public()
    actual = _view(_migration(), "properties_public")
    assert actual in _appended(rekeyed), _differs(sorted(_appended(rekeyed))[0], actual)
    assert "left join listings l on l.id=p.repr_listing_ref_id" in actual


def _guard(body: str) -> tuple[str, str]:
    """The function's code with the race guard and the pointer it selects taken out, and that pointer's column."""
    code = _flat(body)
    guard = re.search(r"where p\.id=s\.id and (?:p\.location_listing_ref_id=s\.(\w+)|s\.(\w+)=p\.location_listing_ref_id)"
                      r"(?=;)", code)
    assert guard, "the outer UPDATE does not refuse a row whose location ad moved while it ran"
    column = guard.group(1) or guard.group(2)
    code = code[: guard.start()] + "where p.id=s.id" + code[guard.end():]
    item = rf"(?:p2\.location_listing_ref_id|ll\.listing_id)(?: as {column})?"
    selected = re.subn(rf"(select p2\.id,){item},", r"\1", code, count=1)
    if not selected[1]:
        selected = re.subn(rf",{item}( from properties p2 )", r"\1", code, count=1)
    assert selected[1] == 1, f"the subquery does not select the location ad as `{column}` for the guard"
    return selected[0], column


def test_city_proximity_is_508s_read_at_the_location_ad_with_a_race_guard() -> None:
    """Only the place join's key, the pointer the subquery selects and the UPDATE's guard change."""
    text = _migration()
    header, body = _function(text, "recompute_city_proximity")
    old_header, old_body = _function(PROXIMITY_SOURCE.read_text(encoding="utf-8"), "recompute_city_proximity")
    assert header == old_header, _differs(old_header, header)
    assert "repr_listing_ref_id" not in _flat(body), "recompute_city_proximity still names the canonical ad"
    assert _flat(body).count(NEW_PROXIMITY_JOIN) == 1, f"no `{NEW_PROXIMITY_JOIN}`"
    reduced, _ = _guard(body)
    expected = _flat(old_body)
    assert expected.count(OLD_PROXIMITY_JOIN) == 1
    expected = expected.replace(OLD_PROXIMITY_JOIN, NEW_PROXIMITY_JOIN)
    assert reduced == expected, _differs(expected, reduced)


def test_the_postcondition_asserts_the_function_body_and_the_check() -> None:
    text = _migration()
    post = _block(text, "post")
    assert _md5(_function(text, "recompute_city_proximity")[1]) in post, "$post$ does not pin this file's body"
    assert CONSTRAINT in post, f"$post$ does not check {CONSTRAINT}"


def test_proximity_is_reset_once_where_the_location_ad_is_not_the_canonical_ad() -> None:
    """The hourly job recomputes about 13.8k properties from their location ad's point (a NULL stamp is its queue)."""
    flat = _flat(_migration())
    resets = re.findall(r"update (?:public\.)?properties(?: \w+)? set city_proximity_computed_at=null where ([^;]*);",
                        flat)
    assert len(resets) == 1, f"expected one proximity reset, found {len(resets)}"
    assert re.search(r"(?:\w+\.)?(?:location|repr)_listing_ref_id(?:<>|!=| ?is distinct from ?)"
                     r"(?:\w+\.)?(?:location|repr)_listing_ref_id", resets[0]), resets[0]
    assert "location_listing_ref_id" in resets[0] and "repr_listing_ref_id" in resets[0]


def _top_level(text: str) -> str:
    """`_masked`, every dollar-quoted body blanked too: the function's own temp-table DROP is not this file's."""
    return re.sub(r"(\$[a-z_]*\$).*?\1", lambda m: " " * len(m.group(0)), _masked(text), flags=re.S)


def test_636_drops_nothing() -> None:
    """Additive: the two views and the function are replaced in place; nothing is dropped."""
    code = _top_level(_migration())
    assert not re.search(r"\bdrop\s+(?:table|view|materialized|function|column|index|trigger|policy|constraint)\b",
                         code)
    assert not re.search(r"\bcascade\b", code)


def test_the_revert_restores_625_617_and_508_and_drops_the_check() -> None:
    text = _revert()
    old_browse = _view(BROWSE_SOURCE.read_text(encoding="utf-8"), "browse_projection")
    browse = _view(text, "browse_projection")
    assert browse == old_browse, _differs(old_browse, browse)
    old_public, _ = _rekeyed_public()
    public = _view(text, "properties_public")
    assert public in {old_public, *_appended(old_public)}, _differs(old_public, public)
    assert _md5(_function(text, "recompute_city_proximity")[1]) == LIVE_CITY_PROXIMITY, (
        "the revert does not re-create 508's body byte for byte")
    flat = _flat(text)
    assert re.search(rf"alter table (?:only )?(?:public\.)?properties drop constraint if exists {CONSTRAINT};", flat), (
        f"the revert does not `drop constraint if exists {CONSTRAINT}`")
    masked = _top_level(text)
    assert not re.search(r"\bcascade\b", masked), "a CASCADE would drop properties_public's dependants silently"
    if re.search(r"\bdrop\s+view\s+(?:if\s+exists\s+)?(?:public\.)?properties_public\b", masked):
        assert re.search(r"grant select on (?:public\.)?properties_public to authenticated;", flat), (
            "a re-created properties_public must restate its grant")
