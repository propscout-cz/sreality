"""One identifying User-Agent (#2016, operator ruling 2026-10-25): the product site and mailbox, never the repository."""

from __future__ import annotations

import pathlib
import re

from scraper import contact

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_TREES = ("scraper", "api", "toolkit", "scripts", "location_data", "autodedup")


def test_the_string_is_the_ruled_one() -> None:
    assert contact.USER_AGENT == "propscout/1.0 (+https://app.propscout.cz; info@propscout.cz)"


def test_every_site_we_identify_to_gets_the_one_string() -> None:
    from scraper.overpass_client import DEFAULT_USER_AGENT
    from scraper.reas_client import ReasClient
    from scripts import ingest_map_overlays

    assert DEFAULT_USER_AGENT == ReasClient.USER_AGENT == ingest_map_overlays.USER_AGENT == contact.USER_AGENT


def test_no_runtime_user_agent_names_the_repository() -> None:
    # The repository goes private: a User-Agent pointing at it would point at a 404.
    offenders = [f"{path.relative_to(ROOT)}:{n}"
                 for tree in RUNTIME_TREES for path in sorted((ROOT / tree).rglob("*.py"))
                 for n, line in enumerate(path.read_text().splitlines(), start=1)
                 if re.search(r"sreality-tracker/|\+https://github\.com/", line)]
    assert not offenders, offenders
