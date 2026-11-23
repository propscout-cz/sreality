"""What the read-contract tests parse the SPA's TypeScript and the read models' migrations with."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = REPO / "migrations"
QUERIES_TS = REPO / "frontend" / "src" / "lib" / "queries.ts"
BROWSE_SORT_TS = REPO / "frontend" / "src" / "lib" / "browseSort.ts"
FILTERS_TS = REPO / "frontend" / "src" / "lib" / "filters.ts"
SCOPE_ARGS = {f"{s}_{side}" for s in ("tags", "collections", "pipeline")
              for side in ("set", "in", "out")}
# Strip line comments so a comment that merely *describes* the bad pattern
# ("...a BARE call: publication_gate_enabled()...") isn't flagged. `--` covers
# SQL, `#` covers Python sources that embed build SQL.
LINE_COMMENT = re.compile(r"(--|#).*$", re.MULTILINE)


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


def registry_key_map() -> dict[str, str]:
    """filters.ts REGISTRY_KEY_MAP: registry id -> the ListingFilters field Browse keeps its value in."""
    src = FILTERS_TS.read_text(encoding="utf-8")
    block = _strip_ts_comments(_balanced_block(src, src.index("{", src.index("export const REGISTRY_KEY_MAP"))))
    keys = dict(re.findall(r"^\s*([a-z][a-z0-9_]*)\s*:\s*'(\w+)'", block, flags=re.MULTILINE))
    assert len(keys) > 60, f"only parsed {len(keys)} REGISTRY_KEY_MAP entries: the parse broke"
    return keys


def _select_cols(const: str) -> list[str]:
    src = QUERIES_TS.read_text(encoding="utf-8")
    m = re.search(rf"\bconst {const}\s*=(.*?);\n", src, re.DOTALL)
    assert m, f"{const} not found in frontend/src/lib/queries.ts"
    joined = "".join(re.findall(r"'([^']*)'", m.group(1)))
    return [c.split(":")[-1] for c in joined.split(",") if c]  # `alias:column`


def _strip_comments(src: str) -> str:
    return LINE_COMMENT.sub("", src)


def _latest_migration_matching(pattern: str, what: str) -> Path:
    pat = re.compile(pattern, re.IGNORECASE)
    hits = [p for p in MIGRATIONS.glob("*.sql") if pat.search(p.read_text())]
    assert hits, f"no migration defines {what}"
    # Numeric prefix orders them; the highest-numbered is the effective one.
    return max(hits, key=lambda p: int(p.name.split("_", 1)[0]))


def _latest_migration_defining(view: str) -> Path:
    # `(public\.)?` is load-bearing: migration 425 writes
    # `create or replace view public.properties_public`, and without the optional
    # schema prefix this resolved the "effective" definition to migration 375 —
    # one column stale, so the read-contract check below was reading the wrong
    # select list. Found while removing the gate (2026-09-05).
    return _latest_migration_matching(
        rf"create\s+(or\s+replace\s+)?(materialized\s+)?view\s+(public\.)?{re.escape(view)}\b",
        view,
    )


def _projection_columns() -> set[str]:
    """Parse browse_projection's output column names from its latest migration.
    Paren-depth-aware split of the top-level SELECT list; alias = the `as X`
    name when present, else the token after the final dot."""
    src = _strip_comments(
        _latest_migration_defining("browse_projection").read_text()
    ).lower()
    start = src.index("view browse_projection as")
    body = src[start:]
    body = body[body.index("select") + len("select"):]
    body = body[: re.search(r"\n\s*from properties", body).start()]
    items, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    cols: set[str] = set()
    for item in items:
        item = " ".join(item.split())
        m = re.search(r"\bas\s+([a-z_0-9]+)\s*$", item)
        if m:
            cols.add(m.group(1))
        else:
            cols.add(item.rsplit(".", 1)[-1].strip())
    return cols


def _sortable_fields() -> set[str]:
    src = BROWSE_SORT_TS.read_text()
    m = re.search(r"const SORTABLE_FIELDS[^;]+;", src)
    assert m, "browseSort.ts: SORTABLE_FIELDS not found"
    return set(re.findall(r"'(\w+)'", m.group(0)))
