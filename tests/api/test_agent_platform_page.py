"""The admin Agent platform page's read tools are the registry's: a tool merged without its page line fails here."""

import re
from pathlib import Path

from api.capabilities.entry import Effect
from api.capabilities.registry import ENTRIES

MAP = Path(__file__).resolve().parents[2] / "frontend/src/lib/agentPlatformMap.ts"


def _page_live_tools() -> set[str]:
    text = MAP.read_text()
    done = {int(n) for n, state in re.findall(r"\bn: (\d+), done: (true|false)", text) if state == "true"}
    groups = text.split("export const TOOL_GROUPS", 1)[1].split("];", 1)[0]
    tools = re.findall(r"name: '(\w+)'(?:, ticket: (\d+))?", groups)
    return {name for name, ticket in tools if not ticket or int(ticket) in done}


def test_the_pages_live_tools_are_the_registrys_non_write_tools() -> None:
    registered = {name for name, entry in ENTRIES.items() if entry.effect is not Effect.WRITE}
    assert _page_live_tools() == registered, (
        "Update TOOL_GROUPS in frontend/src/lib/agentPlatformMap.ts: drop a merged tool's `ticket`, or add its line."
    )
