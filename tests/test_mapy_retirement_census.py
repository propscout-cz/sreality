"""The Mapy retirement census (spec #2441 Part 6, ticket #2448): Mapy code leaves the repo over several PRs (the phone
map, the switch-over, Stage A, Stage B), and this offline census watches it go. Outside LEDGER, no code line names a
Mapy API host, a Mapy key or switch variable (a `VITE_`-prefixed copy too), or the static-map route. Code files only
(Python, TS, JS, YAML, shell, Dockerfiles) anywhere in the repo, the Actions workflows and the Railway config included;
data files stay out, so key-free mapy.cz links and recorded payloads pass. RED FIRST: a retiring PR removes its files from LEDGER, then the code. The ledger ends with the kept
scope (at most the admin address picker, its why and next-best in docs/architecture.md, ruling Q1 b) or nothing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".yml", ".yaml", ".sh"}
# Never walked: installs and builds, and every dot-folder but these two (.claude holds other sessions' checkouts).
_SKIP = {"node_modules", "dist", "__pycache__"}
_DOT_KEPT = {".github", ".railway"}
NAMES = re.compile(r"api\.mapy\.(cz|com)|MAPY2?_CZ_API_KEY|MAPY_(GEOCODE|STATIC_MAPS)_ENABLED"
                   r"|(?i:static[-_]?maps?)(?![a-zA-Z])")
_SELF = "tests/test_mapy_retirement_census.py"
# Every code file that names Mapy today. A retiring PR removes its entries first (the census goes red), then the code.
LEDGER: frozenset[str] = frozenset({
    ".github/workflows/seed_curated_cities.yml",            # Stage A (#2454)
    ".railway/railway.ts",                                  # the worker's key copy (#2454), the API's keys (#2455)
    "api/maps.py",                                          # the suggest proxy: the switch-over (#2453)
    "frontend/src/components/filter-controls/LocationTypeahead.tsx",  # the place box: the switch-over (#2453)
    "frontend/src/components/listing-detail/AddressPicker.test.tsx",  # the static map's invalidation (#2446)
    "frontend/src/components/listing-detail/AddressPicker.tsx",       # the static map's invalidation (#2446)
    "frontend/src/components/phone/LocationBody.test.tsx",  # the static map (#2446)
    "frontend/src/components/phone/LocationBody.tsx",       # the static map (#2446)
    "frontend/src/components/phone/PhoneProperty.test.tsx",  # the static map (#2446)
    "frontend/src/components/phone/propertyLocation.ts",    # the static map (#2446)
    "scraper/geocoding.py",                                 # Stage A (#2454); its key helpers move to the API first
    "scripts/seed_curated_cities.py",                       # Stage A (#2454)
    "tests/api/test_http_json.py",                          # pins suggest's key-free log: the switch-over (#2453)
    "tests/api/test_maps.py",                               # the switch-over (#2453)
    "tests/scraper/test_geocoding.py",                      # Stage A (#2454)
})


def mapy_names(root: Path) -> dict[str, list[int]]:
    """Per code file under `root`, the lines naming a Mapy API host, key or switch variable, or the static-map route."""
    found: dict[str, list[int]] = {}
    for top, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP and (not d.startswith(".") or d in _DOT_KEPT))
        for name in sorted(files):
            path = Path(top) / name
            rel = path.relative_to(root).as_posix()
            if (path.suffix not in SUFFIXES and name != "Dockerfile") or rel == _SELF:
                continue
            lines = [n for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if NAMES.search(line)]
            if lines:
                found[rel] = lines
    return found


def test_a_mapy_name_in_a_code_file_is_found_and_a_key_free_link_in_data_is_not(tmp_path: Path) -> None:
    planted = {
        "api/search.py": 'URL = "https://api.mapy.cz/v1/suggest"\n',                          # a host
        "frontend/src/env.ts": "const k = import.meta.env.VITE_MAPY_CZ_API_KEY;\n",            # a key, VITE_ too
        "scraper/flags.py": 'ON = os.environ.get("MAPY_STATIC_MAPS_ENABLED")\n',               # a switch
        "frontend/src/Poloha.tsx": "const W = STATIC_MAP_W;\nconst img = useStaticMap(id);\n",  # the static map
        "Dockerfile": "ENV MAPY2_CZ_API_KEY=\n",                                                # a Dockerfile is code
        "api/links.json": '{"href": "https://mapy.cz/zakladni?q=Praha"}\n',                     # data: key-free link
        "frontend/src/share.ts": "const href = 'https://mapy.com/fnc/v1/showmap';\n",          # a key-free link
        "toolkit/geo.py": "def static_mapping(): ...\n",                                        # not the static map
        "frontend/node_modules/x/index.js": "fetch('https://api.mapy.cz/v1/suggest')\n",       # an install
        ".claude/worktrees/w/api/maps.py": 'URL = "https://api.mapy.cz/v1/suggest"\n',         # another checkout
    }
    for rel, text in planted.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    assert mapy_names(tmp_path) == {"Dockerfile": [1], "api/search.py": [1], "frontend/src/Poloha.tsx": [1, 2],
                                    "frontend/src/env.ts": [1], "scraper/flags.py": [1]}


def test_only_the_ledgers_files_name_mapy() -> None:
    found = mapy_names(ROOT)
    outside = {rel: lines for rel, lines in found.items() if rel not in LEDGER}
    assert not outside, ("these code files name a Mapy API host, key or switch variable, or the static-map route "
                         "outside the ledger (spec #2441: Mapy is leaving; the kept scope needs ruling Q1 b's "
                         "record):\n" + "\n".join(f"{rel}:{lines}" for rel, lines in sorted(outside.items())))
    stale = sorted(LEDGER - set(found))
    assert not stale, "these ledger files no longer name Mapy: drop them from LEDGER:\n" + "\n".join(stale)
