"""W16: ONE step vocabulary for the candidates funnel and the audit list's states.

The operator, 2026-09-14: the candidates page's funnel and the location audit page's
waterfall "must use ONE terminology and, where possible, ONE waterfall". They did not.
"Known to the location engine" READ as *answered* while it was byte-for-byte the set
the audit calls *judged* (a `listing_location` row exists); and the funnel's town step
was cut with no consumer rule at all, so its single "lost 99,889" silently added three
unlike things together:

    ~53,374  judged, still without a location   — a real loss
     45,619  ABROAD                             — an ANSWER, not a loss
       ~895  a point in Czechia, but no town    — a real, tiny loss

`location_data/location_steps.py` is now the one place those tests are spelled, and
`frontend/src/lib/locationSteps.ts` the one place a step is worded. Neither can be
imported by the other side — a migration cannot import Python, and Python cannot import
a TypeScript module — so THIS FILE IS THE RAIL that holds the three spellings together,
exactly as tests/test_location_w5_serve_resolved.py holds the consumer rule's.

Four properties, each one a way the vocabulary could quietly fork again:

1.  Every shared flag and every shared count the module renders appears VERBATIM in FUNNEL_SQL.
2.  Every step key either producer can stamp is WORDED in the frontend module, in both
    languages, with a non-empty label and a non-empty note — the KEYS are pinned, never
    the strings, because a label that cannot change without a migration is the thing
    dropping `label_cs` was for.
3.  No surface hardcodes a step's name. A step renamed in the module is renamed
    everywhere, in both languages, at once.
4.  The candidates funnel does no arithmetic, because the client sums are what let the two drift.

W1 (migration 719, ruling W1-c) dropped the audit waterfall, its second producer.
"""

from __future__ import annotations

import re
from pathlib import Path

from location_data import location_steps as ls

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "migrations"
FRONTEND = REPO / "frontend" / "src"
STEPS_PY = REPO / "location_data" / "location_steps.py"
STEPS_TS = FRONTEND / "lib" / "locationSteps.ts"
FUNNEL = FRONTEND / "components" / "new-dedup" / "CandidateFunnel.tsx"
AUDIT_PAGE = FRONTEND / "pages" / "LocationPinAudit.tsx"
CANDIDATES_PAGE = FRONTEND / "pages" / "NewDedupCandidates.tsx"

_LINE_COMMENT = re.compile(r"--.*$", re.MULTILINE)


def _squeeze(t: str) -> str:
    return " ".join(t.split()).lower()


# --------------------------------------------------------------- 1. one definition


def test_every_shared_flag_is_spelled_the_same_in_the_funnel() -> None:
    """The four booleans the chain is cut from. RED by: FUNNEL_SQL asking
    `ll.obec_kod is not null` without the obec rank floor — which is exactly the fork W16 closed,
    and which made the same word mean two sets."""
    from toolkit.dedup_candidates_sql import FUNNEL_SQL

    funnel = _squeeze(FUNNEL_SQL)
    for name, expr in ls.step_flags_sql().items():
        assert _squeeze(expr) in funnel, f"FUNNEL_SQL does not carry {name} verbatim"


def test_every_shared_count_is_spelled_the_same_in_the_funnel() -> None:
    """And the counts on top of them. `located_no_town` is the PLAIN COMPLEMENT, which is what
    keeps the three splits partitioning `located` by construction whatever the data does."""
    from toolkit.dedup_candidates_sql import FUNNEL_SQL

    funnel = _squeeze(FUNNEL_SQL)
    for name, expr in ls.shared_count_filters().items():
        assert _squeeze(expr) in funnel, name


def test_town_missing_is_one_fragment_the_scorecard_and_the_refetch_lane_share() -> None:
    """Rule 25's invariant, broken, is spelled once: the scorecard view (migration 698, which
    the location gauge reads) and scraper.db carry it VERBATIM (a migration cannot import it, and
    importing it in scraper.db is a cycle: location_steps -> claims_common -> loader_db ->
    scraper.db), held here. RED by: a lane that re-derives "no town" and queues a different set
    from the one the gauge judges."""
    from scraper import db

    missing = _squeeze(ls.town_missing_sql())
    scorecard = _squeeze(_LINE_COMMENT.sub("", (
        MIGRATIONS / "698_location_quality_scorecard.sql").read_text(encoding="utf-8")))
    assert _squeeze(ls.step_flags_sql()["has_town"]) in missing
    assert missing in _squeeze(db._LOCATION_REFETCH_CANDIDATES_SQL)
    assert missing in scorecard


def test_the_consumer_rule_is_imported_and_never_retyped() -> None:
    """`located` is claims_common's own rule. RED by: a local `geom IS NOT NULL` here,
    which would leave this chain behind the next change to what "served" means."""
    from location_data.claims_common import served_location_predicate

    assert ls.step_flags_sql()["located"] == served_location_predicate("l.id")
    body = STEPS_PY.read_text(encoding="utf-8")
    assert "from location_data.claims_common import served_location_predicate" in body
    # A claim's `value_geom` (the town-evidence probe) is not the answer row's `geom`.
    assert not re.search(r"\bgeom IS NOT NULL", body)


def test_the_town_floor_is_applied_by_rank_never_by_enum_order() -> None:
    """tests/toolkit/test_dedup_candidates_sql.py forbids `granularity >=` on the dedup side."""
    from toolkit.dedup_candidates_sql import FUNNEL_SQL

    floor = ls.obec_rank_floor_sql("gr")
    assert "location_granularity_rank" in floor
    assert "granularity >=" not in floor and "granularity > " not in floor
    assert _squeeze(floor) in _squeeze(FUNNEL_SQL)


def test_the_backend_module_carries_no_label_of_its_own() -> None:
    """Labels live in ONE file, and it is not this one — a step's name here would be a second
    label store the moment the frontend module changed. (It is moved to the bottom of this
    file's concerns deliberately: the backend declares KEYS and PREDICATES, nothing else.)"""
    body = STEPS_PY.read_text(encoding="utf-8")
    for key, fields in _ts_steps().items():
        for lang in ("cs", "en"):
            assert fields[lang] not in body, f"location_steps.py spells {key}.{lang}"


# ------------------------------------------------------------------- 2. one wording

_TS_STEP = re.compile(r"^  (\w+): \{\n(.*?)^  \},$", re.MULTILINE | re.DOTALL)
# `name:` then the string literal, which prettier may have wrapped onto the next line.
_TS_FIELD = re.compile(r"^    (\w+):\s*'((?:[^'\\]|\\.)*)',$", re.MULTILINE)


def _ts_steps() -> dict[str, dict[str, str]]:
    """LOCATION_STEPS, read out of the TypeScript. Python cannot import a .ts module, so
    this rail parses it — the same trick the migration side uses to stay pinned."""
    block = STEPS_TS.read_text(encoding="utf-8").split("export const LOCATION_STEPS", 1)[1]
    return {
        key: {name: text.replace("\\'", "'") for name, text in _TS_FIELD.findall(inner)}
        for key, inner in _TS_STEP.findall(block)
    }


def test_every_step_key_the_funnel_stamps_is_worded_in_both_languages() -> None:
    """RED by: a new step landing in the backend with no label — the page would then print
    the raw key, or worse, a blank cell where a count belongs."""
    ts = _ts_steps()
    for key in ls.STEP_KEYS:
        assert key in ts, f"locationSteps.ts does not word {key}"
        for field in ("cs", "en", "note_cs", "note_en"):
            assert ts[key].get(field), f"{key}.{field} is empty"


def test_the_frontend_words_nothing_the_backend_does_not_stamp() -> None:
    """The other direction: a label with no step behind it is wording nobody can read."""
    assert set(_ts_steps()) == set(ls.STEP_KEYS)


def test_abroad_is_worded_as_an_answer_and_never_as_a_loss() -> None:
    """The operator's actual complaint, pinned as text: 45,619 listings are ABROAD, which is
    a determination the engine made, and the page must say so where it says it."""
    ts = _ts_steps()
    assert "answer" in ts["located_foreign"]["en"].lower()
    assert "not a loss" in ts["located_foreign"]["en"].lower()
    assert "answer" in ts["located_foreign"]["note_en"].lower()


# ------------------------------------------------------- 3. nobody hardcodes a step


def test_no_surface_spells_a_step_name_of_its_own() -> None:
    """The readouts render `stepLabel(key, lang)`. RED by: a label pasted into a
    component — which is how "Known to the location engine" and "U kterých už systém polohu
    řešil" came to be the same set under two names on two pages."""
    ts = _ts_steps()
    surfaces = {
        # the funnel is English and is nothing but the chain, so both languages apply
        "CandidateFunnel.tsx": (FUNNEL.read_text(encoding="utf-8"), ("cs", "en")),
        "NewDedupCandidates.tsx": (CANDIDATES_PAGE.read_text(encoding="utf-8"), ("en",)),
    }
    for name, (text, langs) in surfaces.items():
        for key, fields in ts.items():
            for lang in langs:
                assert fields[lang] not in text, f"{name} hardcodes {key}.{lang}"


def test_both_readouts_read_the_wording_module() -> None:
    assert "@/lib/locationSteps" in FUNNEL.read_text(encoding="utf-8")
    assert "@/lib/locationSteps" in AUDIT_PAGE.read_text(encoding="utf-8")
    assert "@/lib/locationSteps" in CANDIDATES_PAGE.read_text(encoding="utf-8")


_JS_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def test_the_two_hidden_states_have_one_czech_name_on_the_audit_page() -> None:
    """The hidden set's two states name the listing list's filter pill. They were briefly
    reading two different Czech names ("nevyřešeno" in the split, "zpracováno, nerozhodnuto" in
    the list), which is the same fault as two names for one step, one level down. RED by: a
    state name typed back into the page instead of read from the module."""
    code = _JS_COMMENT.sub("", AUDIT_PAGE.read_text(encoding="utf-8"))
    ts = _ts_steps()
    for key in ("hidden_pending", "hidden_unresolved"):
        assert f"stepLabel('{key}', 'cs')" in code, f"{key} is not read from the module"
        assert ts[key]["cs"] not in code, f"the page re-types {key}'s Czech name"


# --------------------------------------------------------- 4. no client arithmetic


def test_the_candidates_funnel_computes_no_step_of_its_own() -> None:
    """It renders what the lane wrote. The client sums and the client-side `previous - this`
    subtraction are what let two readouts' steps drift apart in the first place, and they are
    gone."""
    text = FUNNEL.read_text(encoding="utf-8")
    body = text.split("*/", 1)[1]          # the header comment explains the rule
    for bad in (".n -", "reduce(", "* 100"):
        assert bad not in body, f"CandidateFunnel recomputes a funnel number ({bad})"
    assert "row.n" in body and "row.lost" in body and "row.share_pct" in body


# ---------------------------------------------------------------- the step shapes


def test_the_run_chain_carries_every_shared_key() -> None:
    """On a candidate run the town is a chain step (the subject is pairing), and abroad and the
    townless are splits of `located`."""
    for key in ls.SHARED_STEP_KEYS:
        assert key in ls.RUN_STEPS
    assert ls.RUN_STEPS["located_town"].kind == "chain"
    for key in ("located_foreign", "located_no_town"):
        assert ls.RUN_STEPS[key].kind == "split" and ls.RUN_STEPS[key].parent_key == "located"


def test_a_chain_step_never_hangs_off_a_parent_and_a_split_always_does() -> None:
    for key, shape in ls.RUN_STEPS.items():
        assert shape.kind in ("chain", "split"), key
        if shape.kind == "chain":
            assert shape.parent_key is None, key
        else:
            assert shape.parent_key in ls.RUN_STEPS, key


def test_the_shared_prefix_narrows_in_order() -> None:
    prefix = ["all_listings", "with_verdict", "located"]
    assert [k for k in ls.RUN_STEPS if k in ls.SHARED_STEP_KEYS][:3] == prefix
    assert ls.SHARED_STEP_KEYS[:3] == tuple(prefix)
