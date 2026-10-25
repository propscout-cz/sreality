"""Every portal's scheduled entrypoint speaks the cadence split (rule 19; Actions exit W1, #2021).

The schedule walks and the realtime worker drains, so a walk job passes `--index-only`; the job runner's registry
(W2) generates one walk per portal from `portal_factory.PORTAL_CLASSES`, so the census is that list, never a
hand-kept one.
"""

from __future__ import annotations

import importlib

import pytest

from scraper.portal_factory import PORTAL_CLASSES


@pytest.mark.parametrize("source", sorted(PORTAL_CLASSES))
def test_the_entrypoint_accepts_both_halves_of_the_split(source: str) -> None:
    entrypoint = importlib.import_module(f"scraper.{source}_main")
    walk = entrypoint._parse_args(["--index-only"])
    drain = entrypoint._parse_args(["--drain-only"])
    assert (walk.index_only, walk.drain_only) == (True, False)
    assert (drain.index_only, drain.drain_only) == (False, True)


def test_the_census_covers_every_portal() -> None:
    assert len(PORTAL_CLASSES) == 10
