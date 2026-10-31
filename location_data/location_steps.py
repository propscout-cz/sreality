"""W16 — the ONE place the location chain's steps are spelled (operator ruling 2026-09-14).

Two readouts asked the same first questions in different words: the NEW DEDUP candidates
funnel ("Known to the location engine", "Placed precisely enough to name a town") and the
location audit page's waterfall, which asked the same two questions in Czech.
Step 2 was the SAME set under two names; step 3 was two different sets under two names, and
the candidates side's single "lost 99,889" silently added three unlike things together —
judged-but-not-located (~53 k), abroad (45.6 k, which is an ANSWER) and a Czech point with no
town (~900).

So: one definition per step, spelled here once and RENDERED into every producer.

* `step_flags_sql()` — the four booleans the chain is cut with. `located` is the consumer
  rule imported from `claims_common` and never retyped; `has_town` carries dedup's obec rank
  floor, applied by RANK through `location_granularity_rank` and never by enum order
  (tests/toolkit/test_dedup_candidates_sql.py forbids the enum comparison).
* `step_counts_sql()` — the six shared counts, so the funnel and the audit producer aggregate
  with one expression each.
* `AUDIT_STEPS` / `RUN_STEPS` — the step keys in order and each key's shape. `located_town` is
  a SPLIT of `located` on the audit page (whose subject is the hidden set) and a CHAIN step on
  a candidate run (whose subject is pairing): same key, same predicate, same wording — only
  the role differs, and the role is declared here rather than in a component.

NO WORDING LIVES HERE. A step's Czech and English labels live in exactly one file,
frontend/src/lib/locationSteps.ts, because a better sentence must never cost a migration.
A migration cannot import Python, so two SQL carriers hold these expressions VERBATIM:
`refresh_location_audit_waterfall()` (pinned by tests/test_location_steps_vocabulary.py) and the
location scorecard view `location_quality_by_source` (migration 698: the step flags,
`town_missing_sql()` and `town_evidence_sql()`, pinned by
tests/location_data/test_location_quality_view_contract.py) — the same rail W5 uses to keep the
serving surfaces on one rule.
"""

from __future__ import annotations

from typing import NamedTuple

from location_data.claims_common import served_location_predicate


def obec_rank_floor_sql(alias: str = "gr") -> str:
    """Dedup's blocking floor: the answer is at least town-grain. By RANK, never by enum
    order — `location_granularity_rank` is the one table that says which grain outranks
    which (migration 380)."""
    return (
        f"{alias}.rank >= (SELECT r.rank FROM location_granularity_rank r"
        " WHERE r.granularity = 'obec')"
    )


def step_flags_sql(*, listings: str = "l", loc: str = "ll", rank: str = "gr") -> dict[str, str]:
    """The four booleans every step is cut from, keyed onto a surface's own aliases.

    `located` is `SERVED_LOCATION_PREDICATE` — a point, or the determination that the listing
    is abroad — rendered off the LISTING and not off the left join, so the text stays pinnable
    against `claims_common`. The other three read the answer row the left join brought.
    """
    return {
        "located": served_location_predicate(f"{listings}.id"),
        "has_verdict": f"({loc}.listing_id is not null)",
        "is_foreign": f"({loc}.country_status = 'foreign')",
        "has_town": f"({loc}.obec_kod is not null and {obec_rank_floor_sql(rank)})",
    }


def located_town_sql(flags: str = "b") -> str:
    """"Placed in a named town": located, not abroad, and the answer names an obec at obec
    grain or finer. The three splits of `located` are mutually exclusive BY CONSTRUCTION, so
    they partition it whatever the data says — a row that is both foreign and Czech-towned is
    a resolver bug, and foreign wins because that is the answer the consumer rule serves."""
    return (
        f"{flags}.located and not coalesce({flags}.is_foreign, false)"
        f" and coalesce({flags}.has_town, false)"
    )


def _located_foreign_sql(flags: str) -> str:
    return f"{flags}.located and coalesce({flags}.is_foreign, false)"


def _no_town(is_foreign: str, has_town: str) -> str:
    return f"not coalesce({is_foreign}, false) and not coalesce({has_town}, false)"


def _located_no_town_sql(flags: str) -> str:
    """The plain complement, so the three splits close by construction."""
    return f"{flags}.located and {_no_town(f'{flags}.is_foreign', f'{flags}.has_town')}"


def town_missing_sql(*, loc: str = "ll", rank: str = "gr") -> str:
    """Rule 25's invariant, broken: not determined foreign and no town, off a LEFT JOIN of the
    answer row — so a listing with no row counts (foreign is a determination, never a default).
    The scorecard view (migration 698) counts it; the `location_refetch` lane acts on it."""
    flags = step_flags_sql(loc=loc, rank=rank)
    return _no_town(flags["is_foreign"], flags["has_town"])


# Rule 25's broken set splits by EVIDENCE (ruling Q6, W1): exactly the claims BIND can reach a
# town from (resolver/bind.py, town ladder R4/R6/R7). `street_name` binds only inside an anchoring
# town; `okres_name` and `kraj_name` cannot name one.
TOWN_EVIDENCE_CLAIM_TYPES: tuple[str, ...] = (
    "obec_name", "cast_obce_name", "psc", "address_point_id", "coordinate",
)


def town_evidence_sql(listing_id: str = "b.id", claims: str = "ec") -> str:
    """The listing holds a town-bearing claim (a coordinate only with a position): unresolved, else withheld."""
    types = ", ".join(f"'{t}'" for t in TOWN_EVIDENCE_CLAIM_TYPES)
    return (
        f"EXISTS (SELECT 1 FROM location_claims {claims}"
        f" WHERE {claims}.listing_id = {listing_id}"
        f" AND {claims}.claim_type IN ({types})"
        f" AND {claims}.subject_scoped IS NOT FALSE"
        f" AND ({claims}.claim_type <> 'coordinate' OR {claims}.value_geom IS NOT NULL))"
    )


def shared_count_filters(flags: str = "b") -> dict[str, str]:
    """The FILTER expression behind each shared count, without the `count(*)` around it —
    what tests/test_location_steps_vocabulary.py pins into both producers."""
    return {
        "with_verdict": f"{flags}.has_verdict",
        "located": f"{flags}.located",
        "located_town": located_town_sql(flags),
        "located_foreign": _located_foreign_sql(flags),
        "located_no_town": _located_no_town_sql(flags),
    }


def step_counts_sql(flags: str = "b", *, total_as: str = "all_listings") -> str:
    """The six shared counts as one SELECT-list fragment. `total_as` names the first column
    only: the candidates funnel has always called its base `listings` and the audit relation
    calls it `all_listings`; they are the same count."""
    parts = [f"count(*) as {total_as}"]
    parts += [
        f"count(*) filter (where {expr}) as {name}"
        for name, expr in shared_count_filters(flags).items()
    ]
    return ", ".join(parts)


class StepShape(NamedTuple):
    """Where a step sits in a chain. `kind`: 'chain' narrows the step above it and carries a
    loss; 'split' partitions its `parent_key` exactly and carries none; 'deduction' is a set
    carved out of the chain — calling it a loss would double-count it down the column."""

    step_no: int
    sub_no: int
    kind: str
    parent_key: str | None


# The audit page's chain (migrations 523/524/526): every listing → judged → located, with the
# hidden set deducted from the whole database and read against it.
AUDIT_STEPS: dict[str, StepShape] = {
    "all_listings": StepShape(1, 0, "chain", None),
    "with_verdict": StepShape(2, 0, "chain", None),
    "located": StepShape(3, 0, "chain", None),
    "located_town": StepShape(3, 1, "split", "located"),
    "located_foreign": StepShape(3, 2, "split", "located"),
    "located_no_town": StepShape(3, 3, "split", "located"),
    "hidden": StepShape(4, 0, "deduction", "all_listings"),
    "hidden_unresolved": StepShape(4, 1, "split", "hidden"),
    "hidden_pending": StepShape(4, 2, "split", "hidden"),
}

# A candidate run's chain. Same six keys down to the town, then the two dedup-only steps. The
# town is a CHAIN step here because pairing is what the page is about — and its loss is
# EXACTLY the two splits above it, which is the whole point of stamping them.
RUN_STEPS: dict[str, StepShape] = {
    "all_listings": StepShape(1, 0, "chain", None),
    "with_verdict": StepShape(2, 0, "chain", None),
    "located": StepShape(3, 0, "chain", None),
    "located_foreign": StepShape(3, 1, "split", "located"),
    "located_no_town": StepShape(3, 2, "split", "located"),
    "located_town": StepShape(4, 0, "chain", None),
    "eligible": StepShape(5, 0, "chain", None),
    "paired": StepShape(6, 0, "chain", None),
}

# The keys BOTH readouts carry, in chain order — the shared vocabulary itself.
SHARED_STEP_KEYS: tuple[str, ...] = (
    "all_listings", "with_verdict", "located",
    "located_town", "located_foreign", "located_no_town",
)

# Every key either side names; frontend/src/lib/locationSteps.ts must word all of them.
STEP_KEYS: tuple[str, ...] = tuple(dict.fromkeys((*AUDIT_STEPS, *RUN_STEPS)))
