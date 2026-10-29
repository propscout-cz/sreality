"""Every `called_for` the code sends is one `llm_calls` admits: the INSERT runs after the provider has billed.

The admitted list is the newest migration that adds `llm_calls_called_for_check` (a CHECK is restated whole, so the
newest statement is the live list). The sent values are every `CALLED_FOR = "..."` constant and `called_for="..."`
literal under the Python trees. Blind spot: a value assembled at run time (autodedup's judge tiers reach the client
through `vote.called_for`) is not seen here.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TREES = ("api", "toolkit", "scraper", "scripts", "autodedup", "location_data")
_SENT = re.compile(r"""\b_?CALLED_FOR\s*(?::[^=\n]+)?=\s*["']([a-z_]+)["']|\bcalled_for\s*=\s*["']([a-z_]+)["']""")
_ADD = re.compile(r"add\s+constraint\s+llm_calls_called_for_check\b", re.I)
# Both shapes the history uses: `called_for in ('a', ...)` and `called_for = any (array['a', ...])`.
_CHECK = re.compile(r"add\s+constraint\s+llm_calls_called_for_check\s+check\s*\(\s*called_for\s*"
                    r"(?:in\s*\(|=\s*any\s*\(\s*array\s*\[)([^)\]]*)", re.S | re.I)


def _admitted() -> set[str]:
    restated = []
    for path in (ROOT / "migrations").glob("*.sql"):
        sql = path.read_text(encoding="utf-8")
        lists = _CHECK.findall(sql)
        assert len(lists) == len(_ADD.findall(sql)), f"{path.name} restates the CHECK in a shape this census cannot read"
        restated += [((int(path.name.split("_", 1)[0]), i), values) for i, values in enumerate(lists)]
    return set(re.findall(r"'([a-z_]+)'", max(restated, key=lambda r: r[0])[1]))


def _sent() -> dict[str, str]:
    out: dict[str, str] = {}
    for tree in _TREES:
        for path in sorted((ROOT / tree).rglob("*.py")):
            for match in _SENT.finditer(path.read_text(encoding="utf-8")):
                out.setdefault(match.group(1) or match.group(2), str(path.relative_to(ROOT)))
    return out


def test_every_called_for_the_code_sends_is_admitted() -> None:
    admitted = _admitted()
    assert {value: where for value, where in _sent().items() if value not in admitted} == {}


def test_the_census_reads_both_sides() -> None:
    assert {"agent_run", "agent_estimation", "summarize_listing"} <= _admitted()
    assert {"agent_run", "agent_estimation", "summarize_listing", "outreach_draft"} <= set(_sent())
