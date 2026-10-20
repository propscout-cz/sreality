"""Migration 642 heals the last off-canon spellings; its mapping renders the vocabulary, never restates it.

The file's CASE is read back here and held against `scraper.vocabulary`: every target is a canon
member, every source is not, and a spelling the vocabulary already reads gets the vocabulary's
answer. What the vocabulary does not read is the operator's ruling, listed once below, so a
phrase cannot be mapped twice in two places with two answers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scraper import vocabulary
from toolkit.filter_registry import CONDITION_OPTIONS

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "642_legacy_vocabulary_heal.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.lstrip().startswith("--"))

CANON = frozenset(option.value for option in CONDITION_OPTIONS)
_CASE = re.compile(r"when '([^']+)'\s+then (?:'([^']+)'|null)")
MAPPING: dict[str, str | None] = {src: (tgt or None) for src, tgt in _CASE.findall(CODE)}
GRAMMAR_SQL = r"'^[1-9]\+(kk|1)$'"

# The operator's reading (2026-10-20) of each phrase the vocabulary does not know. A foreign
# "very good" is velmi_dobry, a typo is its word, an elaboration of "v puvodnim stavu" is what the
# vocabulary says of the plain phrase unless it says "udrzovany", a construction stage is
# ve_vystavbe, and a word that is not a condition is NULL.
RULED: dict[str, str | None] = {
    "puvodni": "dobry",
    "puvodni_zachovaly": "dobry",
    "zachovaly": "dobry",
    "v_puvodnim_ale_zachovalem_stavu": "dobry",
    "v_originalnim,_autentickem_stavu": "dobry",
    "dobre_stavu": "dobry",
    "dobny": "dobry",
    "bezny": "dobry",
    "v_puvodnim,_avsak_velmi_udrzovanem_stavu": "udrzovany",
    "v_puvodnim,_peclive_udrzovanem_stavu": "udrzovany",
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


def test_the_case_maps_every_source_once_and_only_to_the_canon() -> None:
    assert len(MAPPING) == 31
    assert len(_CASE.findall(CODE)) == len(MAPPING), "a spelling is mapped twice"
    assert not (set(MAPPING) & CANON), "a canon member is never a source"
    assert {t for t in MAPPING.values() if t is not None} <= CANON


@pytest.mark.parametrize("source", sorted(MAPPING))
def test_a_spelling_the_vocabulary_reads_gets_its_answer(source: str) -> None:
    answer = _vocabulary_reads(source)
    if answer is not None:
        assert source not in RULED, f"{source}: the vocabulary reads it; the ruling must not restate it"
        assert MAPPING[source] == answer
    else:
        assert source in RULED, f"{source}: the vocabulary does not read it and no ruling names it"
        assert MAPPING[source] == RULED[source]


def test_the_ruling_names_nothing_the_file_does_not_map() -> None:
    assert set(RULED) <= set(MAPPING)
    assert {s for s in MAPPING if _vocabulary_reads(s) is None} == set(RULED)


def test_the_canon_literals_are_the_registry_three_times() -> None:
    lists = re.findall(r"condition not in \(([^)]+)\)", CODE)
    assert len(lists) == 3
    for literal in lists:
        assert frozenset(re.findall(r"'([^']+)'", literal)) == CANON


def test_the_sql_grammar_is_the_vocabulary_grammar() -> None:
    assert CODE.count(GRAMMAR_SQL) == 3
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
    assert CODE.count("insert into dirty_properties (property_id)") == 2
    assert CODE.count("on conflict (property_id) do update set marked_at = now()") == 2
    assert "create schema if not exists backup_642;" in CODE
    assert "create table if not exists backup_642.listings_vocabulary as" in CODE
    assert "revoke all on all tables in schema backup_642 from anon, authenticated;" in CODE
    assert "alter table backup_642.listings_vocabulary enable row level security;" in CODE
    assert CODE.index("backup_642.listings_vocabulary as") < CODE.index("update public.listings l")
    assert "set lock_timeout = '5s';" in CODE and CODE.count("begin;") == 1 and CODE.count("commit;") == 1
    assert "raise exception '642 did not land: an off-canon condition survived';" in CODE
    assert "raise exception '642 did not land: a disposition outside the grammar survived';" in CODE
    assert "else null" in CODE, "an off-canon spelling the file does not name still becomes NULL"
