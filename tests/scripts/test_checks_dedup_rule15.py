"""Rule 15 for the Engine check: it reads the live engine's output, never the removed engine's."""

# Two arms, because each sees what the other cannot. The static arm reads every string literal of
# scripts/checks/dedup.py (docstrings left out, f-string parts in): no 'auto' source, no spelled
# generation, no frozen relation of migration 475, no applied_merges. The dynamic arm runs the
# check over worlds that send every statement it has and reads each text and its parameters,
# imported engine statements included: every statement that reads property_merge_events carries a
# positive source filter bound to the engine's and the operator's sources only. The one ledger
# read without that filter is _MERGES_SQL's id floor, inside a statement that carries it: it fixes
# an id boundary and reads no row's content. Only execution proves the predicate selects nothing
# else: the live SQL rail seeds a 'auto' merge beside them.

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any

from autodedup.apply import MERGE_SOURCE
from scripts.checks import dedup as dd
from tests.scripts.test_checks_dedup import _building, _check, _reopened
from tests.scripts.test_checks_rule21 import _literals

_MODULE = Path(inspect.getsourcefile(dd))
_AUTO = re.compile(r"'auto'", re.IGNORECASE)
_SPELLED_GENERATION = re.compile(r"generation\s*(=|<>|!=|in\s*\()\s*'", re.IGNORECASE)
_POSITIVE_SOURCE = re.compile(r"\.source\s*=\s*any\(%\(sources\)s::text\[\]\)")
# Migration 475's frozen relations of the removed engine, its two publish columns, and the
# engine-side ledgers the design binds unread (applied_merges, merges, operator_merges).
_FROZEN = re.compile(
    r"\b(dedup_pair_audit|dedup_decision_feedback|dedup_golden_\w*|dedup_vision_bakeoff_\w*|"
    r"dedup_model_compare_sets|listing_visual_matches|listing_site_plan_matches|"
    r"listing_floor_plan_matches|image_room_classifications|listing_image_comparisons|"
    r"dedup_funnel_resolutions_archive|published_at|publish_reason|applied_merges|"
    r"autodedup\.merges|operator_merges|legacy_retire)\b", re.IGNORECASE)


def _values(params: Any) -> list[Any]:
    """Every scalar a statement was bound with, lists and mappings flattened."""
    if isinstance(params, dict):
        return [v for value in params.values() for v in _values(value)]
    if isinstance(params, (list, tuple, set, frozenset)):
        return [v for value in params for v in _values(value)]
    return [params]


def _offences(sent: list[tuple[str, Any, bool]]) -> list[str]:
    """What breaks rule 15 in the statements a run sent, one line each."""
    out = []
    for sql, params, _ in sent:
        head = " ".join(sql.split())[:60]
        if _AUTO.search(sql) or "auto" in _values(params):
            out.append(f"selects the removed engine's source: {head}")
        if _SPELLED_GENERATION.search(sql):
            out.append(f"spells a generation: {head}")
        if "%(generation)s" in sql and params.get("generation") != dd.GENERATION:
            out.append(f"binds a generation other than the live one: {head}")
        if _FROZEN.search(sql):
            out.append(f"reads a relation rule 15 keeps unread: {head}")
        if "property_merge_events" in sql:
            sources = (params or {}).get("sources") if isinstance(params, dict) else None
            if not _POSITIVE_SOURCE.search(sql) or not sources or not set(sources) <= {
                    MERGE_SOURCE, "operator"}:
                out.append(f"reads the merge ledger without the positive source filter: {head}")
            if re.search(r"\b\w+\.(listing_id|generation)\b", sql.split("property_merge_events",
                                                                         1)[1]):
                out.append(f"reads the ledger's legacy listing_id or its generation: {head}")
    return out


def _runs() -> list[tuple[str, Any, bool]]:
    """Every statement of a merging world (decision lag judged), of one whose open lag reading is
    judged through its excuses, and of a building one."""
    return _check()[1].sent + _check(**_reopened())[1].sent + _check(**_building())[1].sent


def test_no_literal_of_the_module_selects_the_removed_engine() -> None:
    literals = _literals(_MODULE.read_text())
    assert len(literals) > 40, "the scan read nothing: the SQL lives in literals"
    offenders = [(line, text) for line, text in literals
                 if text.strip().lower() == "auto" or _AUTO.search(text)
                 or _SPELLED_GENERATION.search(text) or _FROZEN.search(text)
                 or text in (dd.GENERATION, "legacy") or re.fullmatch(r"g\d+", text)]
    assert offenders == []


def test_the_module_imports_nothing_of_the_removed_engines_retirement() -> None:
    tree = ast.parse(_MODULE.read_text())
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not {m for m in modules if "legacy" in (m or "")}
    assert not {name for name in names if "legacy" in name.lower() or name == "LEGACY_SOURCE"}


def test_every_statement_the_check_sends_reads_only_the_live_engine() -> None:
    sent = _runs()
    ledger = {sql for sql, _, _ in sent if "property_merge_events" in sql}
    assert ledger == {dd._MERGES_SQL, dd._RULINGS_SQL}
    assert _offences(sent) == []


def test_the_ledger_readers_bind_the_engines_and_the_operators_sources_only() -> None:
    sent = {sql: params for sql, params, _ in _runs()}
    assert sent[dd._MERGES_SQL]["sources"] == [MERGE_SOURCE, "operator"]
    assert sent[dd._RULINGS_SQL]["sources"] == [MERGE_SOURCE]


def test_the_rail_catches_a_negative_filter_a_blind_read_and_a_spelled_generation() -> None:
    """A rail that passes everything proves nothing: each shape it exists for is caught."""
    bad = [
        ("select count(*) from property_merge_events e where e.source <> 'auto'", {}, True),
        ("select count(*) from property_merge_events e where e.created_at > now()", {}, True),
        ("select count(*) from property_merge_events e where e.source = any(%(sources)s::text[])",
         {"sources": ["autodedup", "auto"]}, True),
        ("select 1 from property_merge_events e where e.source = any(%(sources)s::text[]) "
         "and e.listing_id = 7", {"sources": ["autodedup"]}, True),
        ("select 1 from autodedup.rt_fp f where f.generation = 'rt'", {}, True),
        ("select 1 from autodedup.rt_fp f where f.generation = %(generation)s::text",
         {"generation": "g12"}, True),
        ("select 1 from autodedup.applied_merges a", {}, True),
    ]
    assert len(_offences(bad)) >= len(bad)
    assert all(_offences([statement]) for statement in bad)
