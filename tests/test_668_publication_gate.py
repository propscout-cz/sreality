"""Migrations 668 + 669: the publication rule (rule 16, operator ruling 2026-10-23). Offline, text only.

668 adds `properties.settled_at` and nothing else; 669 restates browse_projection from 665's body with ONE WHERE arm,
toolkit.publication.PUBLISHED_PREDICATE character for character, after the consumer rule. A migration cannot import
the constant, so this file is what keeps the two texts one rule. That the latest definition still carries it is
tests/test_browse_read_path_guardrail.py; what the view serves, executed, is tests/test_location_ad_readers_live.py.
"""

from __future__ import annotations

import re

from tests.migration_defs import MIGRATIONS
from toolkit.publication import CAP_SQL, PUBLISHED_PREDICATE, published_property_predicate

S665 = (MIGRATIONS / "665_browse_street_sort.sql").read_text(encoding="utf-8")
S668 = (MIGRATIONS / "668_publication_settled_at.sql").read_text(encoding="utf-8")
S669 = (MIGRATIONS / "669_publication_gate_view.sql").read_text(encoding="utf-8")
CONSUMER_RULE = "(ll.geom IS NOT NULL OR ll.country_status = 'foreign')"
# Comments and string literals together, a literal first: 668's column comment holds a `--`.
_NOISE = re.compile(r"'(?:[^']|'')*'|--[^\n]*")


def _code(sql: str) -> str:
    return _NOISE.sub(lambda m: "''" if m.group(0).startswith("'") else "", sql)


def _statements(sql: str) -> list[str]:
    return [" ".join(s.split()).lower() for s in _code(sql).split(";") if s.strip()]


def _view(sql: str) -> str:
    """browse_projection's CREATE, comments dropped, whitespace folded, up to its `;`."""
    code = re.sub(r"--[^\n]*", "", sql)
    at = code.index("create or replace view browse_projection as")
    return " ".join(code[at: code.index(";", at)].split())


def _block(sql: str, tag: str) -> str:
    at = sql.index(f"do ${tag}$")
    return sql[at: sql.index(f"${tag}$;", at)]


def _header(sql: str) -> str:
    lines = []
    for line in sql.splitlines():
        if not line.startswith("--"):
            break
        lines.append(line.lstrip("-").strip())
    return " ".join(" ".join(lines).split())


def test_the_rule_has_exactly_one_shape() -> None:
    """A change to the constant is a change to every surface, so the shape itself is pinned: the stamp, else the
    first sighting plus the one cap, against now() — no flag, no setting, no second clock."""
    assert CAP_SQL == "interval '6 hours'"
    assert PUBLISHED_PREDICATE == "coalesce(p.settled_at, p.first_seen_at + interval '6 hours') <= now()"
    assert published_property_predicate("l.property_id") == (
        "EXISTS (SELECT 1 FROM properties pp WHERE pp.id = l.property_id"
        " AND coalesce(pp.settled_at, pp.first_seen_at + interval '6 hours') <= now())")


def test_668_adds_the_column_and_nothing_else() -> None:
    """One nullable column with no default: NULL is "not settled before the cap", and the stamper is its only writer."""
    stmts = _statements(S668)
    assert stmts[:3] == ["set lock_timeout = ''", "set statement_timeout = ''",
                         "alter table public.properties add column if not exists settled_at timestamptz"]
    assert len(stmts) == 4 and stmts[3].startswith("comment on column public.properties.settled_at is ''")


def test_669_is_665s_body_plus_the_published_arm() -> None:
    """WHERE-only: no column moves, so browse_list's positional `select *` sync stays aligned. RED by: restating
    the view from an older body, or touching anything but the WHERE."""
    body = _view(S669)
    assert body.replace(f" and {PUBLISHED_PREDICATE}", "", 1) == _view(S665)


def test_669_carries_the_constant_verbatim_after_the_consumer_rule() -> None:
    """THE PIN. pg_get_viewdef re-prints a parsed tree, so 669's own $post$ can only see that `settled_at` is
    named; only this comparison against the importable constant sees the text."""
    assert _view(S669).endswith(f"and {CONSUMER_RULE} and {PUBLISHED_PREDICATE}"), (
        "669's browse_projection does not end its WHERE with the consumer rule, then "
        "toolkit.publication.PUBLISHED_PREDICATE: the two texts are one rule and must be the same characters")


def test_669_refuses_any_body_but_665s_and_any_schema_without_668() -> None:
    """Before any lock: a body that is neither 665's (md5 of its pg_get_viewdef, as 665 records it for its own
    re-run) nor already gated is refused, and so is a schema without the column the arm names."""
    pre665, pre669 = _block(S665, "pre"), _block(S669, "pre")
    own665 = set(re.findall(r"'([0-9a-f]{32})'", pre665)) - set(re.findall(r"\b[0-9a-f]{32}\b", _header(S665)))
    assert len(own665) == 1 and own665 <= set(re.findall(r"'([0-9a-f]{32})'", pre669))
    assert "column_name = 'settled_at'" in pre669 and "(apply 668 first)" in pre669
    assert S669.index("do $pre$") < S669.index("\nbegin;")


def test_669_changes_the_gated_view_and_nothing_else() -> None:
    """No pg_proc body in either file (migration 656's $pre$ reads every function's source; 668's statements are
    pinned above) and no other relation: the detail-by-id surfaces (properties_public) stay ungated, as rule 25
    keeps them for the location rule."""
    ddl = [s for s in _statements(S669)
           if re.match(r"(?:create|alter|drop|grant|revoke|comment|insert|update|delete|truncate)\b", s)]
    assert len(ddl) == 1 and ddl[0].startswith("create or replace view browse_projection as select"), ddl


def test_the_headers_state_the_apply_order() -> None:
    """668 is additive and lands first; 669 lands only after the merge and the worker's rollout, so the old
    publishing check never reads the gated view."""
    assert "apply BEFORE the merge through apply_migration.yml" in _header(S668)
    h669 = _header(S669)
    assert "APPLY ORDER -- the REVERSE of the usual additive order" in h669
    assert "merge the PR, confirm Railway's realtime-worker rollout" in h669
    assert "THEN apply this file through apply_migration.yml (--ref main), never the MCP" in h669
