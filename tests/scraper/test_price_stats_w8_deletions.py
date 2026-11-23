"""W8-2 (#2362): the price-stats town resolution is gone. The fetch reads `ruian_translation`, so nothing resolves a
town by name, by suggest or by point in polygon any more (that the old locality table is not named again:
tests/test_admin_boundaries_unread.py)."""

from __future__ import annotations

import pathlib

import pytest
import yaml

from scraper import price_stats_client, price_stats_db, price_stats_main, price_stats_parser

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("module", "name"), [
    *((price_stats_db, n) for n in ("upsert_locality", "list_localities", "resolve_obce", "unresolved_obec_names",
                                    "localities_for_obec_ids", "locality_exists")),
    *((price_stats_main, n) for n in ("_load_city_names", "resolve_localities", "resolve_obce_by_geo",
                                      "_DEFAULT_CITIES")),
    (price_stats_client.PriceStatsClient, "suggest_municipality"),
    (price_stats_client.PriceStatsClient, "suggest_municipalities"),
    (price_stats_client, "SUGGEST_URL"),
    (price_stats_parser, "parse_suggest_municipality"),
    (price_stats_parser, "parse_suggest_municipalities"),
])
def test_the_resolution_code_is_gone(module: object, name: str) -> None:
    assert not hasattr(module, name)


@pytest.mark.parametrize("flag", ["--resolve-only", "--cities-file"])
def test_the_resolution_flags_are_gone(flag: str) -> None:
    with pytest.raises(SystemExit) as exit_:
        price_stats_main.main([flag, "x"] if flag == "--cities-file" else [flag])
    assert exit_.value.code == 2, "argparse refuses an unknown flag before any connection opens"


def test_the_city_list_and_the_resolve_mode_are_gone() -> None:
    assert not (ROOT / "data" / "price_stat_cities.json").exists()
    workflow = ROOT / ".github" / "workflows" / "scrape_price_stats.yml"
    mode = yaml.safe_load(workflow.read_text())[True]["workflow_dispatch"]["inputs"]["mode"]
    assert mode["options"] == ["probe", "full"]
    assert "--resolve-only" not in workflow.read_text()
