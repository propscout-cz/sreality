"""Judge the first thirty properties on two models and write a report the operator can read (MVP § 2, ADR-0007).

Usage:
    python -m scripts.photo_assessment_compare --out comparison.md

Calls the paid models: on the defaults about $4.50, never past --max-usd (it stops and reports what it has), and that
day's agent ceiling counts this spend, so new runs may park if it is low. Needs
SUPABASE_DB_URL, the R2_* variables and ANTHROPIC_API_KEY. Reads the database and writes one `llm_calls` row per model
call; it writes no assessment to the cache, so whichever model is kept starts clean. The thirty are a stable spread:
live, published properties with a stored photo, in the order of the hash of their id, so a second run sees the same
ones.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api import dependencies
from api.llm_client import LLMClient, ProviderError, provider_for_model
from api.providers import ModelPrice
from scraper import db
from toolkit import photo_assessment as pa
from toolkit.publication import published_predicate

DEFAULT_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
DEFAULT_LIMIT = 30
DEFAULT_MAX_USD = 15.0
# A proposal for the operator's reading, not a ruling: scores one point apart are the same judgement in other words.
AGREE_OVERALL = 0.90
AGREE_FACET = 0.80
WORST_SHOWN = 10

_SAMPLE_SQL = f"""
SELECT p.id FROM properties p
WHERE p.status = 'active' AND {published_predicate('p')}
  AND EXISTS (SELECT 1 FROM listings l JOIN images i ON i.listing_id = l.id
              WHERE l.property_id = p.id AND l.is_active AND i.storage_path IS NOT NULL)
ORDER BY md5(p.id::text)
LIMIT %(limit)s
"""


@dataclass
class Sample:
    """One property on both models; `failed` holds a model's failure in its words."""

    property_id: int
    judged: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


@dataclass
class FacetAgreement:
    """Over the properties both models judged: both scored, one saw it and the other did not, both said null."""

    facet: str
    scored: int = 0
    exact: int = 0
    within_one: int = 0
    total_gap: int = 0
    one_sided: int = 0
    both_null: int = 0

    @property
    def compared(self) -> int:
        return self.scored + self.one_sided + self.both_null

    @property
    def agreed(self) -> int:
        return self.within_one + self.both_null

    @property
    def agreement(self) -> float | None:
        return self.agreed / self.compared if self.compared else None

    @property
    def mean_gap(self) -> float | None:
        return self.total_gap / self.scored if self.scored else None


def facet_agreement(samples: Sequence[Sample], facets: Sequence[str], models: Sequence[str]) -> list[FacetAgreement]:
    out = []
    first, second = models
    for facet in facets:
        row = FacetAgreement(facet)
        for sample in samples:
            if first not in sample.judged or second not in sample.judged:
                continue
            a, b = sample.judged[first][facet]["score"], sample.judged[second][facet]["score"]
            if a is None and b is None:
                row.both_null += 1
            elif a is None or b is None:
                row.one_sided += 1
            else:
                row.scored += 1
                row.total_gap += abs(a - b)
                row.exact += a == b
                row.within_one += abs(a - b) <= 1
        out.append(row)
    return out


def _gap(a: Any, b: Any) -> int:
    """How far apart two scores are; one model seeing what the other could not counts as the widest."""
    if a is None and b is None:
        return 0
    return 5 if a is None or b is None else abs(a - b)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _score(value: Any) -> str:
    return "not visible" if value is None else str(value)


def _verdict(rows: Sequence[FacetAgreement], cheaper: str) -> str:
    compared = sum(r.compared for r in rows)
    if not compared:
        return "No property was judged by both models, so this run says nothing about whether they agree."
    overall = sum(r.agreed for r in rows) / compared
    same = sum(r.exact + r.both_null for r in rows) / compared
    weakest = min((r for r in rows if r.compared), key=lambda r: r.agreement or 0.0)
    head = (f"The two models were within one point of each other, or both said a facet cannot be seen, on {overall:.0%} of "
            f"the {compared} facet scores compared, and gave exactly the same answer on {same:.0%}. One point on a "
            f"five-point scale is a loose test, so read both numbers. Their weakest facet is {weakest.facet}, at "
            f"{_pct(weakest.agreement)}.")
    if overall >= AGREE_OVERALL and (weakest.agreement or 0.0) >= AGREE_FACET:
        return (f"{head} **They agree**, by the proposed bar ({AGREE_OVERALL:.0%} overall, {AGREE_FACET:.0%} on every "
                f"facet), so the design's rule keeps the cheaper model, {cheaper}. Read the disagreements below first.")
    return (f"{head} **They do not agree enough**, by the proposed bar ({AGREE_OVERALL:.0%} overall, "
            f"{AGREE_FACET:.0%} on every facet), so the default stays as it is until you have read why.")


def report(samples: Sequence[Sample], *, facets: Sequence[str], models: Sequence[str], version: str,
           costs: Mapping[str, float], cheaper: str, stopped_early: str | None = None) -> str:
    """The Markdown report: the answer first, then each facet, then the widest disagreements in both models' words."""
    first, second = models
    rows = facet_agreement(samples, facets, models)
    both = [s for s in samples if first in s.judged and second in s.judged]
    lines = [f"# Photo assessment: {first} against {second}", "", _verdict(rows, cheaper), ""]
    if stopped_early:
        lines += [f"**This run stopped early:** {stopped_early}", ""]
    lines += [f"{len(samples)} properties were tried and {len(both)} were judged by both models, under prompt version "
              f"`{version}`. Scores run from 1 (poor) to 5 (excellent), and \"not visible\" means the model said the "
              "pictures do not show that facet.", "", "## What it cost", "", "| Model | Properties judged | Total | Per property |",
              "|---|---|---|---|"]
    for model in models:
        judged = sum(model in s.judged for s in samples)
        per = f"${costs.get(model, 0.0) / judged:.3f}" if judged else "n/a"
        lines.append(f"| {model} | {judged} | ${costs.get(model, 0.0):.2f} | {per} |")
    lines += ["", "## Facet by facet", "",
              "\"Agree\" is within one point, or both saying not visible. \"Only one saw it\" means one model scored the "
              "facet and the other said it cannot be seen; that counts as a disagreement.", "",
              "| Facet | Agree | Same score | Average gap | Only one saw it | Both: not visible | Compared |",
              "|---|---|---|---|---|---|---|"]
    for r in rows:
        same = _pct(r.exact / r.scored if r.scored else None)
        gap = "n/a" if r.mean_gap is None else f"{r.mean_gap:.2f}"
        lines.append(f"| {r.facet} | {_pct(r.agreement)} | {same} | {gap} | {r.one_sided} | {r.both_null} | {r.compared} |")
    gaps = sorted(((_gap(s.judged[first][f]["score"], s.judged[second][f]["score"]), s.property_id, f, s)
                   for s in both for f in facets), key=lambda g: (-g[0], g[1], g[2]))
    worst = [g for g in gaps if g[0] > 1][:WORST_SHOWN]
    lines += ["", "## The widest disagreements", ""]
    if not worst:
        lines.append("No facet of any property differed by more than one point.")
    for _, property_id, facet, sample in worst:
        lines += [f"**Property {property_id}, {facet}**", ""]
        for model in models:
            said = sample.judged[model][facet]
            lines.append(f"- {model}: {_score(said['score'])}. {said['evidence']}")
        lines.append("")
    failures = [(s.property_id, m, why) for s in samples for m, why in s.failed.items()]
    if failures:
        lines += ["", "## What failed", "", "These are left out of the agreement above.", ""]
        lines += [f"- Property {pid} on {model}: {why}" for pid, model, why in failures]
    return "\n".join(lines).rstrip() + "\n"


def compare(conn: Any, llm: LLMClient, property_ids: Sequence[int], *, prices: Mapping[str, ModelPrice], max_usd: float,
            facets: pa.FacetFile, save: Callable[[Sequence[Sample], Mapping[str, float], str | None], None]) -> int:
    """Each property on each model, stopping before a judgement whose worst case would pass `max_usd`. `save` is called
    after every property, so what was paid for survives a crash. Returns how many properties it tried."""
    costs = dict.fromkeys(prices, 0.0)
    samples: list[Sample] = []
    for property_id in property_ids:
        photos = pa.photo_set(conn, property_id)
        if photos is None or not photos.usable:
            continue
        needed = sum(pa.worst_case_usd(photos, facets, price) for price in prices.values())
        if sum(costs.values()) + needed > max_usd:
            save(samples, costs, f"after ${sum(costs.values()):.2f}, the next property could have passed the "
                                 f"${max_usd:.2f} limit. Raise --max-usd to go further.")
            return len(samples)
        sample = Sample(property_id)
        samples.append(sample)
        for model in prices:
            try:
                judged = pa.judge(llm, photos, facets, model=model)
                sample.judged[model] = judged.facets
                costs[model] += judged.cost_usd
            except pa.InvalidReply as exc:
                sample.failed[model] = str(exc)
                costs[model] += exc.cost_usd
            except (pa.PhotosUnreadable, ProviderError) as exc:
                sample.failed[model] = str(exc)
        save(samples, costs, None)
    save(samples, costs, None)
    return len(samples)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="the report to write")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="how many properties (default 30)")
    parser.add_argument("--property-id", type=int, action="append", default=[], help="judge these instead; repeatable")
    parser.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD, help="stop before passing this (default 15)")
    parser.add_argument("--model", action="append", default=[], help="exactly two; default Opus 5.5 and Sonnet 5.5")
    opts = parser.parse_args(argv)
    models = tuple(opts.model) or DEFAULT_MODELS
    if len(models) != 2:
        print("name exactly two models", file=sys.stderr)
        return 2
    try:
        opts.out.parent.mkdir(parents=True, exist_ok=True)
        opts.out.touch()
    except OSError as exc:
        print(f"cannot write the report: {exc}", file=sys.stderr)
        return 2
    providers = dependencies.get_providers()
    with db.connect() as conn:
        llm = LLMClient(conn, providers=providers)
        try:
            prices = {model: llm.provider(provider_for_model(model)).price_for(model) for model in models}
        except ProviderError as exc:
            print(exc, file=sys.stderr)
            return 2
        unpriced = [model for model, price in prices.items() if price is None]
        if unpriced:
            print(f"no price for {unpriced}, so the limit could not hold", file=sys.stderr)
            return 2
        cheaper = min(models, key=lambda model: prices[model].output_per_mtok)
        facets = pa.load_facets()

        def save(samples: Sequence[Sample], costs: Mapping[str, float], stopped: str | None) -> None:
            opts.out.write_text(report(samples, facets=facets.keys, models=models, version=facets.version, costs=costs,
                                       cheaper=cheaper, stopped_early=stopped), encoding="utf-8")

        ids = opts.property_id
        if not ids:
            with conn.cursor() as cur:
                cur.execute(_SAMPLE_SQL, {"limit": opts.limit})
                ids = [row[0] for row in cur.fetchall()]
        tried = compare(conn, llm, ids, prices=prices, max_usd=opts.max_usd, facets=facets, save=save)
    print(f"wrote {opts.out}: {tried} properties", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
