"""The legacy-vocabulary heal migration renders the vocabulary; it never restates it.

The file's CASE is read back here and held against `scraper.vocabulary`: every target is a
member of the canon AS IT STOOD when the file was written (a later addition to the canon is
not a defect of an applied file), every source is not, and a spelling the vocabulary reads
gets an answer that AGREES with the vocabulary's. What the vocabulary did not read on
2026-10-20 is this PR's reading under the operator's delegation, listed once below; if the
vocabulary later learns one of those spellings, the two must agree -- never exclude each other."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scraper import vocabulary
from toolkit.filter_registry import CONDITION_OPTIONS

MIGRATION = sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*_legacy_vocabulary_heal.sql"))[-1]
NUMBER = MIGRATION.name[:3]
SQL = MIGRATION.read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.lstrip().startswith("--"))

# The canon the file was written against. The live registry may grow; it may not shrink or rename.
CANON_AT_HEAL = frozenset({
    "projekt", "ve_vystavbe", "novostavba", "po_rekonstrukci", "velmi_dobry", "dobry",
    "udrzovany", "v_rekonstrukci", "pred_rekonstrukci", "spatny", "k_demolici",
})
_CASE = re.compile(r"when '([^']+)'\s+then (?:'([^']+)'|null)")
MAPPING: dict[str, str | None] = {src: (tgt or None) for src, tgt in _CASE.findall(CODE)}
GRAMMAR_SQL = r"'^[1-9]\+(kk|1)$'"

# This PR's reading (2026-10-20, under "resolve the legacy vocabulary") of each phrase the vocabulary
# did not read. A foreign "very good" is velmi_dobry; a typo is its word; an elaboration of "v puvodnim
# stavu" is what the vocabulary says of the plain phrase (dobry -- udrzovany ranks below dobry, so a
# better-kept original state must not land there); a construction stage is ve_vystavbe (pred kolaudaci
# follows legal status: unfinished until approved); a word that is not a condition is NULL.
RULED: dict[str, str | None] = {
    "puvodni": "dobry",
    "puvodni_zachovaly": "dobry",
    "zachovaly": "dobry",
    "v_puvodnim_ale_zachovalem_stavu": "dobry",
    "v_puvodnim,_avsak_velmi_udrzovanem_stavu": "dobry",
    "v_puvodnim,_peclive_udrzovanem_stavu": "dobry",
    "v_originalnim,_autentickem_stavu": "dobry",
    "dobre_stavu": "dobry",
    "dobny": "dobry",
    "bezny": "dobry",
    "ve_velmi_dobrem_stavu": "velmi_dobry",
    "sehr_dobry": "velmi_dobry",
    "bardzo_dobry": "velmi_dobry",
    "zeer_dobry": "velmi_dobry",
    "muito_dobry": "velmi_dobry",
    "po_castecne_rekonstrukci": "po_rekonstrukci",
    "po_castecne_renovaci": "po_rekonstrukci",
    "predrekonstrukci": "pred_rekonstrukci",
    "predkonstrukci": "pred_rekonstrukci",
    "pred_rekonstrukciou": "pred_rekonstrukci",
    "v_prestavbe": "v_rekonstrukci",
    "ve_vstavbe": "ve_vystavbe",
    "ve_vyvstavbe": "ve_vystavbe",
    "hruba_stavba": "ve_vystavbe",
    "pred_kolaudaci": "ve_vystavbe",
    "smisena": None,
    "nizkoenergeticka": None,
    "zatepleny": None,
    "demontovana": None,
}


def _vocabulary_reads(spelling: str) -> str | None:
    """The vocabulary's answer to the spelling's words (the stored key with `_` as the space it was)."""
    vocabulary.take_unmapped()
    answer = vocabulary.canonical("condition", "bazos", spelling.replace("_", " "))
    vocabulary.take_unmapped()
    return answer


def test_the_canon_the_file_was_written_against_still_stands() -> None:
    live = frozenset(option.value for option in CONDITION_OPTIONS)
    assert CANON_AT_HEAL <= live, "a canon member the heal targets was renamed or removed"


def test_the_case_maps_every_source_once_and_only_to_the_canon() -> None:
    assert len(MAPPING) == 31
    assert len(_CASE.findall(CODE)) == len(MAPPING), "a spelling is mapped twice"
    assert not (set(MAPPING) & CANON_AT_HEAL), "a canon member is never a source"
    assert {t for t in MAPPING.values() if t is not None} <= CANON_AT_HEAL


@pytest.mark.parametrize("source", sorted(MAPPING))
def test_the_file_agrees_with_whatever_the_vocabulary_reads(source: str) -> None:
    answer = _vocabulary_reads(source)
    if answer is not None:
        assert MAPPING[source] == answer, f"{source}: the vocabulary reads {answer!r}, the file wrote {MAPPING[source]!r}"
        if source in RULED:
            assert RULED[source] == answer, f"{source}: the ruling and the vocabulary disagree"
    else:
        assert source in RULED, f"{source}: the vocabulary does not read it and no ruling names it"
        assert MAPPING[source] == RULED[source]


def test_the_ruling_names_nothing_the_file_does_not_map() -> None:
    assert set(RULED) <= set(MAPPING)


def test_the_canon_literals_are_the_canon_at_heal_everywhere() -> None:
    lists = re.findall(r"condition not in \(([^)]+)\)", CODE)
    assert len(lists) == 4, "backup, gate, update, post-condition"
    for literal in lists:
        assert frozenset(re.findall(r"'([^']+)'", literal)) == CANON_AT_HEAL


def test_the_sql_grammar_is_the_vocabulary_grammar() -> None:
    assert CODE.count(GRAMMAR_SQL) == 4, "backup, gate, update, post-condition"
    grammar = re.compile(GRAMMAR_SQL.strip("'"))
    for member in vocabulary.CANON["disposition"]:
        assert grammar.fullmatch(member), member
        assert vocabulary.disposition("bazos", member) == member
    vocabulary.take_unmapped()
    for near_miss in ("6+2", "1+0", "0+1", "8+7", "2+0", "9+5"):
        assert grammar.fullmatch(near_miss) is None, near_miss
        assert vocabulary.disposition("bazos", near_miss) is None, near_miss
    assert vocabulary.take_unmapped()


def test_the_file_keeps_the_seam_rules() -> None:
    assert "listing_snapshots" not in CODE and "last_seen_at" not in CODE
    assert CODE.count("insert into dirty_properties (property_id, marked_at)") == 2
    assert CODE.count("clock_timestamp()\n  from (select distinct property_id from") == 2, (
        "distinct BEFORE the per-row clock, or ON CONFLICT hits a property twice (apply run 39399998245)")
    assert CODE.count("on conflict (property_id) do update set marked_at = excluded.marked_at;") == 2
    backup = f"backup_{NUMBER}"
    assert f"create schema if not exists {backup};" in CODE
    assert f"create table if not exists {backup}.listings_vocabulary as" in CODE
    assert f"revoke all on all tables in schema {backup} from anon, authenticated;" in CODE
    assert f"alter table {backup}.listings_vocabulary enable row level security;" in CODE
    assert CODE.index(f"{backup}.listings_vocabulary as") < CODE.index("not exists (select 1 from")
    assert CODE.index("not exists (select 1 from") < CODE.index("update public.listings l")
    assert f"raise exception '{NUMBER} refused: % row(s) to heal are not in {backup}" in CODE
    assert "set lock_timeout = '5s';" in CODE and "set local lock_timeout = '5s';" in CODE
    assert CODE.count("begin;") == 1 and CODE.count("commit;") == 1
    assert f"raise exception '{NUMBER} did not land: an off-canon condition survived';" in CODE
    assert f"raise exception '{NUMBER} did not land: a disposition outside the grammar survived';" in CODE
    assert "else null" in CODE, "an off-canon spelling the file does not name still becomes NULL"
