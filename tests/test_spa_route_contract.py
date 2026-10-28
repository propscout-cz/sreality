"""The SPA route patterns that cross a territory boundary.

Two territories build in-app URLs: the SPA itself (which owns them, in
`frontend/src/lib/routes.ts`) and the Chrome extension's "Otevřít v aplikaci"
link. Only the SPA's own registry has a rail — its census test lives inside the
frontend, and `frontend-build.yml` (where the lint ban runs) is path-filtered to
`frontend/**`, so neither would fire on an extension-only change.

That is the drift this file closes: renaming a route in the SPA would silently
break every extension install, with no test failing anywhere. It runs in
`test.yml`, which fires unfiltered on every push and PR.

Deliberately narrow. It pins ONLY the patterns actually emitted across a
boundary — verified by reading the emitter, not assumed:
  /listing/:source/:nativeId   chrome-extension
  /collections                 chrome-extension (the save checklist's "Spravovat kolekce")
  /n/:dispatchId               the push lane (api/push_delivery.py): one event's push, `?d=` its delivery
  /notifications               the push lane: a coalesced push, `?d=` its first delivery
Widening it past what actually crosses the boundary would make it a second full
route table to maintain, which is the failure mode it exists to prevent.

One emitter is data, not code: the health registry (`scripts/verify_pipeline.py`
CHECKS, and the watcher's own row in `health_liveness()`) stores each check's
owner page in `health_checks.owner_path`, which the Health page links. That rail
matches every non-empty path against the registry, so it pins no route here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
ROUTES_TS = REPO / "frontend" / "src" / "lib" / "routes.ts"
EXTENSION_SRC = REPO / "chrome-extension" / "src"
MIGRATIONS = REPO / "migrations"

# `key: def('/pattern'),` — the one authored form in the registry.
_DEF = re.compile(r"^\s*(\w+):\s*def\('([^']+)'\),", re.MULTILINE)

# The routes that leave the SPA, and who emits them.
CROSS_TERRITORY = {
    "listingCanonical": "/listing/:source/:nativeId",
    "collections": "/collections",
}


# The routes every push links to, absolutely, from the backend (notifications push W4). The landing page ships with
# the SPA's push wave (W5b, PR #1948): until it is in this tree its registry half waits; its shape is pinned anyway.
PUSH_EMITTED = {"notificationOpen": "/n/:dispatchId", "notifications": "/notifications"}
_LANDING_PAGE = REPO / "frontend" / "src" / "pages" / "NotificationOpen.tsx"


def _extension_source() -> str:
    """Every extension module, as one text: a link may be built in any of them."""
    files = sorted(p for p in EXTENSION_SRC.glob("*.ts") if not p.name.endswith(".test.ts"))
    assert files, f"no extension modules under {EXTENSION_SRC}"
    return "\n".join(p.read_text() for p in files)


def _registry() -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _DEF.finditer(ROUTES_TS.read_text())}


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """`/listing/:source/:nativeId` -> a regex matching one built path."""
    return re.compile("^" + re.sub(r":[A-Za-z_]\w*", r"[^/]+", pattern) + "$")


def test_the_registry_still_declares_every_externally_emitted_route() -> None:
    """A rename or deletion in the SPA registry fails here, not in someone's inbox."""
    registry = _registry()
    assert registry, f"parsed no ROUTES entries from {ROUTES_TS} — did def() change shape?"
    for key, expected in CROSS_TERRITORY.items():
        assert key in registry, (
            f"ROUTES.{key} is gone from the SPA registry, but it is emitted outside "
            f"the SPA. Update the emitters in the same PR."
        )
        assert registry[key] == expected, (
            f"ROUTES.{key} changed from {expected!r} to {registry[key]!r}. Every "
            f"installed extension still points at the old path. Update "
            f"chrome-extension/src and this contract together."
        )


def test_extension_builds_the_canonical_listing_route() -> None:
    """Vanilla-TS territory, so this reads the source rather than calling it."""
    src = _extension_source()
    built = re.search(
        r"\$\{APP_BASE_URL\}(/listing/\$\{encodeURIComponent\([^)]+\)\}"
        r"/\$\{encodeURIComponent\([^)]+\)\})",
        src,
    )
    assert built, (
        "chrome-extension/src no longer builds a "
        "${APP_BASE_URL}/listing/{source}/{native} URL — if the shape changed, "
        "check it still matches the SPA registry."
    )
    # Collapse the interpolations to their pattern shape and compare.
    shape = re.sub(r"\$\{encodeURIComponent\([^)]+\)\}", ":param", built.group(1))
    expected = re.sub(r":[A-Za-z_]\w*", ":param", CROSS_TERRITORY["listingCanonical"])
    assert shape == expected, (
        f"extension builds {shape!r} but the SPA registry declares {expected!r}"
    )


def test_extension_builds_the_collections_route() -> None:
    """The save-to-collection checklist links to the app's collections page."""
    src = _extension_source()
    assert f"${{APP_BASE_URL}}{CROSS_TERRITORY['collections']}`" in src, (
        "chrome-extension/src no longer builds "
        "${APP_BASE_URL}/collections — if the link moved, check it still matches "
        "the SPA registry."
    )


def _push_links() -> dict[str, str]:
    """The path (no origin, no query) of each link the push lane builds: one event's, and a coalesced push's."""
    from api import push_delivery as pd

    def claimed(delivery: int) -> "pd.Claimed":
        return pd.Claimed(delivery, 1, 1, "7f1c2e9a-0000-4000-8000-000000000001", None, False, True, False, 600,
                          False, "https://web.push.apple.com/x", "p", "a", "https://app.example.cz",
                          {"change_kind": "new"})

    def path(rows: list[Any]) -> str:
        navigate = pd.build_payload(rows, "https://app.example.cz", 1)["notification"]["navigate"]
        assert navigate.startswith("https://app.example.cz/") and "?d=" in navigate, navigate
        return navigate[len("https://app.example.cz"):].split("?", 1)[0]

    return {"notificationOpen": path([claimed(5)]), "notifications": path([claimed(5), claimed(6)])}


def test_the_push_lane_builds_the_routes_it_pins() -> None:
    for key, built in _push_links().items():
        assert _pattern_to_regex(PUSH_EMITTED[key]).match(built), (key, built)


def test_the_registry_declares_every_route_a_push_opens() -> None:
    """A rename in the SPA would leave every push in flight opening a dead page (and a 404 tap reads as the app
    being broken); the emitter is api/push_delivery.py's LANDING_PATH / FEED_PATH."""
    registry = _registry()
    assert registry["notifications"] == PUSH_EMITTED["notifications"]
    if "notificationOpen" not in registry and not _LANDING_PAGE.exists():
        pytest.skip("the /n/:dispatchId landing ships with the SPA's push wave (W5b, PR #1948)")
    assert registry.get("notificationOpen") == PUSH_EMITTED["notificationOpen"], (
        "ROUTES.notificationOpen moved or went: every push links to /n/{dispatch}. Update "
        "api/push_delivery.py LANDING_PATH and this contract together.")


# The watcher upserts its own registry row inside health_liveness(), so its owner
# page and domain are literals in the newest migration that (re)creates the function.
_LIVENESS_FN = re.compile(r"create or replace function public\.health_liveness\(", re.I)
_LIVENESS_ROW = re.compile(
    r"insert into health_checks\b[^(]*\(([^)]*)\)"
    r"\s*values\s*\(\s*('liveness',.*)\)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SQL_VALUE = re.compile(r"'(?:[^']|'')*'|[^,\s][^,]*")


def _liveness_row() -> dict[str, str]:
    """The watcher's registry row: column -> SQL literal."""
    defining = [
        p for p in MIGRATIONS.glob("*.sql") if _LIVENESS_FN.search(p.read_text())
    ]
    newest = max(defining, key=lambda p: (int(p.name.split("_")[0]), p.name))
    row = _LIVENESS_ROW.search(newest.read_text())
    assert row, f"{newest.name} recreates health_liveness() but its row did not parse"
    columns = [c.strip() for c in row.group(1).split(",")]
    values = _SQL_VALUE.findall(row.group(2))
    assert len(columns) == len(values), (columns, values)
    return dict(zip(columns, values))


def _text(literal: str) -> str:
    assert literal[0] == literal[-1] == "'", literal
    return literal[1:-1]


def test_every_health_owner_path_is_an_spa_route() -> None:
    """The Health page links each check to the page that owns the fix. A renamed
    route would leave a dead link with no test failing, so every non-empty
    owner_path must match a registry pattern ('' means no owner page)."""
    from scripts.verify_pipeline import CHECKS

    owners = {c.key: c.owner_path for c in CHECKS}
    owners["liveness"] = _text(_liveness_row()["owner_path"])
    patterns = [_pattern_to_regex(p) for p in _registry().values()]
    assert any(owners.values()), "parsed no owner_path: the rail would pass vacuously"
    stray = {
        k: p for k, p in owners.items() if p and not any(r.match(p) for r in patterns)
    }
    assert not stray, f"owner_path with no route in {ROUTES_TS}: {stray}"


_SECTION_KEY = re.compile(r"^\s*\['([a-z_]+)',\s*'[^']*'\],\s*$", re.M)


def test_every_registry_domain_has_a_section_on_the_health_page() -> None:
    """A check whose domain has no section is measured but never shown where it
    belongs. SECTIONS (lib/healthChecks.ts) and DOMAINS must stay one list, and
    the watcher's row, registered in SQL, must name one of them."""
    from scripts.verify_pipeline import DOMAINS

    ts = (REPO / "frontend" / "src" / "lib" / "healthChecks.ts").read_text()
    block = ts.split("export const SECTIONS = [", 1)[1].split("] as const;", 1)[0]
    assert tuple(_SECTION_KEY.findall(block)) == tuple(DOMAINS)
    domain = _text(_liveness_row()["domain"])
    assert domain in DOMAINS, f"health_liveness() registers domain {domain!r}: no section"


def test_the_contract_names_only_what_actually_crosses_a_boundary() -> None:
    """Guards the file against becoming a second full route table.

    Every entry must be emitted by the extension. An entry nothing emits is dead
    weight that still has to be kept in sync.
    """
    emitters = _extension_source()
    for key, pattern in CROSS_TERRITORY.items():
        literal_prefix = pattern.split("/:")[0]
        assert literal_prefix in emitters, (
            f"{key} ({pattern}) is pinned by this contract but nothing outside the "
            f"SPA emits it — drop it rather than maintaining a second route table."
        )


def test_registry_file_is_where_this_test_thinks_it_is() -> None:
    assert ROUTES_TS.exists(), ROUTES_TS
    assert EXTENSION_SRC.is_dir(), EXTENSION_SRC
    assert os.path.getsize(ROUTES_TS) > 0
