"""Migration 671's predicate IS the plot rule, literally (the 573 pattern).

671 NULLs the stored `estate_area` values `scraper.area.stated_plot` declines, and its header
asserts that each arm is exactly that rule. The CI schema replay runs the file only on an
empty schema, so nothing else would notice a later change to the category sets, the
labelled bases or the text-evidence pattern letting the file NULL plots the parser still
keeps. The pattern is ONE text both engines read: the file carries `scraper.area`'s own
constant verbatim (asserted below), Python's `re` evaluates the file's literal here, and
Postgres evaluates it in the live half over seeded hits and misses
(tests/test_migration_671_live.py).
"""

from __future__ import annotations

import itertools
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from scraper import area

_SQL = (Path(__file__).resolve().parent.parent / "migrations"
        / "671_plot_column_echo_heal.sql").read_text(encoding="utf-8")
_BODY = _SQL[_SQL.index("set lock_timeout"):]
_HEADER = _SQL[:_SQL.index("set lock_timeout")]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _header_text() -> str:
    return _squash(" ".join(line.lstrip("-") for line in _HEADER.splitlines()))


def _hit_predicates() -> list[str]:
    return [_squash(m) for m in re.findall(
        r"from public\.listings l\s+where (.*?)\s+for update of l skip locked", _BODY, re.DOTALL)]


def _in_list(listed: str) -> frozenset[str]:
    return frozenset(c.strip().strip("'") for c in listed.split(","))


def test_the_header_says_destructive_and_when_it_may_be_applied() -> None:
    header = _header_text()
    assert "DESTRUCTIVE" in header
    assert "APPLY ONLY AFTER the plot rule" in header
    assert "MERGED AND DEPLOYED" in header and "operator's explicit OK" in header
    assert "backup_before_drop.yml" in header


def test_the_heal_is_applied_from_main_after_the_rule_deploys() -> None:
    """One PR carries the rule and the heal (operator's call, 2026-10-25); apply_migration.yml is
    dispatched from main after the rule is deployed, never from a side branch."""
    header = _header_text()
    assert "apply_migration.yml --ref main" in header
    assert "g3-plot-echo" not in header
    assert "applied from `main` once the rule is deployed" in header
    assert "scraper.field_census --bless" in header


def test_the_file_has_one_hit_predicate_for_the_one_column() -> None:
    assert len(_hit_predicates()) == 1
    assert "set estate_area = null" in _BODY
    # area_basis belongs to area_m2, which is correct on every hit: never touched here.
    assert "area_basis = null" not in _BODY
    assert "listing_snapshots" not in _BODY and "last_seen_at" not in _BODY


def test_the_post_condition_counts_with_the_same_predicate() -> None:
    post = _squash(_BODY[_BODY.index("do $post$"):])
    assert _hit_predicates()[0] + ";" in post


def test_the_category_literals_are_the_rules_sets() -> None:
    predicate = _hit_predicates()[0]
    flat = re.search(r"l\.category_main = '(\w+)' or", predicate)
    echo = re.search(r"\(l\.category_main = '(\w+)' and \(l\.estate_area = l\.usable_area",
                     predicate)
    assert flat and frozenset({flat[1]}) == area.PLOT_FREE_CATEGORIES
    assert echo and frozenset({echo[1]}) == area.PLOT_ECHO_CATEGORIES
    bases = re.search(r"l\.area_basis in \(([^)]*)\)", predicate)
    assert bases and _in_list(bases[1]) == area.PLOT_ECHO_BASES
    assert area.PLOT_ECHO_BASES < area.AREA_BASES
    assert "unknown" not in area.PLOT_ECHO_BASES and "plot" not in area.PLOT_ECHO_BASES


def _literal_after(predicate: str, opener: str) -> str:
    """The SQL string literal(s) right after `opener`, adjacent pieces joined (the file
    wraps the long pattern over lines; Postgres concatenates them)."""
    rest = predicate[predicate.index(opener) + len(opener):]
    pieces = re.match(r"((?:'[^']*' ?)+)", rest)
    assert pieces, opener
    return "".join(re.findall(r"'([^']*)'", pieces[1]))


_NBSP = chr(0x00A0)
_TEXTS: tuple[str | None, ...] = (
    None,
    "Pozemek o výměře 852 m².",                                  # (a) for 852
    "Zastavěná plocha 10 m².",                                  # (b) for 852 / 6841
    "Pozemek 6 841 m², zastavěná plocha 10 m².",                # (a) 6841, (c) over (b)
    "Areál na pozemcích o výměře 0,0852 ha.",                   # (a) for 852, in ha
    "Pozemek. Kancelář 852 m².",                                # a sentence end: (d)
    f"Celkem pozemky 6{_NBSP}841{_NBSP}m2, parcela 1.200 m2.",  # (a) 6841, grouped
    "Prodej skladu 852 m2 v areálu.",                           # (d)
)


def _sql_text_keeps(predicate: str, plot: float, text: str | None) -> bool:
    """The file's `not coalesce((select bool_or ...), false)` arm, evaluated in Python over
    the file's OWN literals: the pattern, the separator class, the dotted-thousands test,
    ha x 10000, round(x, 1), and the parcel / built-up split on the stem's first letter."""
    for literal in ("m[1] !~ '^[Zz]' as parcel", "case when m[3] ~ '^[hH]' then 10000 else 1 end",
                    "then replace(n.t, '.', '') else n.t end, ',', '.')::numeric", ", 1) as m2"):
        assert literal in predicate, literal
    pattern = re.compile(_literal_after(predicate, "regexp_matches(l.description, "))
    seps = re.compile(_literal_after(predicate, "regexp_replace(m[2], "))
    dotted = re.compile(_literal_after(predicate, "case when n.t ~ "))
    rows = []
    for m in pattern.finditer(text or ""):
        t = seps.sub("", m[2])
        t = (t.replace(".", "") if dotted.search(t) else t).replace(",", ".")
        m2 = (Decimal(t) * (10000 if re.match("^[hH]", m[3]) else 1)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP)
        rows.append((not re.match("^[Zz]", m[1]), m2))
    stored = Decimal(str(plot)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return (any(parcel and m2 == stored for parcel, m2 in rows)
            or (not any(parcel for parcel, _ in rows)
                and any(not parcel and m2 < stored for parcel, m2 in rows)))


def test_the_predicate_declines_exactly_what_the_rule_declines() -> None:
    """The hit predicate, evaluated in Python over a grid, against `stated_plot`: every
    category the vocabulary emits, every basis, a plot that equals the usable measure,
    the headline, both, or neither, NULLs on each side, and a description that states the
    same parcel, a different one, a smaller built-up area, or nothing. SQL three-valued
    logic is spelled out: a comparison against NULL is NULL, and `or` with NULL is NULL
    unless the other arm is true, so the row is NOT selected."""
    predicate = _hit_predicates()[0]
    assert "l.estate_area is not null" in predicate

    def sql_hit(category: str | None, plot: float | None, usable: float | None,
                headline: float | None, basis: str | None, text: str | None) -> bool:
        if plot is None:
            return False
        if category == "byt":
            return True
        if category != "komercni":
            return False
        usable_eq = None if usable is None else plot == usable
        headline_eq = (None if headline is None else plot == headline) and (
            basis in ("usable", "floor", "total"))
        return (bool(usable_eq) or bool(headline_eq)) and not _sql_text_keeps(
            predicate, plot, text)

    figures = (None, 10.0, 852.0, 6841.0)
    kept_by_text = 0
    for category, plot, usable, headline, basis, text in itertools.product(
            ("byt", "dum", "komercni", "pozemek", "ostatni", None),
            figures, figures, figures, tuple(sorted(area.AREA_BASES)) + (None,), _TEXTS):
        case = (category, plot, usable, headline, basis, text)
        declined = plot is not None and area.stated_plot(
            category, plot, usable=usable, headline=headline, headline_basis=basis,
            text=text) is None
        assert declined == sql_hit(*case), case
        kept_by_text += (category == "komercni" and plot is not None and plot == usable
                         and not declined)
    assert kept_by_text, "the grid never exercised the text arm"


def test_the_text_pattern_is_the_rules_own_constant_in_a_portable_spelling() -> None:
    """ONE pattern text for both engines: the file carries `scraper.area`'s constants
    verbatim, and the text avoids every construct whose meaning differs between Python's
    `re` and Postgres's regex (`\\b` is a backspace there, `\\s` / `\\d` / `\\w` and case
    folding follow the locale, an inline flag is a different syntax) and every literal
    invisible character (each is an escape)."""
    predicate = _hit_predicates()[0]
    source = area.PLOT_TEXT_EVIDENCE_SRC
    assert _literal_after(predicate, "regexp_matches(l.description, ") == source
    assert _literal_after(predicate, "case when n.t ~ ") == area.AREA_DOTTED_THOUSANDS_SRC
    assert _literal_after(predicate, "regexp_replace(m[2], ") == (
        "[" + area.AREA_THOUSANDS_SEPS_SRC + "]")
    assert not re.search(r"\\[bBsSdDwWAZ]|\(\?[aiLmsux]", source)
    assert all(ch.isprintable() and not ch.isspace() for ch in source)
    assert "'" not in source
    stem = re.compile(r"(?:" + re.search(r"\]\)\(([^()]*)\)", source)[1] + ")")
    for word in ("pozemek", "Pozemku", "POZEMKY", "parcela", "Parcely", "PARCEL"):
        assert stem.match(word) and not re.match("^[Zz]", stem.match(word)[0]), word
    for word in ("zastavěná", "Zastavene", "ZASTAVĚNÁ"):
        assert stem.match(word) and re.match("^[Zz]", stem.match(word)[0]), word


def test_the_rule_reads_at_the_columns_scale_so_sql_equality_is_the_same_test() -> None:
    """numeric(9,1) rounds half up on the write: 100.04 is stored as 100.0 and equals a
    100.0 usable measure in SQL; the Python rule quantizes the same way before comparing,
    so a live parse and this file agree on the hit."""
    assert area.stated_plot("komercni", 100.04, usable=100.0) is None
    assert area.stated_plot("komercni", 100.05, usable=100.0) == 100.05


def test_the_pre_apply_count_query_uses_the_files_own_predicate() -> None:
    header = _header_text()
    assert _hit_predicates()[0].replace("l.", "") in header
    assert "group by rail, source, category_main, is_active, area_basis" in header


def test_the_restore_names_the_one_column_and_only_fills_a_still_null_cell() -> None:
    restore = _squash(re.search(r"RESTORE.*?excluded\.marked_at;", _HEADER, re.DOTALL)[0])
    assert "set estate_area = b.estate_area" in restore
    assert "from backup_671.listing_plots b" in restore
    assert "l.estate_area is null" in restore
    assert "insert into dirty_properties" in restore


def test_the_backup_holds_the_plot_the_basis_and_the_rail_and_is_owner_only() -> None:
    body = _squash(_BODY)
    assert "create schema if not exists backup_671;" in body
    assert "revoke all on schema backup_671 from anon, authenticated;" in body
    assert "alter table backup_671.listing_plots enable row level security;" in body
    assert "revoke all on backup_671.listing_plots from anon, authenticated;" in body
    insert = re.search(r"insert into backup_671\.listing_plots \(([^)]*)\)", body)
    assert insert and _in_list(insert[1]) >= {
        "listing_id", "property_id", "rail", "estate_area", "usable_area", "area_m2",
        "area_basis"}
    assert "on conflict (listing_id) do nothing" in body
    assert "then 'P1' else 'P2'" in body


def test_the_heal_keeps_the_seam_rules() -> None:
    body = _squash(_BODY)
    # Rule 20: the same statement enqueues every touched property, distinct before the stamp.
    assert ("insert into dirty_properties (property_id, marked_at) select d.property_id, "
            "clock_timestamp() from (select distinct property_id from cleared where "
            "property_id is not null) d on conflict (property_id) do update set marked_at = "
            "excluded.marked_at;") in body
    assert "for update of l skip locked" in body
    assert "l.estate_area is not distinct from hit.estate_area" in body
    assert body.count("begin;") == 1 and body.rstrip().endswith("commit;")
