"""The photo assessment's own rules, no database: the facet file is the prompt and its version, the output schema is
fixed in shape, a photo set's identity is its bytes and their order, and a reply that fails the schema is never kept."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api.llm_client import LLMClient
from api.providers import ModelPrice
from scraper import image_storage
from tests.api._agent_fixtures import reply
from tests.api._fakes import _FakeConn, _ScriptedProvider
from toolkit import photo_assessment as pa
from toolkit.vision_images import COMPARISON_MAX_EDGE, DOCUMENT_MAX_EDGE

MODEL = pa.DEFAULT_MODEL
FILE = "# Judge\n\nLook closely.\n\n### view\nWhat one sees.\n\n### kitchen\nThe units.\n"


def _facets(tmp_path: Path, text: str = FILE) -> pa.FacetFile:
    path = tmp_path / "facets.md"
    path.write_text(text, encoding="utf-8")
    return pa.load_facets(path)


def _good(*keys: str) -> dict[str, Any]:
    return {"facets": {k: {"score": 3, "evidence": f"{k}: ordinary"} for k in keys}}


def _photos(*ids: int, plans: tuple[int, ...] = ()) -> pa.PhotoSet:
    return pa.PhotoSet(property_id=7, listing_id=70, stored=len(ids) + len(plans), pending=0,
                       photos=tuple(pa.Photo(i, f"img/70/{i}.jpg", None, 800, 600) for i in ids),
                       plans=tuple(pa.Photo(i, f"img/70/{i}.jpg", None, 800, 600) for i in plans))


class _R2:
    def __init__(self) -> None:
        self.read: list[str] = []

    def download_bytes(self, key: str) -> bytes:
        self.read.append(key)
        return b"not a real jpeg, so the downscaler passes it through"


def _llm(*replies: Any) -> tuple[LLMClient, _ScriptedProvider]:
    provider = _ScriptedProvider("anthropic", list(replies), prices={MODEL: ModelPrice(4.0, 20.0, 0.2, 5.0)})
    return LLMClient(_FakeConn(), providers={"anthropic": provider}), provider


# --- the facet file --------------------------------------------------------------------------------------------

def test_the_shipped_facet_file_loads_and_names_its_facets() -> None:
    """The operator's edit is checked here: a heading that is no facet key fails CI, in words they can act on."""
    facets = pa.load_facets()
    assert facets.keys and len(set(facets.keys)) == len(facets.keys)
    assert facets.version


def test_every_edit_of_the_file_is_a_new_version_with_no_code_change(tmp_path: Path) -> None:
    before = _facets(tmp_path)
    assert _facets(tmp_path, FILE).version == before.version, "the same file is the same version"
    assert _facets(tmp_path, FILE + "\nOne more sentence.\n").version != before.version
    assert _facets(tmp_path, FILE.replace("The units.", "The units and worktop.")).version != before.version


def test_the_facet_list_comes_from_the_file(tmp_path: Path) -> None:
    facets = _facets(tmp_path, FILE + "\n### balcony\nIts size.\n")
    assert facets.keys == ("view", "kitchen", "balcony")
    assert set(pa.tool_schema(facets)["input_schema"]["properties"]["facets"]["required"]) == set(facets.keys)
    assert "Its size." in facets.system, "the file is the prompt as the operator wrote it"


@pytest.mark.parametrize("text, why", [
    ("# Judge\n\nNo facet at all.\n", "no facet"),
    ("### view\nA.\n### view\nB.\n", "twice"),
    ("### The View\nA.\n", "The View"),
])
def test_a_file_no_assessment_can_come_from_is_refused_in_words(tmp_path: Path, text: str, why: str) -> None:
    with pytest.raises(pa.FacetFileError, match=why):
        _facets(tmp_path, text)


# --- the fixed output shape ------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {},
    {"facets": {"view": {"score": 3, "evidence": "a"}}},
    {"facets": {"view": {"score": 6, "evidence": "a"}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": 0, "evidence": "a"}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": 3.5, "evidence": "a"}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": True, "evidence": "a"}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": 3, "evidence": ""}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": 3, "evidence": "two\nlines"}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": 3, "evidence": "x" * 301}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": 3}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": 3, "evidence": "a", "extra": 1}, "kitchen": {"score": 3, "evidence": "b"}}},
    {"facets": {"view": {"score": 3, "evidence": "a"}, "kitchen": {"score": 3, "evidence": "b"}, "pool": {"score": 1, "evidence": "c"}}},
    {"facets": {"view": {"score": 3, "evidence": "a"}, "kitchen": {"score": 3, "evidence": "b"}}, "overall": 3},
])
def test_a_reply_outside_the_schema_is_refused(tmp_path: Path, bad: dict[str, Any]) -> None:
    assert pa.schema_problem(bad, _facets(tmp_path)) is not None


def test_a_score_or_a_null_with_its_note_passes(tmp_path: Path) -> None:
    good = {"facets": {"view": {"score": None, "evidence": "žádná fotka z okna"}, "kitchen": {"score": 5, "evidence": "nová"}}}
    assert pa.schema_problem(good, _facets(tmp_path)) is None


# --- the photo set's identity ----------------------------------------------------------------------------------

def test_a_photo_sets_hash_is_stable_and_moves_with_its_bytes_order_and_role() -> None:
    base = _photos(1, 2).hash
    assert _photos(1, 2).hash == base
    assert _photos(2, 1).hash != base, "another order is another prompt"
    assert _photos(1, 2, 3).hash != base, "another photo"
    assert _photos(1, plans=(2,)).hash != base, "a plan is sent at another size"
    remastered = pa.PhotoSet(property_id=9, listing_id=90, stored=2, pending=0, plans=(), photos=(
        pa.Photo(1, "img/70/1.jpg", "sreality-1800-fit", 1800, 1350), pa.Photo(2, "img/70/2.jpg", None, 800, 600)))
    assert remastered.hash != base, "the re-master lane overwrites bytes under a stable key"


def test_the_hash_names_no_property_or_ad_because_the_set_is_the_identity() -> None:
    same = pa.PhotoSet(property_id=1234, listing_id=5678, stored=99, pending=4, photos=_photos(1, 2).photos, plans=())
    assert same.hash == _photos(1, 2).hash


def test_the_set_takes_the_photos_in_order_up_to_the_cap_and_the_first_plans() -> None:
    rows = [(i, f"k{i}", None, 1, 1, "kitchen") for i in range(1, pa.MAX_PHOTOS + 5)]
    rows += [(900 + i, f"p{i}", None, 1, 1, "floor_plan") for i in range(pa.MAX_PLANS + 2)]
    photos, plans = pa.select_photos(rows)
    assert [p.image_id for p in photos] == list(range(1, pa.MAX_PHOTOS + 1))
    assert [p.image_id for p in plans] == [900 + i for i in range(pa.MAX_PLANS)]


# --- one judgement ---------------------------------------------------------------------------------------------

def test_a_judgement_sends_photos_at_the_comparison_tier_and_a_plan_at_the_document_tier(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edges: dict[str, int] = {}

    def block(r2: Any, key: str, max_edge: int) -> dict[str, Any]:
        edges[key] = max_edge
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "eA=="}}
    monkeypatch.setattr(pa, "image_block", block)
    facets = _facets(tmp_path)
    llm, provider = _llm(reply((pa.TOOL_NAME, _good(*facets.keys)), model=MODEL))
    judged = pa.judge(llm, _photos(1, plans=(2,)), facets, model=MODEL, r2=_R2())
    assert edges == {"img/70/1.jpg": COMPARISON_MAX_EDGE, "img/70/2.jpg": DOCUMENT_MAX_EDGE}
    assert judged.facets == _good(*facets.keys)["facets"] and judged.model == MODEL and judged.cost_usd > 0
    call = provider.calls[0]
    assert call["system"] == facets.system and call["max_tokens"] == pa.MAX_REPLY_TOKENS
    assert [t.name for t in call["tools"]] == [pa.TOOL_NAME]
    # Opus 5.5 and Sonnet 5.5 answer a forced tool_choice with a 400: the model is told to call it, and a reply that
    # does not is refused below.
    assert call.get("tool_choice") is None


@pytest.mark.parametrize("bad_reply", [
    reply(text="Vypadá to pěkně.", model=MODEL),
    reply((pa.TOOL_NAME, {"facets": {}}), model=MODEL),
    reply((pa.TOOL_NAME, _good("view", "kitchen")), (pa.TOOL_NAME, _good("view", "kitchen")), model=MODEL),
    reply((pa.TOOL_NAME, _good("view", "kitchen")), stop="max_tokens", model=MODEL),
    reply(stop="refusal", model=MODEL),
])
def test_a_billed_reply_that_is_no_assessment_says_what_it_cost(tmp_path: Path, bad_reply: Any) -> None:
    facets = _facets(tmp_path)
    llm, _ = _llm(bad_reply)
    with pytest.raises(pa.InvalidReply) as refused:
        pa.judge(llm, _photos(1), facets, model=MODEL, r2=_R2())
    assert refused.value.cost_usd > 0 and refused.value.llm_call_id


def test_unreadable_photos_are_refused_before_any_money(tmp_path: Path) -> None:
    class Broken:
        def download_bytes(self, key: str) -> bytes:
            raise OSError("no such key")
    llm, provider = _llm()
    with pytest.raises(pa.PhotosUnreadable):
        pa.judge(llm, _photos(1), _facets(tmp_path), model=MODEL, r2=Broken())
    assert provider.calls == []


def test_without_the_bucket_nothing_is_paid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_storage, "is_configured", lambda: False)
    llm, provider = _llm()
    with pytest.raises(pa.PhotosUnreadable):
        pa.judge(llm, _photos(1), _facets(tmp_path), model=MODEL)
    assert provider.calls == []


def test_the_worst_case_covers_every_token_at_its_dearest_rate(tmp_path: Path) -> None:
    facets = _facets(tmp_path)
    price = ModelPrice(4.0, 20.0, 0.2, 5.0)
    one = pa.worst_case_usd(_photos(1), facets, price)
    assert one >= pa.MAX_REPLY_TOKENS * 20.0 / 1e6
    assert pa.worst_case_usd(_photos(1, 2), facets, price) - one == pytest.approx(pa.PHOTO_TOKENS_MAX * 5.0 / 1e6)
    assert pa.worst_case_usd(_photos(1, plans=(2,)), facets, price) - one == pytest.approx(pa.PLAN_TOKENS_MAX * 5.0 / 1e6)
