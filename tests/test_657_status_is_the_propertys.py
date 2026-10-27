"""Migration 657: a row's status is its property's (operator ruling 2026-10-22). Offline, text only.

The portal rule keeps its signature and becomes two independent conjuncts (the status on the property,
the portal side over active_sources under `active`, all_sources otherwise); the Stats pivot is 648's
statement with 634's three lens edits taken back and nothing else. Both applies are gated on the live
body or this file's own; no signature changes, so nothing is dropped and no ACL is restated. What the
rule answers is executed against tests/fixtures/portal_rule.json by tests/test_portal_rule.py.
"""

from __future__ import annotations

import hashlib
import re

from tests.migration_defs import MIGRATIONS, latest_definition

NAME = "657_status_is_the_propertys.sql"
SQL = (MIGRATIONS / NAME).read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL)
S597 = (MIGRATIONS / "597_portal_exclusion.sql").read_text(encoding="utf-8")
S648 = (MIGRATIONS / "648_browse_energy_rating_filter.sql").read_text(encoding="utf-8")
STATS = "browse_stats_properties"
RULE = "portal_status_matches"


def _statement(text: str, fn: str) -> str:
    at = text.index(f"create or replace function public.{fn}(")
    end = "$function$;" if fn == STATS else "$$;"
    return text[at: text.index(end, at) + len(end)]


def _md5(statement: str, quote: str) -> str:
    return hashlib.md5(statement.split(quote)[1].encode()).hexdigest()


def _without_the_lens(s: str) -> str:
    """648's pivot with 634's three edits taken back (tests/test_stats_status_lens_migration.py adds them)."""
    s = s.replace("l.cast_obce_id, l.ulice_id, l.all_sources, l.active_sources\n    from browse_list l\n",
                  "l.cast_obce_id, l.ulice_id\n    from browse_list l\n")
    at = s.index("  keyed as (\n")
    end = s.index("      l.category_main, l.category_type, l.price_per_m2_basis,\n", at)
    s = (s[:at] + "  keyed as (\n    select l.is_active, l.category_main, l.category_type, l.price_per_m2_basis,\n"
         + s[end + len("      l.category_main, l.category_type, l.price_per_m2_basis,\n"):])
    at = s.index("and (c1 is null or kc is not null)\n") + len("and (c1 is null or kc is not null)\n")
    return s[:at] + s[s.index("  ),", at):]


def test_these_are_the_latest_definitions() -> None:
    assert latest_definition(STATS).name == "678_stats_small_samples.sql"  # 661/662: one status parameter, 678 the small-sample summary
    assert latest_definition(RULE).name == NAME
    assert latest_definition("browse_map_cells").name == "662_status_filter_contract.sql"
    assert SQL.count(f"create or replace function public.{STATS}(") == 1
    assert SQL.count(f"create or replace function public.{RULE}(") == 1


def test_the_pivot_is_648s_without_the_lens_and_nothing_else() -> None:
    new = _statement(SQL, STATS)
    assert new == _without_the_lens(_statement(S648, STATS))
    body = new.split("$function$")[1]
    for gone in ("property_active", "l.ulice_id, l.all_sources", "'active', null, false"):
        assert gone not in body, gone
    assert body.count("portal_status_matches(") == 1  # the cohort's WHERE, as before


def test_the_rule_keeps_its_signature_and_stays_inlinable() -> None:
    sig = re.compile(rf"create or replace function public\.{RULE}\((.*?)\)\nreturns boolean\n"
                     r"language sql\nimmutable\nparallel safe\nas \$\$", re.S)
    assert sig.search(SQL).group(1) == sig.search(S597).group(1)
    body = _statement(SQL, RULE).split("$$")[1]
    assert body.count("select") == 1 and " from " not in body and "set " not in body
    # the status is the property's; the portal side reads the ads in that status
    assert ("case p_status when 'active' then p_is_active\n"
            "                       when 'inactive' then not p_is_active\n"
            "                       else true end") in body
    assert "case when p_status = 'active' then p_active_sources else p_all_sources end && p_portals" in body
    exclusion = ("  and not coalesce(\n    case when p_excluded_active_only then p_active_sources "
                 "else p_all_sources end && p_excluded, false)\n")
    assert body.endswith(exclusion) and _statement(S597, RULE).split("$$")[1].endswith(exclusion)


def test_the_applies_are_gated_on_the_live_bodies_or_this_files_own() -> None:
    pre = SQL[SQL.index("do $pre$"): SQL.index("$pre$;", SQL.index("do $pre$"))]
    post = SQL[SQL.index("do $post$"): SQL.index("$post$;", SQL.index("do $post$"))]
    for fn, old, new, quote in ((RULE, _statement(S597, RULE), _statement(SQL, RULE), "$$"),
                                (STATS, _statement(S648, STATS), _statement(SQL, STATS), "$function$")):
        assert f"p.proname = '{fn}')\n     not in ('{_md5(old, quote)}', '{_md5(new, quote)}')" in pre, fn
        assert f"p.proname = '{fn}')\n     is distinct from '{_md5(new, quote)}'" in post, fn
    assert _md5(_statement(S648, STATS), "$function$") == "d7e671e5e589b972e30e7ca8a63ad8b6"  # pg_proc, 2026-10-22
    assert _md5(_statement(S597, RULE), "$$") == "2811fc4473f372e5f4597fd1a8a5ab0f"  # pg_proc, 2026-10-22
    assert "has_function_privilege('authenticated'" in post and "prosecdef" in post


def test_nothing_is_dropped_and_no_acl_is_restated() -> None:
    assert "drop function" not in CODE and "revoke" not in CODE
    assert "grant" not in CODE.replace("has_function_privilege", "")
    assert CODE.index("begin;") < CODE.index("do $pre$") < CODE.index(
        f"create or replace function public.{RULE}(") < CODE.index(
        f"create or replace function public.{STATS}(") < CODE.index("do $post$") < CODE.index("commit;")
