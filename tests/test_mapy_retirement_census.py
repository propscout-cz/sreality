"""The Mapy retirement census (spec #2441 Part 6, ticket #2448): Mapy code leaves the repo over several PRs (the phone
map, the switch-over, Stage A, Stage B), and this offline census watches it go. Outside LEDGER, no code line names a
Mapy API host, a Mapy key or switch variable, or the static-map route. Code files only (Python, TS, JS, YAML, shell),
the Actions workflows and the Railway config included; data files stay out, so key-free mapy.cz links and recorded
payloads pass. RED FIRST: a retiring PR removes its files from LEDGER, then the code. The ledger ends with the kept
scope (at most the admin address picker, its why and next-best in docs/architecture.md, ruling Q1 b) or nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ("api", "toolkit", "scraper", "scripts", "location_data", "autodedup", "tests", "frontend/src",
        "frontend/scripts", "chrome-extension/src", ".github", ".railway")
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".yml", ".yaml", ".sh"}
NAMES = re.compile(r"api\.mapy\.(cz|com)|\bMAPY2?_CZ_API_KEY\b|\bMAPY_(GEOCODE|STATIC_MAPS)_ENABLED\b"
                   r"|static[-_]map|staticMap")
_SELF = "tests/test_mapy_retirement_census.py"
# Every code file that names Mapy today. A retiring PR removes its entries first (the census goes red), then the code.
LEDGER: frozenset[str] = frozenset({
    ".github/workflows/seed_curated_cities.yml",            # Stage A (#2454)
    ".railway/railway.ts",                                  # the worker's key copy now, the API's keys at Stage B
    "api/main.py",                                          # the static-map route (#2446)
    "api/maps.py",                                          # the suggest proxy: the switch-over (#2453)
    "api/routes/property_location.py",                      # the static map (#2446)
    "frontend/src/components/filter-controls/LocationTypeahead.tsx",  # the place box: the switch-over (#2453)
    "frontend/src/components/listing-detail/AddressPicker.test.tsx",  # the static map's invalidation (#2446)
    "frontend/src/components/listing-detail/AddressPicker.tsx",       # the static map's invalidation (#2446)
    "frontend/src/components/phone/LocationBody.test.tsx",  # the static map (#2446)
    "frontend/src/components/phone/PhoneProperty.test.tsx",  # the static map (#2446)
    "frontend/src/components/phone/propertyLocation.ts",    # the static map (#2446)
    "scraper/geocoding.py",                                 # Stage A (#2454); its key helpers stay while a call runs
    "scripts/seed_curated_cities.py",                       # Stage A (#2454)
    "tests/api/test_http_json.py",                          # a Mapy URL as the helper's sample: the last call's PR
    "tests/api/test_maps.py",                               # the switch-over (#2453)
    "tests/api/test_property_location.py",                  # the static map (#2446)
    "tests/scraper/test_geocoding.py",                      # Stage A (#2454)
})


def mapy_names(root: Path, roots: Iterable[str] = CODE) -> dict[str, list[int]]:
    """Per code file under `roots`, the lines naming a Mapy API host, key or switch variable, or the static-map route."""
    found: dict[str, list[int]] = {}
    for top in roots:
        for path in sorted((root / top).rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.suffix not in SUFFIXES or "node_modules" in path.parts or rel == _SELF or not path.is_file():
                continue
            lines = [n for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if NAMES.search(line)]
            if lines:
                found[rel] = lines
    return found


def test_a_mapy_host_in_a_code_file_is_found_and_a_key_free_link_in_data_is_not(tmp_path: Path) -> None:
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "search.py").write_text('URL = "https://api.mapy.cz/v1/suggest"\n', encoding="utf-8")
    (tmp_path / "api" / "links.json").write_text('{"href": "https://mapy.cz/zakladni?q=Praha"}\n', encoding="utf-8")
    (tmp_path / "api" / "share.ts").write_text("const href = 'https://mapy.com/fnc/v1/showmap';\n", encoding="utf-8")
    assert mapy_names(tmp_path, ("api",)) == {"api/search.py": [1]}


def test_only_the_ledgers_files_name_mapy() -> None:
    found = mapy_names(ROOT)
    outside = {rel: lines for rel, lines in found.items() if rel not in LEDGER}
    assert not outside, ("these code files name a Mapy API host, key or switch variable, or the static-map route "
                         "outside the ledger (spec #2441: Mapy is leaving; the kept scope needs ruling Q1 b's "
                         "record):\n" + "\n".join(f"{rel}:{lines}" for rel, lines in sorted(outside.items())))
    stale = sorted(LEDGER - set(found))
    assert not stale, "these ledger files no longer name Mapy: drop them from LEDGER:\n" + "\n".join(stale)
