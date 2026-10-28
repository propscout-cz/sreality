"""Guard data/clip_taxonomy.json — the entire CLIP zero-shot definition.

No torch needed (pure JSON + the label contract), so it runs in normal CI and
catches a malformed taxonomy before any dispatch burns a runner.
"""

import json
import re
from pathlib import Path

from toolkit.room_taxonomy import ROOM_TYPES

_TAX = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "clip_taxonomy.json").read_text()
)


def test_model_pinned():
    assert _TAX.get("model")


def test_revision_pinned_to_a_full_sha():
    # The model NAME alone resolves to the hub's head at download time, so without a
    # revision an upstream re-upload silently changes every vector we write while
    # image_clip_embeddings.model stays identical. 10.36M stored vectors and every
    # per-tag centroid depend on this being one coherent population.
    rev = _TAX.get("revision")
    assert rev, "clip_taxonomy.json must pin a 'revision'"
    assert re.fullmatch(r"[0-9a-f]{40}", rev), f"revision {rev!r} is not a 40-hex sha"


def test_toolkit_reads_the_same_pin():
    # One source for the pair — a second hardcoded copy is how they drift apart.
    from toolkit.tag_definitions import embedding_model, embedding_revision
    assert embedding_model() == _TAX["model"]
    assert embedding_revision() == _TAX["revision"]


def test_every_collapse_key_is_a_prompt():
    assert set(_TAX["collapse"]) == set(_TAX["prompts"])


def test_collapse_targets_are_logical_labels():
    valid = set(ROOM_TYPES)
    for fine, logical in _TAX["collapse"].items():
        assert logical in valid, f"collapse {fine!r}->{logical!r} not a ROOM_TYPE"


def test_prompts_nonempty():
    assert _TAX["prompts"]
    assert all(isinstance(v, str) and v for v in _TAX["prompts"].values())


def test_staircase_tags_wired():
    assert _TAX["collapse"]["staircase_interior"] == "staircase_interior"
    assert _TAX["collapse"]["staircase_exterior"] == "staircase_exterior"
    assert {"staircase_interior", "staircase_exterior"} <= set(ROOM_TYPES)


def test_toilet_anchor_disambiguates_from_bathroom():
    # The sharpened WC anchor must exclude shower/bathtub so CLIP stops confusing WC <-> koupelna.
    assert "no shower" in _TAX["prompts"]["toilet"].lower()


def test_property_document_wired():
    assert _TAX["collapse"]["energy_certificate"] == "property_document"
    assert _TAX["collapse"]["document_text"] == "property_document"
    assert "property_document" in set(ROOM_TYPES)
