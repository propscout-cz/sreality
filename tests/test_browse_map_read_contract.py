"""The map's cohort and the Stats cohort are built from ONE argument object (W6b).

W6b gives the Browse map a server-side RPC — `browse_map_cells`, migration 439 — that
takes the SAME named parameters as `browse_stats_properties`. The SPA builds that ~74-key
object ONCE (`buildBrowseStatsArgs` in frontend/src/lib/queries.ts) and both readers call
it, because two inline literals of 74 keys each is the single richest opportunity in this
codebase for the map and the Stats tab to describe different sets of properties with no
error anywhere.

Three drifts are possible and all three are silent. This file is the net for each.

  1. A key in the builder that the RPC has no parameter for. PostgREST answers a `.rpc()`
     with an unknown argument with `PGRST202` ("Could not find the function ... in the
     schema cache") — the WHOLE map read 404s, and reads fail silently app-wide (main.tsx
     wires onError on the MutationCache only, never the QueryCache). So the symptom of
     adding a Browse filter and forgetting the migration is an empty map, not an error.

  2. A parameter the map RPC carries and the Stats RPC does not, or vice versa. Divergence
     here is the count-vs-list class migration 351 was written to close.

  3. A prefilter id space the RPC does not carry. `applyPrefilters` emits `.in()` on two
     spaces, obec_id and property_id (the broker allowlist among the latter since W5,
     MS19), and the RPC read has no `.in()` to inherit.

The curated-set scope (migration 629) rides beside them as nine named arguments built
ONCE (`scopeArgs` in frontend/src/lib/setScope.ts) and spread into the builder AND the
list/pin source read (`readSource`), so all four cohort functions must take all nine.

Offline; runs in the normal `pytest -q` lane. Nothing here needs a database — the
behaviour of the shipped SQL is tests/test_browse_map_cells_live.py's subject.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUERIES_TS = REPO / "frontend" / "src" / "lib" / "queries.ts"
MIGRATIONS = REPO / "migrations"

# The one parameter browse_map_cells has on purpose and browse_stats_properties does not.
_MAP_ONLY_PARAMS = {"point_budget"}
# Its twin (migration 626): the chart spec the Stats pivot takes and the map does not. Like
# point_budget it shapes the ANSWER, never the cohort, and the builder does not send it
# (`fetchStatsPivot` spreads the builder's object and adds `spec` beside it).
_STATS_ONLY_PARAMS = {"spec"}


def _ts() -> str:
    return QUERIES_TS.read_text(encoding="utf-8")


def _balanced_block(src: str, open_at: int) -> str:
    """The substring from `open_at` (which must index a `{`) to its matching `}`."""
    assert src[open_at] == "{", src[open_at: open_at + 40]
    depth, i = 0, open_at
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at: i + 1]
        i += 1
    raise AssertionError("unbalanced braces in queries.ts")


def _strip_ts_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", src)


def _builder_keys() -> set[str]:
    """The snake_case keys `buildBrowseStatsArgs` puts on the wire, the prefilter
    allowlists it reads off `pre` included."""
    src = _ts()
    at = src.index("export const buildBrowseStatsArgs")
    block = _balanced_block(src, src.index("{", src.index("return {", at)))
    body = _strip_ts_comments(block)
    keys = set(re.findall(r"^\s*([a-z][a-z0-9_]*)\s*:", body, flags=re.MULTILINE))
    # The prefilter allowlists ride in from the ONE resolved object (`pre`), by name: a
    # caller cannot hand the builder a hand-mapped subset any more.
    for field in ("obecIds", "propertyIds"):
        assert re.search(rf"\bpre\.{field}\b", body), (
            f"buildBrowseStatsArgs no longer reads `pre.{field}` — that allowlist the "
            "caller resolves over the network has stopped reaching the RPC."
        )
    assert len(keys) > 60, f"only parsed {len(keys)} builder keys — the parse broke"
    # The curated-set scope arrives as a spread of the one scope builder (migration 629).
    assert re.search(r"\.\.\.scopeArgs\(f\)", body), (
        "buildBrowseStatsArgs no longer spreads scopeArgs(f) — the Stats and map cohorts "
        "would ignore the tag / collection / pipeline scope the list honours."
    )
    return keys | SCOPE_ARGS


SETSCOPE_TS = REPO / "frontend" / "src" / "lib" / "setScope.ts"
SCOPE_ARGS = {f"{s}_{side}" for s in ("tags", "collections", "pipeline")
              for side in ("set", "in", "out")}


def _rpc_call_block(fn: str) -> str:
    """The object literal passed to `supabase.rpc('<fn>', {...})`, comments stripped."""
    src = _ts()
    at = src.index(f"supabase.rpc('{fn}'")
    return _strip_ts_comments(_balanced_block(src, src.index("{", at)))


def _latest_migration_defining(func: str) -> Path:
    pat = re.compile(rf"create or replace function (?:public\.)?{func}\s*\(", re.IGNORECASE)
    hits = [p for p in MIGRATIONS.glob("*.sql") if pat.search(p.read_text(encoding="utf-8"))]
    assert hits, f"no migration defines {func}"
    return max(hits, key=lambda p: int(p.name.split("_", 1)[0]))


def _sql_params(func: str, minimum: int = 60) -> set[str]:
    """Parameter names of the LATEST definition of `func`, read from the migration."""
    sql = _latest_migration_defining(func).read_text(encoding="utf-8")
    at = re.search(rf"create or replace function (?:public\.)?{func}\s*\(",
                   sql, re.IGNORECASE).end()
    depth, i = 1, at
    while depth:
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
        i += 1
    args = re.sub(r"--[^\n]*", "", sql[at: i - 1])
    names = set(re.findall(r"(?:^|,)\s*([a-z][a-z0-9_]*)\s+[a-z]", args))
    assert len(names) > minimum, f"only parsed {len(names)} params for {func} — the parse broke"
    return names


def test_every_builder_key_is_a_parameter_of_both_rpcs() -> None:
    """One builder, two RPCs, zero unknown arguments.

    RED by: adding a filter to `buildBrowseStatsArgs` and shipping it without adding the
    parameter to migration 439's browse_map_cells (or to browse_stats_properties). The
    live symptom is a PGRST202 on the whole read — an EMPTY MAP, because the SPA swallows
    query errors — which is exactly the class of failure that reaches production green.
    """
    keys = _builder_keys()
    for func in ("browse_stats_properties", "browse_map_cells"):
        missing = sorted(keys - _sql_params(func))
        assert not missing, (
            f"buildBrowseStatsArgs sends {missing} but public.{func} has no such "
            f"parameter ({_latest_migration_defining(func).name} is its latest definition)."
        )


def test_the_scope_arguments_reach_every_cohort_function() -> None:
    """The nine curated-set arguments are the same names on the wire and in all four
    cohort functions; the list/pin sources also take `hide_dismissed`."""
    # RED by: renaming a set or a side in lib/setScope.ts without a migration (a PGRST202 on
    # every Browse read), or dropping the spread from readSource (the list ignores a scope
    # the map and Stats honour).
    ts = SETSCOPE_TS.read_text(encoding="utf-8")
    for side in ("set", "in", "out"):
        assert f"`${{name}}_{side}`" in ts, f"scopeArgs no longer builds `<set>_{side}`"
    sets = _balanced_block(ts, ts.index("{", ts.index("export const CURATED_SETS")))
    assert set(re.findall(r"^  ([a-z]+): \{", sets, flags=re.MULTILINE)) == {
        "tags", "collections", "pipeline"}
    for func in ("browse_stats_properties", "browse_map_cells"):
        assert SCOPE_ARGS <= _sql_params(func), func
    for func in ("browse_list_visible", "properties_map_visible"):
        assert SCOPE_ARGS | {"hide_dismissed"} <= _sql_params(func, minimum=9), func
    src = _ts()
    read = _strip_ts_comments(src[src.index("const readSource"): src.index("const listSource")])
    assert "hide_dismissed: !f.showDismissed, ...scopeArgs(f)" in read, (
        "readSource no longer hands the source function the dismissed flag and the scope"
    )


def test_the_two_rpcs_take_the_same_cohort_parameters() -> None:
    """browse_map_cells - point_budget = browse_stats_properties - spec.

    RED by: adding a cohort parameter to one RPC and not the other. The map and the Stats
    tab would then answer for different cohorts under the same filters, and the only
    symptom is two numbers that disagree.
    """
    stats = _sql_params("browse_stats_properties")
    cells = _sql_params("browse_map_cells")
    assert not _MAP_ONLY_PARAMS - cells, (
        f"browse_map_cells lost {sorted(_MAP_ONLY_PARAMS - cells)}, which only the map takes."
    )
    assert not _STATS_ONLY_PARAMS - stats, (
        f"browse_stats_properties lost {sorted(_STATS_ONLY_PARAMS - stats)}, which only the "
        "Stats pivot takes."
    )
    assert not cells - stats - _MAP_ONLY_PARAMS, (
        f"browse_map_cells takes {sorted(cells - stats - _MAP_ONLY_PARAMS)} and "
        "browse_stats_properties does not — the map cohort can now be narrowed in a way "
        "the Stats cohort cannot."
    )
    assert not stats - cells - _STATS_ONLY_PARAMS, (
        f"browse_stats_properties takes {sorted(stats - cells - _STATS_ONLY_PARAMS)} and "
        "browse_map_cells does not — the Stats cohort can now be narrowed in a way the map "
        "cohort cannot."
    )


def test_the_builder_sends_no_answer_shaping_parameter() -> None:
    """`spec` and `point_budget` are each one reader's own; the shared builder carries neither,
    or the other RPC would answer PGRST202 to every call."""
    assert not _builder_keys() & (_MAP_ONLY_PARAMS | _STATS_ONLY_PARAMS)


def test_the_map_rpc_carries_both_prefilter_id_spaces() -> None:
    """Whatever applyPrefilters filters on, the map RPC must receive.

    `applyPrefilters` is the ONE place the cohort reads apply their id allowlists. The
    RPC read has no `.in()` to inherit, so each space has to be handed over by name.

    RED by: adding an `.in()` to applyPrefilters without a matching argument, or by
    sending the retired `listing_ids_filter` again (migration 597 dropped the
    parameter, so a bundle that names it fails every map read).
    """
    src = _ts()
    at = src.index("export const applyPrefilters")
    body = _strip_ts_comments(_balanced_block(src, src.index("{", at)))
    pairs = re.findall(r"\.in\('([a-z_]+)',\s*p\.([A-Za-z]+)\)", body)
    assert {column for column, _ in pairs} == {"obec_id", "property_id"}, (
        f"applyPrefilters' id spaces changed: {pairs}"
    )

    # The map call hands the builder the whole resolved object; the builder is where
    # each allowlist is named (asserted by _builder_keys), so the call need only pass it.
    call = _rpc_call_block("browse_map_cells")
    assert "buildBrowseStatsArgs(f, pre)" in call, (
        "the browse_map_cells call no longer passes the resolved prefilters to the one "
        "cohort builder — the map cohort would lose every id allowlist"
    )
    builder = _strip_ts_comments(_balanced_block(
        _ts(), _ts().index("{", _ts().index("return {", _ts().index("export const buildBrowseStatsArgs")))))
    for column, field in pairs:
        assert re.search(rf"pre\.{field}\b", builder), (
            f"applyPrefilters filters on `{column}` via `p.{field}`, but buildBrowseStatsArgs "
            f"never reads `pre.{field}` — that allowlist is silently dropped from the RPC cohorts"
        )


def test_the_map_lane_is_chosen_from_the_filters_alone() -> None:
    """The points-vs-cells lane must be a function of the query KEY, nothing else.

    The map query is keyed `['map', filters, pinLimit]` (the pin limit is the server's
    point budget, passed in as an argument). Anything outside that key that can change
    the SHAPE of the answer between renders lets react-query serve a CLUSTER payload out
    of a cache entry warmed by a POINTS payload — ListingMap is then handed `cells` for
    `rows` or the reverse, with no refetch and no error. That is exactly why `?map=legacy`
    was a module-load constant while it existed; W3 S4 deleted the hatch, and this is the
    invariant it was obeying, kept without it.

    RED by: reading `window.location` / `localStorage` inside fetchListingsForMap to
    resurrect a runtime lane switch.
    """
    fetcher_at = _ts().index("export const fetchListingsForMap")
    fetcher = _strip_ts_comments(_ts()[fetcher_at: fetcher_at + 4000])
    for forbidden in ("window.location", "localStorage", "URLSearchParams"):
        assert forbidden not in fetcher, (
            f"fetchListingsForMap reads {forbidden} directly — the lane choice must come "
            "from the filters, or the react-query cache can mix shapes."
        )
