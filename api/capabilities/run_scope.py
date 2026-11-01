"""What one agent run lends a paid tool: its model client, the money and time it has left, its tally and its bill."""

from __future__ import annotations

import contextlib
from collections import Counter
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.llm_client import LLMClient

# The loop sets it round each tool call. A ContextVar, because `call_tool` is a two-argument closure over the Caller each
# runner builds: every runner's own model client reaches the tool, and no runner has to know a tool can spend.
_CURRENT: ContextVar["RunScope | None"] = ContextVar("agent_run_scope", default=None)


@dataclass
class RunScope:
    llm: LLMClient
    left_usd: Callable[[], float]
    left_s: Callable[[], float]
    counts: Counter[str] = field(default_factory=Counter)
    _cost_usd: float = 0.0
    _llm_call_id: int | None = None

    @property
    def agent_run_id(self) -> int | None:
        """The row this run is on the ledger as; the local runner has none."""
        return self.llm.agent_run_id

    def charge(self, cost_usd: float, llm_call_id: int | None) -> None:
        """Called as soon as the provider has billed, so a bill survives whatever the tool does next."""
        self._cost_usd += cost_usd
        self._llm_call_id = llm_call_id or self._llm_call_id

    def settle(self) -> tuple[float, int | None]:
        """What the tool call just ended has spent, and its model call; the loop adds it to the run."""
        bill = self._cost_usd, self._llm_call_id
        self._cost_usd, self._llm_call_id = 0.0, None
        return bill


def current() -> RunScope | None:
    return _CURRENT.get()


@contextlib.contextmanager
def running(scope: RunScope | None) -> Iterator[None]:
    token = _CURRENT.set(scope)
    try:
        yield
    finally:
        _CURRENT.reset(token)
