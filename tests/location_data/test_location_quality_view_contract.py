"""W1: the location scorecard view (migrations 698, 733, 736) is the ONE definition of every location quality number.

Static over the migration text, like test_location_schema_contracts.py: no database, so it runs in every lane. A
migration cannot import Python, so each shared definition the view carries VERBATIM is pinned here against the module
that spells it (location_steps' flags, town-missing set and evidence probe; the publication rule; NEW_AD_H), and the
view's (metric, class) vocabulary against the gauge's closed tuple. A plan fact is a rail too: every CTE but the
scalars and the single-use feeders is `as materialized` (inlined, the text ran 34.8 s on production against 11.7 s).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from location_data import location_steps as ls
from location_data.claims_common import served_location_predicate
from location_data.resolver import resolve_db
from scripts.checks import quality as qc
from scripts.checks.common import NEW_AD_H
from tests.location_data.test_location_schema_contracts import _clean as _location_corpus
from tests.location_data.test_location_schema_contracts import _statements, _values_rows
from toolkit import publication

ROOT = Path(__file__).resolve().parents[2]
VIEW = "public.location_quality_by_source"
# The view's live text is its newest restatement (698 first, 733 adds the position metric): the rails read that one.
MIGRATION = sorted(p for p in (ROOT / "migrations").glob("*.sql")
                   if f"create or replace view {VIEW}" in p.read_text(encoding="utf-8"))[-1]
# W6-c's position audit (migration 733): the matview, its admin-gated list view and its refresh.
POSITION_MIGRATION = ROOT / "migrations" / "733_location_w6c_position_audit.sql"
POSITION_MV = "public.location_position_mv"
POSITION_VIEW = "public.location_position_audit_public"

# The CTEs left inline, each with why: the three scalar lookups the planner runs once as InitPlans, and the two feeders
# read exactly once by a materialized CTE (the shape measured at 11.7 s).
INLINE = {
    "obec_rank": "a scalar: the obec rank",
    "street_rank": "a scalar: the street rank",
    "cur": "a scalar: the current registry version",
    "degenerate_ku_obce": "a feeder read once, by `versions`",
    "cohort_rows": "a feeder read once, by `b`",
}
COHORTS = {"listing", "served", "queue"}
# The view's four grain classes as its `agg` FILTERs spell them after `b.has_town and b.country_status = 'cz' and`.
GRAIN_FILTERS = {
    "address": "b.is_address_grain",
    "street": "b.rank >= (select rank from street_rank) and not b.is_address_grain",
    "quarter": "b.rank > (select rank from obec_rank) and b.rank < (select rank from street_rank)",
    "obec": "b.rank = (select rank from obec_rank)",
}


def _squeeze(text: str) -> str:
    return " ".join(text.split()).lower()


def _code() -> str:
    """The migration with every `--` comment blanked: a match is code, never prose."""
    return re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8"))


def _balanced(text: str, open_at: int) -> str:
    depth = 0
    for i in range(open_at, len(text)):
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        if depth == 0:
            return text[open_at + 1:i]
    raise AssertionError("unbalanced parentheses")


def _view_body() -> str:
    code = _code()
    start = code.index(f"create or replace view {VIEW}")
    return code[start:code.index(";", start)]


def _ctes() -> dict[str, tuple[bool, str]]:
    """Each CTE of the view: whether it is `as materialized`, and its body."""
    body = _view_body()
    return {m.group(1): (bool(m.group(2)), _balanced(body, m.end() - 1))
            for m in re.finditer(r"^([a-z_]+) as (materialized )?\(", body, re.M)}


def _branches() -> list[str]:
    """The final SELECT's UNION ALL branches, split at depth zero."""
    body = _view_body()
    last = list(re.finditer(r"^([a-z_]+) as (materialized )?\(", body, re.M))[-1]
    tail = body[last.end() - 1 + len(_balanced(body, last.end() - 1)) + 2:]
    out, depth, start = [], 0, 0
    for m in re.finditer(r"\(|\)|\bunion all\b", tail):
        depth += {"(": 1, ")": -1}.get(m.group(0), 0)
        if m.group(0) == "union all" and depth == 0:
            out.append(tail[start:m.start()].strip())
            start = m.end()
    return [*out, tail[start:].strip()]


def test_the_view_is_service_role_only_and_runs_as_its_caller() -> None:
    """The browser never reads it (the page reads stored cells): default privileges hand `authenticated` SELECT on a
    new relation (migration 299), so the revoke is explicit, and nothing grants it back."""
    code = _squeeze(_code())
    assert code.startswith("set lock_timeout = '5s';")
    assert f"create or replace view {VIEW} with (security_invoker = true) as" in code
    assert f"revoke all on {VIEW} from public, anon, authenticated;" in code
    assert f"grant select on {VIEW} to service_role;" in code
    grants = re.findall(rf"grant [a-z, ]+ on {re.escape(VIEW)} to ([a-z_, ]+);", code)
    assert grants == ["service_role"], grants


def test_every_cte_but_the_scalars_and_the_single_use_feeders_is_materialized() -> None:
    ctes = _ctes()
    assert {name for name, (materialized, _) in ctes.items() if not materialized} == set(INLINE)
    body = _view_body()
    for name in ("degenerate_ku_obce", "cohort_rows"):
        assert len(re.findall(rf"\b{name}\b", body)) == 2, f"{name} is read more than once: materialize it"
    assert {"b", "agg", "townless", "outside", "versions", "stock"} <= set(ctes)


def test_every_union_branch_carries_a_literal_cohort() -> None:
    branches = _branches()
    cohorts = [re.match(r"select '(\w+)'::text\b", b) for b in branches]
    assert all(cohorts), [b[:60] for b, m in zip(branches, cohorts) if not m]
    assert {m.group(1) for m in cohorts if m} == COHORTS


def test_the_columns_are_the_ones_the_gauge_reads() -> None:
    first = _branches()[0]
    select = first[len("select "):first.index(" from ")]
    names = [re.split(r"\bas\b|\.", part)[-1].strip() for part in select.split(",")]
    assert ", ".join(names) == qc._LOCATION_COLUMNS == "cohort, source, metric, class, n, of_n, q50, q90, q95, q100"


def test_no_grain_is_compared_by_enum_order() -> None:
    """Ordering by the enum is forbidden in persisted SQL (location_steps): grains go through the rank table."""
    code = _code()
    assert not re.search(r"granularity\s*(?:<=|>=|<(?![>=])|>(?!=))", code)
    assert not re.search(r"(?:<=|>=|<(?![>=])|>(?!=))\s*'(?:obec|street|address_point|building)'", code)
    assert "location_granularity_rank" in _ctes()["obec_rank"][1]


def test_the_listing_cohort_starts_where_publishing_ends() -> None:
    """NEW_AD_H is the one edge between publishing's new ads and this scorecard; the only other interval is the
    publication cap."""
    code = _squeeze(_view_body())
    edge = f"l.is_active and l.first_seen_at < now() - interval '{NEW_AD_H} hours'"
    assert code.count(_squeeze(edge)) == 1, "the listing cohort"
    hours = set(re.findall(r"interval '(\d+) hours'", code))
    assert hours == {str(NEW_AD_H), re.search(r"\d+", publication.CAP_SQL).group(0)}


def test_the_served_cohort_is_published_properties_at_their_location_ad() -> None:
    cohort = _squeeze(_ctes()["cohort_rows"][1])
    assert _squeeze(publication.published_predicate("p")) in cohort
    assert "join listings l on l.id = p.location_listing_ref_id" in cohort
    assert "p.status = 'active' and p.is_active" in cohort
    assert "repr_listing_ref_id" not in _code(), "a property stands at its location ad, never its canonical ad"


def test_the_flags_are_location_steps_rendered_verbatim() -> None:
    """`b` carries step_flags_sql(listings='c') and town_missing_sql() character for character."""
    b = _squeeze(_ctes()["b"][1])
    flags = ls.step_flags_sql(listings="c")
    assert flags["located"] == served_location_predicate("c.id")
    named = {"has_verdict": "has_row", "located": "located", "is_foreign": "is_foreign", "has_town": "has_town"}
    for flag, column in named.items():
        assert _squeeze(f"{flags[flag]} as {column}") in b, flag
    assert _squeeze(f"({ls.town_missing_sql()}) as town_missing") in b
    assert "left join listing_location ll on ll.listing_id = c.id" in b


def test_the_town_split_is_the_evidence_probe_over_the_missing_set() -> None:
    """Ruling Q6: unresolved = a town-bearing claim (town_evidence_sql), withheld = none; over rule 25's set only."""
    townless = _squeeze(_ctes()["townless"][1])
    assert _squeeze(f"select {ls.town_evidence_sql('b.id', 'ec')} as found") in townless
    assert "where b.town_missing" in townless
    listed = re.search(r"ec\.claim_type in \(([^)]*)\)", townless).group(1)
    assert tuple(t.strip(" '") for t in listed.split(",")) == ls.TOWN_EVIDENCE_CLAIM_TYPES
    assert "street_name" not in ls.TOWN_EVIDENCE_CLAIM_TYPES, "a street binds only inside an anchoring town"


def test_outside_is_strict_containment_over_the_placed_rows_check_flagged() -> None:
    """Ruling Q3: the position against the bound town's authoritative (pip) polygon, no tolerance, over exactly the rows
    `placed_n` counts, so the cell's count is a subset of its denominator."""
    outside = _squeeze(_ctes()["outside"][1])
    assert "b.disputed in ('pin_outside_obec', 'pin_outside_cz', 'country_conflict')" in outside
    assert "b.has_town and b.geom is not null" in outside
    assert "g.purpose = 'pip'" in outside and "st_covers(g.geom, b.geom)" in outside
    assert "st_dwithin" not in outside and "st_distance" not in outside
    assert "count(*) filter (where b.has_town and b.geom is not null) as placed_n" in _squeeze(_ctes()["agg"][1])


def test_outside_probes_with_the_one_containing_obec_statement() -> None:
    """The probe is resolve_db.CONTAINING_OBEC_SQL's FROM ... LIMIT 1 block, verbatim but for its version parameter and
    its point, so the resolver and this cell can never disagree about which town a point is in."""
    sql = resolve_db.CONTAINING_OBEC_SQL
    block = sql[sql.index("FROM ruian_admin_unit_geometries g"):sql.index("LIMIT 1") + len("LIMIT 1")]
    assert block.count("%s") == 1 and block.count(resolve_db._PT) == 1
    assert _squeeze("OFFSET 0) o ORDER BY o.code LIMIT 1") in _squeeze(block), "a border point replays"
    rendered = block.replace("%s", "(select id from cur)").replace(resolve_db._PT, "b.geom")
    assert _squeeze(rendered) in _squeeze(_ctes()["outside"][1])


def _check_disputed_classes() -> set[str]:
    """Every class CHECK can write: the string literals location_data passes as `disputed=` (a pass-through of a
    verdict's own `disputed` aside; anything else fails, so a computed class cannot hide from this rail)."""
    found: set[str] = set()
    other: list[str] = []
    for path in sorted((ROOT / "location_data").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.keyword) and node.arg == "disputed"):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                found.add(node.value.value)
            elif not (isinstance(node.value, ast.Attribute) and node.value.attr == "disputed"):
                other.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not other, f"a disputed value that is neither a literal nor a pass-through: {other}"
    return found


def test_every_disputed_class_check_writes_is_a_column_of_the_mix() -> None:
    """The mix is wide columns in `agg`: a class CHECK starts writing would be dropped silently, so it fails here
    until the view, the gauge's `_DISPUTED` and its vocabulary all name it."""
    classes = _check_disputed_classes()
    assert classes == set(qc._DISPUTED) == {c for m, c in qc.LOCATION_VIEW_VOCABULARY if m == "disputed"}
    agg = _squeeze(_ctes()["agg"][1])
    for cls in classes:
        assert f"count(*) filter (where b.disputed = '{cls}') as d_{cls}" in agg, cls
    flagged = re.search(r"b\.disputed in \(([^)]*)\)", _ctes()["outside"][1]).group(1)
    assert {c.strip(" '") for c in flagged.split(",")} <= classes


def _rank_seed() -> dict[str, tuple[int, bool]]:
    """location_granularity_rank as every migration seeds it: granularity -> (rank, is_address_grain)."""
    seed: dict[str, tuple[int, bool]] = {}
    for stmt in _statements(_location_corpus()):
        if re.match(r"\s*insert into location_granularity_rank\b", stmt):
            for row in _values_rows(stmt + ";", "location_granularity_rank"):
                seed[row[0].strip("'")] = (int(row[1]), row[2] == "true")
    return seed


def test_the_grain_classes_partition_every_town_grain_of_the_rank_table() -> None:
    """Grains are wide columns in `agg` too: every granularity the rank table holds at obec rank or finer falls in
    exactly ONE of the four classes, so a new rung is neither dropped nor counted twice."""
    agg = _squeeze(_ctes()["agg"][1])
    for grain, predicate in GRAIN_FILTERS.items():
        assert f"count(*) filter (where b.has_town and b.country_status = 'cz' and {predicate}) as g_{grain}" in agg
    assert tuple(GRAIN_FILTERS) == qc._GRAINS == tuple(c for m, c in qc.LOCATION_VIEW_VOCABULARY if m == "grain")
    seed = _rank_seed()
    obec, street = seed["obec"][0], seed["street"][0]
    classes = {"address": lambda rank, address: address,
               "street": lambda rank, address: rank >= street and not address,
               "quarter": lambda rank, address: obec < rank < street,
               "obec": lambda rank, address: rank == obec}
    towned = {g: v for g, v in seed.items() if v[0] >= obec}
    assert {"obec", "street", "address_point"} <= set(towned)
    for granularity, (rank, address) in towned.items():
        hits = [name for name, test in classes.items() if test(rank, address)]
        assert len(hits) == 1, f"{granularity} (rank {rank}) falls in {hits}"


def test_the_ku_invariant_keeps_its_one_excuse_at_the_current_registry() -> None:
    excuse = _squeeze(_ctes()["degenerate_ku_obce"][1])
    assert "d.discrepancy = 'degenerate_boundary_geometry'" in excuse
    assert "d.registry_version_id = (select id from cur)" in excuse
    versions = _squeeze(_ctes()["versions"][1])
    assert ("b.katastr_kod is null and b.obec_kod is not null and b.obec_kod not in "
            "(select code from degenerate_ku_obce)") in versions


def test_postgis_stays_unqualified() -> None:
    """Production keeps PostGIS in `extensions`, CI replays it in `public`: a qualified call breaks one of them."""
    code = _code().lower()
    assert not re.search(r"\b(?:extensions|public)\.st_", code)


def _vocabulary() -> set[tuple[str, str]]:
    """(metric, class) as the view emits them: the stock VALUES pairs, then each branch's literals ('*' when open)."""
    pairs = set(re.findall(r"\('(\w+)',\s*'(\w+)',", _ctes()["stock"][1]))
    for branch in map(_squeeze, _branches()):
        m = re.match(r"select '\w+'::text(?: as cohort)?, [^,]+, (s\.metric|'(\w+)'), ", branch)
        assert m, branch[:80]
        if m.group(1) == "s.metric":
            continue
        rest = branch[m.end():]
        if rest.startswith("case"):
            classes = re.findall(r"(?:then|else) '(\w+)'", rest[:rest.index(" end")])
        else:
            literal = re.match(r"'(\w+)'", rest)
            classes = [literal.group(1) if literal else "*"]
        pairs |= {(m.group(2), cls) for cls in classes}
    return pairs


def test_the_metric_vocabulary_is_the_gauges_closed_tuple() -> None:
    assert _vocabulary() == set(qc.LOCATION_VIEW_VOCABULARY)
    assert len(set(qc.LOCATION_VIEW_VOCABULARY)) == len(qc.LOCATION_VIEW_VOCABULARY)


def test_the_location_quality_page_renders_the_registered_check() -> None:
    """W1 (ruling Q7): the page renders this gauge's STORED row through Health's own query, so
    its key is the registry's and the registry's owner link is the page; its two bars read an
    arm's facts by name (`at[source]?.street?.facts.grains`), so each arm must be one the gauge
    emits and each fact one that arm writes. RED by: renaming a key, an arm or a fact on one
    side (the bars would vanish silently behind their guard), or the page growing a read of its
    own again."""
    from scripts.verify_pipeline import CHECKS, THRESHOLDS

    page = (ROOT / "frontend" / "src" / "pages" / "LocationQuality.tsx").read_text(encoding="utf-8")
    key = re.search(r"const CHECK_KEY = '(\w+)'", page)
    assert key, "the page names no check key"
    assert next(c for c in CHECKS if c.key == key.group(1)).owner_path == "/location-quality"
    assert "...HEALTH_QUERY" in page and "/location/quality" not in page

    bars = re.findall(r"at\[source\]\?\.(\w+)\?\.facts\.(\w+)", page)
    assert {arm for arm, _ in bars} == {"town", "outside"}, bars
    assert ("town", "positions") in bars, "W6-c: the position grid reads the town cell's stored positions"
    card = {("town", "ads"): (100, 0), ("outside", "position"): (1, 90),
            ("grain", "address"): (60, 90), ("grain", "street"): (10, 0),
            ("position", "door/address"): (60, 100)}
    cells = {c["subject"]: c for c in qc._portal_cells("bazos", card, THRESHOLDS)}
    for arm, fact in bars:
        assert arm in qc.LOCATION_PORTAL_CELLS, f"the gauge emits no {arm!r} arm"
        assert fact in cells[f"bazos/{arm}"]["facts"], f"bazos/{arm} writes no {fact!r} fact"


# --- W6-c: the position metric and the position audit (migration 733) ----------------------------------------------


def _position_code() -> str:
    return re.sub(r"--[^\n]*", "", POSITION_MIGRATION.read_text(encoding="utf-8"))


def _statement(code: str, head: str) -> str:
    start = code.index(head)
    return code[start:code.index(";", start)]


def test_the_position_metric_is_the_matview_kind_by_the_live_grain_over_the_listing_cohort() -> None:
    """Design § 2: the kind is the hourly matview's while the row's answer is the one it classified (`resolved_at`
    unchanged), else `unclassified`; the grain is the row's LIVE grain, so the grid's columns are the grain counts."""
    ctes = _ctes()
    assert "positions" in ctes and ctes["positions"][0], "positions is a materialized CTE"
    positions = _squeeze(ctes["positions"][1])
    assert "left join location_position_mv m on m.listing_id = b.id" in positions
    assert "where b.cohort = 'listing'" in positions
    assert _squeeze(ls.position_reading_kind_sql("b")) in positions
    assert _squeeze(ls.position_reading_kind_sql("b")) == ("coalesce(case when m.resolved_at is not distinct from "
                                                          "b.resolved_at then m.position_kind end, 'unclassified')")
    assert _squeeze(ls.position_grain_sql("b")) in positions
    b = _squeeze(ctes["b"][1])
    assert "ll.granularity" in b and "ll.resolved_at" in b
    branch = next(_squeeze(x) for x in _branches() if "from positions" in x)
    assert branch.startswith("select 'listing'::text, p.source, 'position', p.class, p.n, p.source_n,")


def test_the_position_grain_opens_with_the_scorecards_four_classes_verbatim() -> None:
    """The grid's address/street/quarter/obec columns ARE the `grain` counts: each WHEN is the `agg` FILTER."""
    grain = _squeeze(ls.position_grain_sql("b"))
    whens = re.findall(r"when (.+?) then '(\w+)'", grain)
    town = "b.has_town and b.country_status = 'cz' and "
    assert [(w, cls) for w, cls in whens[:4]] == [(town + p, g) for g, p in GRAIN_FILTERS.items()]
    assert tuple(cls for _, cls in whens) + ("unknown",) == tuple(
        g for g in ls.POSITION_GRAINS if g not in ("okres", "kraj", "country"))
    seed = _rank_seed()
    below_town = {g for g, (rank, _) in seed.items() if rank < seed["obec"][0]}
    assert below_town == {"okres", "kraj", "country", "unknown"} <= set(ls.POSITION_GRAINS)


def test_the_view_vocabulary_names_the_open_position_class() -> None:
    assert ("position", "*") in qc.LOCATION_VIEW_VOCABULARY
    assert ("position", "*") in _vocabulary()


def test_the_position_matview_renders_the_shared_sql_verbatim() -> None:
    """The matview's kind is location_steps.position_kind_sql(), its grain position_grain_sql(), its cohort the
    consumer rule negated (any status) beside every active ad, and the evidence probe the scorecard's."""
    mv = _squeeze(_statement(_position_code(), f"create materialized view if not exists {POSITION_MV}"))
    assert _squeeze(f"{ls.position_kind_sql('p')} as position_kind") in mv
    assert _squeeze(f"{ls.position_grain_sql('b')} as grain") in mv
    assert _squeeze(f"not {served_location_predicate('l.id')}") in mv
    assert "where l.is_active" in mv and "where not l.is_active" in mv
    assert _squeeze(f"case when b.position_kind = 'none' then {ls.town_evidence_sql('b.listing_id', 'ec')} end "
                    "as town_evidence") in mv
    assert "g.purpose = 'authoritative'" in mv and "g.registry_version_id = (select id from cur)" in mv
    assert "st_buffer" not in mv and "radius" not in mv, "no radius anywhere (ruling 2026-11-01)"
    assert "location_claims_geom" not in mv


def test_the_position_kinds_close_over_the_sql_and_each_maps_to_a_resolver_origin() -> None:
    sql = _squeeze(ls.position_kind_sql("p"))
    kinds = re.findall(r"then '(\w+)'", sql)
    assert len(kinds) == len(set(kinds)) and {*kinds, "unexplained"} == set(ls.POSITION_KINDS)
    assert sql.endswith("else 'unexplained' end")
    assert set(ls.POSITION_ORIGIN.values()) == {"registry_point", "street_point", "portal_pin", "admin_centroid",
                                                "none"}
    # The pin is fetched by its key behind OFFSET 0 and compared outside (design § 2, the plan trap).
    assert "offset 0) k)" in _squeeze(ls.position_kind_sql("p"))


def test_the_position_audit_is_admin_gated_and_the_matview_dark_to_the_browser() -> None:
    code = _squeeze(_position_code())
    assert code.startswith("set lock_timeout = '5s'; set statement_timeout = ")
    assert f"revoke all on {POSITION_MV} from public, anon, authenticated;" in code
    assert re.findall(rf"grant [a-z, ]+ on {re.escape(POSITION_MV)} to ([a-z_, ]+);", code) == ["service_role"]
    view = _squeeze(_statement(_position_code(), f"create or replace view {POSITION_VIEW}"))
    assert view.endswith(") __admin_gate where is_platform_admin()")
    assert f"revoke all on {POSITION_VIEW} from public, anon, authenticated;" in code
    assert re.findall(rf"grant [a-z, ]+ on {re.escape(POSITION_VIEW)} to ([a-z_, ]+);", code) == ["authenticated"]
    assert "location_display_label(" in view
    assert not re.search(r"create (?:or replace )?view public\.location_position_audit\b(?!_public)", code), (
        "every admin-gated browser view ends _public")


def test_a_cells_list_is_the_scorecards_reading_rule_over_live_rows() -> None:
    """Decision A (2026-11-06): the list opened from a grid cell applies the scorecard's own cohort (its listing edge,
    verbatim), kind rule (the matview's kind while resolved_at is unchanged, else unclassified) and LIVE grain, so a
    cell's list differs from its number only by the churn since the reading. Rows come from live listings; the
    matview's own kind stays readable as `snapshot_kind` for the every-status no-answer entry."""
    view = _squeeze(_statement(_position_code(), f"create or replace view {POSITION_VIEW}"))
    edge = _squeeze(f"(l.is_active and l.first_seen_at < now() - interval '{NEW_AD_H} hours') as in_reading")
    assert edge in view
    assert _squeeze(f"{ls.position_reading_kind_sql('b')} as position_kind") in view
    assert _squeeze(f"{ls.position_grain_sql('b')} as grain") in view
    assert "m.position_kind as snapshot_kind" in view
    assert "from listings l left join listing_location ll on ll.listing_id = l.id" in view
    assert "left join location_position_mv m on m.listing_id = b.listing_id" in view
    assert "where b.in_reading or m.listing_id is not null" in view
    for scalar in ("obec_rank", "street_rank"):
        assert f"{scalar} as (select r.rank from location_granularity_rank r where r.granularity = " in view


def test_the_position_matview_is_registered_indexed_and_refreshed_off_the_old_minute() -> None:
    code = _squeeze(_position_code())
    assert f"create unique index if not exists location_position_mv_pk on {POSITION_MV} (listing_id);" in code
    assert (f"create index if not exists location_position_mv_cell on {POSITION_MV} "
            "(position_kind, source, grain, listing_id);") in code
    # The list sorts live listings (a cell's rows) or the ~12k no-answer rows: no keyset lane on the matview.
    assert "last_seen_at, listing_id" not in code and "first_seen_at, listing_id" not in code
    assert "values ('location_position_mv', 'refresh_matview', 'pg_cron'," in code
    cron = re.search(r"cron\.schedule\(\s*'refresh-location-position',\s*'([^']+)',\s*\$\$(.+?)\$\$", code)
    assert cron, "pg_cron refresh-location-position"
    minute = cron.group(1).split()[0]
    assert minute.isdigit() and minute != "25", "hourly, never at the old job's :25"
    assert cron.group(2).strip() == ("set statement_timeout='900s'; "
                                     "select public.refresh_matview('location_position_mv');")
    assert "refresh materialized view" not in code
