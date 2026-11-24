"""Do the providers accept every tool we declare? Only a provider can say (#2241).

`test_a_provider_accepts_every_tool` spends money, so it runs only when asked for by name: LIVE_PROVIDER_SCHEMAS=1, that
provider's key exported, and never in CI. Each call declares every producer's tools in one of three arms and asks for
almost no output: a refused schema is an HTTP 400 before the model runs, so an accepted call is the pass.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from api.agent import AGENT_TOOLS
from api.agents.terminator import _Submission
from api.capabilities.registry import ENTRIES
from api.capabilities.schema import portable
from api.outreach import RECORD_OUTREACH_DRAFT_TOOL
from api.providers import Message, ModelPrice, TextBlock, ToolSchema, compute_cost_usd
from api.providers import anthropic, gemini, openai, qwen
from api.refiner import REFINER_TOOL_SCHEMA
from toolkit import description_extraction, photo_assessment
from toolkit.building_extraction import RECORD_BUILDING_UNITS_TOOL
from toolkit.condition_markers import RECORD_LISTING_MARKERS_TOOL
from toolkit.condition_scoring import RECORD_LISTING_CONDITION_TOOL
from toolkit.floor_plan import RECORD_FLOOR_PLAN_ANALYSIS_TOOL
from toolkit.image_similarity import RECORD_IMAGE_COMPARISON_TOOL
from toolkit.summaries import RECORD_LISTING_SUMMARY_TOOL

SWITCH = "LIVE_PROVIDER_SCHEMAS"
MAX_REPLY_TOKENS = 16
TIMEOUT_S = 120.0


def _plain(raw: dict[str, Any], defs: dict[str, Any]) -> Any:
    """The renderer step 2 keeps: `$ref` inlined, no titles, no null defaults, null left as anyOf."""
    if "$ref" in raw:
        return _plain({**defs[raw["$ref"].rsplit("/", 1)[-1]], **{k: v for k, v in raw.items() if k != "$ref"}}, defs)
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "title" or (key == "default" and value is None):
            continue
        if key == "properties":
            out[key] = {name: _plain(sub, defs) for name, sub in value.items()}
        elif isinstance(value, dict):
            out[key] = _plain(value, defs)
        elif isinstance(value, list):
            out[key] = [_plain(v, defs) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    return out


def _step_2_shape(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    return _plain(raw, raw.pop("$defs", {}))


def _native(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


ARMS: dict[str, Callable[[type[BaseModel]], dict[str, Any]]] = {
    "main": portable,
    "step2": _step_2_shape,
    "native": _native,
}

# What an agent folder may declare as its output once the loader's subset checks go: the keywords the old subset refused.
_AGENT_OUTPUT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "days", "rank", "quote"],
    "properties": {
        "kind": {"const": "probe", "description": "A const."},
        "days": {"type": "integer", "enum": [1, 3, 7], "description": "An integer vocabulary."},
        "rank": {"anyOf": [{"type": "integer"}, {"type": "null"}], "description": "A number or null."},
        "quote": {"type": ["string", "null"], "description": "A type array."},
    },
}


def _written_by_hand() -> list[ToolSchema]:
    """The tools whose schema is a literal, the same in every arm."""
    literals = [
        RECORD_BUILDING_UNITS_TOOL, RECORD_LISTING_MARKERS_TOOL, RECORD_LISTING_CONDITION_TOOL,
        RECORD_FLOOR_PLAN_ANALYSIS_TOOL, RECORD_IMAGE_COMPARISON_TOOL, RECORD_LISTING_SUMMARY_TOOL,
        RECORD_OUTREACH_DRAFT_TOOL,
        description_extraction.extraction_tool(tuple(description_extraction._FIELD_SPEC)),
        photo_assessment.tool_schema(photo_assessment.load_facets()),
    ]
    tools = [ToolSchema(name=f"toolkit_{t['name']}", description=t["description"], input_schema=t["input_schema"])
             for t in literals]
    tools.append(ToolSchema(name=f"refiner_{REFINER_TOOL_SCHEMA.name}", description=REFINER_TOOL_SCHEMA.description,
                            input_schema=REFINER_TOOL_SCHEMA.input_schema))
    tools.extend(ToolSchema(name=f"estimator_{t.name}", description=t.description, input_schema=t.input_schema)
                 for t in AGENT_TOOLS.values())
    return tools


def declared_tools(arm: str) -> list[ToolSchema]:
    """Every producer's tools, the pydantic ones rendered as `arm` renders them, each under its own name."""
    render = ARMS[arm]
    tools = _written_by_hand()
    tools.extend(ToolSchema(name=f"capability_{e.name}", description=e.description, input_schema=render(e.args))
                 for e in ENTRIES.values())
    tools.append(ToolSchema(name="agent_submit_result", description="Hand in the answer.",
                            input_schema=render(_Submission)))
    if arm == "step2":
        tools.append(ToolSchema(name="agent_output", description="An agent's own output.", input_schema=_AGENT_OUTPUT))
    return tools


def _cheapest(prices: dict[str, ModelPrice]) -> str:
    return min(prices, key=lambda model: (prices[model].input_per_mtok, prices[model].output_per_mtok))


# OSS is out: it has no key and no price list, only a rented pod's URL. No run proves it, so `$ref` inlining stays.
_PROVIDERS = {
    "gemini": (gemini.GeminiProvider, "GEMINI_API_KEY", [m for m in gemini.PRICES if m.startswith("gemini-3")]),
    "anthropic": (anthropic.AnthropicProvider, "ANTHROPIC_API_KEY", [_cheapest(anthropic.PRICES)]),
    "openai": (openai.OpenAIProvider, "OPENAI_API_KEY", [_cheapest(openai.PRICES)]),
    "qwen": (qwen.QwenProvider, "QWEN_API_KEY", [_cheapest(qwen.PRICES)]),
}
_CALLS = [(provider, model, arm) for provider, (_, _, models) in _PROVIDERS.items() for model in models for arm in ARMS]


def test_every_arm_declares_every_producer_under_unique_names() -> None:
    for arm in ARMS:
        names = [t.name for t in declared_tools(arm)]
        assert len(names) == len(set(names)), arm
        assert all(len(name) <= 64 for name in names), arm
        assert {name.split("_", 1)[0] for name in names} == {"toolkit", "refiner", "estimator", "capability", "agent"}, arm


def test_the_arms_differ_where_the_decision_lies() -> None:
    """Null: `nullable` on main, anyOf in step 2. References: inlined in both, left as they are in native."""
    class Inner(BaseModel):
        n: int

    class Probe(BaseModel):
        rank: int | None = None
        inner: Inner

    main, step2, native = portable(Probe), _step_2_shape(Probe), _native(Probe)
    assert main["properties"]["rank"] == {"type": "integer", "nullable": True}
    assert step2["properties"]["rank"] == {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert step2["properties"]["inner"] == {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    assert "$defs" in native and "$ref" in native["properties"]["inner"]


@pytest.mark.parametrize(("provider", "model", "arm"), _CALLS, ids=["/".join(call) for call in _CALLS])
def test_a_provider_accepts_every_tool(provider: str, model: str, arm: str) -> None:
    factory, key_env, _ = _PROVIDERS[provider]
    key = os.environ.get(key_env)
    if os.environ.get("CI") or not os.environ.get(SWITCH) or not key:
        pytest.skip(f"spends money: needs {SWITCH}=1 and {key_env}, outside CI")
    client = factory()
    try:
        completion = client.complete(
            system="", messages=[Message(role="user", content=[TextBlock(text="Reply with the word ok.")])],
            tools=declared_tools(arm), model=model, max_tokens=MAX_REPLY_TOKENS, timeout_s=TIMEOUT_S,
        )
    except Exception as exc:
        pytest.fail(f"{provider}/{model}/{arm} refused: {str(exc).replace(key, '[key]')}", pytrace=False)
    usage = completion.usage
    cost = compute_cost_usd(price=client.price_for(model), model=model, usage=usage)
    print(f"\n{provider}/{model}/{arm} accepted: {usage.input_tokens} tokens in, ${cost:.4f}")
