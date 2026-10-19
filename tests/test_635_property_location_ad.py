"""Migration 635's rails (ONE PIN W3, R1): the location ad is rank 1 of a window over 588's canonical one."""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
PATH = MIGRATIONS / "635_property_location_ad.sql"
REVERT = MIGRATIONS / "reverts" / "635_revert_property_location_ad.sql"
SOURCE = MIGRATIONS / "588_canonical_order_and_portal_dates.sql"
ROLLUP = ROOT / "scripts" / "recompute_property_stats.py"
BIRTH = ROOT / "scraper" / "db.py"

# md5(pg_proc.prosrc) of the one live body of property_canonical_listings, 588's, read 2026-10-19 (W3).
LIVE_588 = "645aa6268a66ea2dd653d8d310c15d61"
SIGNATURE = "public.property_canonical_listings(bigint)"
REVOKE = f"revoke execute on function {SIGNATURE} from public, anon, authenticated;"
GRANT = f"grant execute on function {SIGNATURE} to service_role;"
# Rule 25's served test as a tier, qualifiers and casts dropped: a map point 2, determined foreign 1, else 0.
TIER = "case when geom is not null then 2 when country_status = 'foreign' then 1 else 0 end"
DROP = re.compile(r"drop function (?:if exists )?public\.property_canonical_listings\(bigint\);")
COLUMN = re.compile(r"alter table (?:public\.)?properties add column (?:if not exists )?"
                    r"location_listing_ref_id bigint;")
INDEX = re.compile(r"create index (?:concurrently )?(?:if not exists )?properties_location_listing_ref_active_idx "
                   r"on (?:public\.)?properties(?: using btree)?\(location_listing_ref_id\) "
                   r"where status = 'active' and location_listing_ref_id is not null;")
_CREATE = re.compile(r"create\s+(?:or\s+replace\s+)?function\s+public\.property_canonical_listings\s*\(", re.I)
_QUOTE = re.compile(r"\bas\s+(\$[a-z_]*\$)", re.I)
_KEY = re.compile(r"^(?P<expr>.*?)(?: (?P<dir>asc|desc))?(?: nulls (?P<nulls>first|last))?$")


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} is not written yet (its author works in parallel)")
    return path.read_text(encoding="utf-8")


def _code(text: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", text).split()).lower()


def _tight(text: str) -> str:
    """`_code`, with no space inside a bracket and one after a comma: whitespace never decides a match."""
    s = re.sub(r"\s*\(\s*", "(", _code(text))
    return re.sub(r"\s*,\s*", ", ", re.sub(r"\s*\)", ")", s))


def _unquoted(code: str) -> str:
    """String literals emptied: a comment's prose never reads as DDL."""
    return re.sub(r"'(?:[^']|'')*'", "''", code)


def _function(text: str) -> tuple[str, str]:
    """The CREATE's header (raw text) and its body exactly as pg_proc.prosrc stores it."""
    for m in _CREATE.finditer(text):
        if "--" in text[text.rfind("\n", 0, m.start()) + 1: m.start()]:
            continue
        quote = _QUOTE.search(text, m.end())
        assert quote, "the CREATE has no dollar-quoted body"
        return text[m.start(): quote.start()], text[quote.end(): text.index(quote.group(1), quote.end())]
    raise AssertionError("no create function public.property_canonical_listings")


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _windows(body: str) -> list[str]:
    """The inside of each `over (...)`, in body order."""
    code, out = _tight(body), []
    for m in re.finditer(r"\bover\(", code):
        depth, i = 1, m.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(code[i], 0)
            i += 1
        out.append(code[m.end(): i - 1])
    return out


def _keys(window: str) -> list[str]:
    assert window.startswith("order by "), f"a window that is not one bare ORDER BY: {window!r}"
    keys, depth, key = [], 0, ""
    for ch in window[len("order by "):]:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            keys, key = [*keys, key.strip()], ""
        else:
            key += ch
    return [*keys, key.strip()]


def _bare(expr: str) -> str:
    return re.sub(r"::[a-z_]+", "", re.sub(r"\b[a-z_]\w*\.(?=[a-z_])", "", expr)).replace("(", "").replace(")", "")


def _resolve(expr: str, code: str) -> str:
    """An output alias (`c.served_tier`) read back to the expression that defines it, qualifiers dropped."""
    if re.fullmatch(r"(?:[a-z_]\w*\.)?[a-z_]\w*", expr):
        m = re.search(rf" as {expr.rsplit('.', 1)[-1]}\b", code)
        if m:
            depth, i = 0, m.start()
            while i > 0 and not (depth == 0 and (code[i - 1] in ",(" or code[:i].endswith("select "))):
                depth += {")": 1, "(": -1}.get(code[i - 1], 0)
                i -= 1
            expr = code[i: m.start()].strip()
    return _bare(expr)


def _block(text: str, tag: str) -> str:
    m = re.search(rf"do \${tag}\$(.*?)\${tag}\$\s*;", text, re.I | re.S)
    assert m, f"no `do ${tag}$ ... ${tag}$;` block"
    return m.group(1)


def _assignments(path: Path) -> dict[str, ast.expr]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {t.id: node.value for node in tree.body if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name)}


def _literal(node: ast.expr) -> str:
    """A string's text; an f-string's literal parts with each splice as `{}`."""
    if isinstance(node, ast.Constant):
        return node.value
    assert isinstance(node, ast.JoinedStr)
    return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)


def test_the_gated_md5_is_588s_body() -> None:
    """The LIVE pin is the body 588 created, not a transcription."""
    assert _md5(_function(SOURCE.read_text(encoding="utf-8"))[1]) == LIVE_588


def test_the_canonical_window_is_588s_byte_for_byte() -> None:
    """R3: the canonical order is unchanged; only an outer select may wrap it (a window cannot order by a sibling)."""
    body = _function(_read(PATH))[1]
    (old,) = _windows(_function(SOURCE.read_text(encoding="utf-8"))[1])
    windows = _windows(body)
    assert len(windows) == 2, f"expected the canonical and the location window, found {len(windows)}"
    assert old in windows, "the canonical window is not 588's text"
    code = _tight(body)
    assert "from public.listings l left join public.listing_location ll on ll.listing_id = l.id" in code
    assert "where l.property_id = p_property_id" in code


def test_the_location_window_is_tier_then_rung_then_canonical_rank_and_nothing_else() -> None:
    """R1: precision over activity; is_active orders only through canonical_rank, never as a key of its own."""
    body = _function(_read(PATH))[1]
    (old,) = _windows(_function(SOURCE.read_text(encoding="utf-8"))[1])
    (window,) = [w for w in _windows(body) if w != old]
    code = _tight(body)
    keys = [_KEY.match(k).groupdict() for k in _keys(window)]
    assert len(keys) == 3, f"the location order has {len(keys)} keys, not tier, rung, canonical_rank: {window!r}"
    tier, rung, canon = keys
    assert _resolve(tier["expr"], code) == TIER and tier["dir"] == "desc", f"tier key: {tier}"
    assert _resolve(rung["expr"], code) == "rank" and (rung["dir"], rung["nulls"]) == ("desc", "last"), (
        f"rung key: {rung}")
    assert canon["expr"].rsplit(".", 1)[-1] == "canonical_rank" and canon["dir"] in (None, "asc"), (
        f"tie-break key: {canon}")
    assert canon["nulls"] is None
    assert "is_active" not in _resolve(tier["expr"], code) + _resolve(rung["expr"], code)
    joins = re.findall(r"(\w+) join (?:public\.)?location_granularity_rank\b", code)
    assert "location_granularity_rank" in code and all(j == "left" for j in joins), (
        "an inner join to the rung table drops every ad with no rung")


def test_the_function_is_dropped_and_recreated_inlinable_with_three_columns() -> None:
    """The output columns change, so DROP + CREATE; SQL, stable, invoker, no SET, not strict (588: still inlined)."""
    text = _read(PATH)
    code = _tight(text)
    header = _tight(_function(text)[0])
    (drop,) = DROP.finditer(code)
    assert drop.end() <= code.index(header)
    returns = re.search(r"returns table\(listing_id bigint, canonical_rank integer, location_rank integer\) (.*)$",
                        header)
    assert returns and sorted(returns.group(1).split()) == ["language", "sql", "stable"], header
    assert not re.search(r"\bdrop (table|column|view|materialized)\b", _unquoted(code)), (
        "635 is additive but for the function")
    assert "comment on function public.property_canonical_listings(bigint) is" in code, "DROP lost 588's comment"


def test_execute_is_service_role_only() -> None:
    """588's ACL restated after the CREATE: the rollup is its one caller."""
    text = _read(PATH)
    code = _tight(text)
    create = code.index(_tight(_function(text)[0]))
    assert code.index(REVOKE) > create and code.index(GRANT) > create
    assert not re.search(r"grant execute on function public\.property_canonical_listings\(bigint\) to "
                         r"(?!service_role;)", code)


def test_the_column_is_a_bare_nullable_bigint_with_a_comment_behind_a_lock_limit() -> None:
    """Metadata-only: no default, no NOT NULL, no foreign key (ads are never deleted; validating would scan)."""
    code = _tight(_read(PATH))
    (alter,) = COLUMN.finditer(code)
    ddl = _unquoted(code)
    assert len(re.findall(r"alter table (?:public\.)?properties\b", ddl)) == 1
    assert not re.search(r"\breferences\b|\bforeign key\b", ddl)
    assert re.search(r"set (?:local )?lock_timeout (?:=|to) '[^']+';", code[: alter.start()])
    assert "comment on column public.properties.location_listing_ref_id is" in code


def test_the_partial_index_twins_the_canonical_pointers() -> None:
    """properties_repr_listing_ref_active_idx's twin: the spatial-first cohort probes it index-only (W0, R8)."""
    assert len(INDEX.findall(_tight(_read(PATH)))) == 1


def test_the_precondition_gates_588s_body_and_its_own() -> None:
    """RED by: a gate missing 588's md5 (drift overwritten) or 635's own (a re-run refuses)."""
    text = _read(PATH)
    pre = _block(text, "pre")
    assert LIVE_588 in pre
    assert _md5(_function(text)[1]) in pre
    assert text.lower().index("do $pre$") < text.index(_function(text)[0]), "the gate runs after the CREATE"


def test_the_postcondition_asserts_this_files_body() -> None:
    text = _read(PATH)
    assert _md5(_function(text)[1]) in _block(text, "post")


def test_the_revert_restores_588_and_drops_the_column_and_index() -> None:
    text = _read(REVERT)
    header, body = _function(text)
    code = _tight(text)
    assert _md5(body) == LIVE_588, "the revert does not re-create 588's body byte for byte"
    assert re.search(r"returns table\(listing_id bigint, canonical_rank integer\) ", _tight(header))
    assert DROP.search(code)
    assert REVOKE in code and GRANT in code
    assert "comment on function public.property_canonical_listings(bigint) is" in code
    assert re.search(r"drop index (?:concurrently )?if exists (?:public\.)?properties_location_listing_ref_active_idx;",
                     code)
    assert re.search(r"alter table (?:public\.)?properties drop column if exists location_listing_ref_id;", code)


def test_birth_names_its_one_ad_as_both_pointers() -> None:
    """The one birth path (also a split's new property) sets both in one INSERT."""
    sql = _tight(_literal(_assignments(BIRTH)["NEW_SINGLETONS_SQL"]))
    assert ("insert into properties(repr_listing_ref_id, location_listing_ref_id, is_active) "
            "select l.id, l.id, l.is_active from listings l") in sql


def test_the_rollup_sets_the_location_ad_in_the_canonical_ads_update() -> None:
    """Rank 1 of location_rank, stored like the canonical ad: both pointers in one UPDATE, both or neither."""
    sql = _tight(_literal(_assignments(ROLLUP)["_RECOMPUTE_BATCH_SQL"]))
    assert re.search(r"kids as\(select l\.\*, [^)]*\bo\.location_rank\b[^)]*from batch b "
                     r"cross join lateral property_canonical_listings\(b\.id\) o", sql)
    assert "loc as(select * from kids where location_rank = 1)" in sql
    assert sql.count("update properties p set") == 1
    update = sql[sql.index("update properties p set"):]
    sets, rest = update.split(" from child_agg r ", 1)
    join = re.search(r"(left )?join loc (\w+) on \2\.property_id = r\.pid\b", rest)
    assert join and not join.group(1), "the location ad joins the UPDATE like the canonical ad (inner)"
    assert f"location_listing_ref_id = {join.group(2)}.id, " in sets
    assert "repr_listing_ref_id = c.id, " in sets


def test_the_rollup_spells_no_location_order_of_its_own() -> None:
    """One spelling: the order lives in property_canonical_listings, never beside its caller."""
    sql = _code(_literal(_assignments(ROLLUP)["_RECOMPUTE_BATCH_SQL"]))
    for word in ("location_granularity_rank", "country_status", "row_number"):
        assert word not in sql, f"the rollup re-derives the location order ({word})"


def test_the_no_ads_reset_nulls_both_pointers() -> None:
    node = _assignments(ROLLUP)["_NO_ADS"]
    assert isinstance(node, ast.Dict)
    reset = {k.value: _literal(v) for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
    assert (reset.get("repr_listing_ref_id"), reset.get("location_listing_ref_id")) == ("NULL", "NULL")
