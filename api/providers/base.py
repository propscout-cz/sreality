"""Neutral types every CompletionProvider produces and consumes.

The agent loop drives the conversation in these neutral types; each
provider implementation converts to/from its native SDK shapes inside
`complete()`. Keeping the loop body provider-agnostic is what lets us
swap Anthropic for Gemini (or add a third provider later) without
touching `api/agent.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

LOG = logging.getLogger(__name__)


# --- Tool schema ----------------------------------------------------------

@dataclass(frozen=True)
class ToolSchema:
    """Provider-agnostic tool declaration.

    `input_schema` is a JSON Schema object (the same shape Anthropic
    accepts as `input_schema`). Each provider translates it to its
    native function-declaration form.
    """
    name: str
    description: str
    input_schema: dict[str, Any]


# --- Conversation blocks --------------------------------------------------

@dataclass(frozen=True)
class OpaqueBlock:
    """A reply block only the provider that wrote it can read: sent back to it verbatim, dropped by any other."""
    provider: str
    data: Any


# `opaque` on the two blocks below: the block as its provider wrote it, for that provider's next request. Every other
# provider reads the neutral fields.
@dataclass(frozen=True)
class TextBlock:
    text: str
    opaque: OpaqueBlock | None = None


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    opaque: OpaqueBlock | None = None


@dataclass(frozen=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class ImageBlock:
    """A base64-encoded image in a user message (vision input)."""
    media_type: str
    data: str


Block = TextBlock | ToolUseBlock | ToolResultBlock | ImageBlock | OpaqueBlock


def written_by(block: Block, provider: str) -> Any:
    """The raw data `provider` wrote for this block, to send back verbatim; None when it wrote none."""
    opaque = block if isinstance(block, OpaqueBlock) else getattr(block, "opaque", None)
    return opaque.data if opaque is not None and opaque.provider == provider else None


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: list[Block]


# --- Completion result ----------------------------------------------------

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    """Token counts for one completion, normalized across providers.

    INVARIANT — the prompt-side fields are DISJOINT: input_tokens counts only
    fresh (uncached) prompt tokens, cache_read_tokens the cache hits,
    cache_write_tokens the cache writes. compute_cost_usd sums the three at their
    own rates, so a provider whose native prompt-token count INCLUDES cached
    tokens (OpenAI / DashScope: prompt_tokens ⊇ cached_tokens) MUST subtract them
    at the boundary or the cached fraction is billed twice. Anthropic already
    reports the three disjoint.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Hosted code: the calls the provider reports, and its container time in dollars (an estimate: see `complete`).
    code_execution_requests: int = 0
    hosted_code_usd: float = 0.0


StopReason = Literal["tool_use", "end_turn", "max_tokens", "stop", "refusal", "model_context_window_exceeded",
                     "pause_turn"]


@dataclass(frozen=True)
class Completion:
    """One turn's worth of provider output, in neutral form."""
    text_blocks: list[str]
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: Usage
    model: str
    raw: Any = field(repr=False, default=None)
    blocks: list[Block] | None = field(repr=False, default=None)
    # Hosted code only: the commands the provider ran itself, the container they ran in, the files they left.
    hosted_calls: tuple[ToolCall, ...] = ()
    container: str | None = None
    file_ids: tuple[str, ...] = ()

    @property
    def reply(self) -> list[Block]:
        """The assistant turn a tool-use loop sends back: the provider's own blocks in their order, else text then calls."""
        if self.blocks is not None:
            return list(self.blocks)
        return [
            *(TextBlock(text=t) for t in self.text_blocks if t),
            *(ToolUseBlock(id=c.id, name=c.name, input=c.input) for c in self.tool_calls),
        ]


# --- Batch (async) result types -------------------------------------------

BatchResultStatus = Literal["succeeded", "errored", "canceled", "expired"]


@dataclass(frozen=True)
class BatchStatus:
    """Snapshot of an in-flight provider batch."""
    provider_batch_id: str
    ended: bool
    raw_status: str
    counts: dict[str, int]


@dataclass(frozen=True)
class BatchResultItem:
    """One request's outcome within a completed batch.

    `completion` is populated only when `status == "succeeded"`;
    `error` carries a message for every non-success status.
    """
    custom_id: str
    status: BatchResultStatus
    completion: Completion | None = None
    error: str | None = None


# --- Pricing --------------------------------------------------------------

@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0


def compute_cost_usd(
    *,
    price: ModelPrice | None,
    model: str,
    usage: Usage,
) -> float:
    """Compute USD cost from a Usage tuple and a price row.

    If `price` is None the cost is recorded as 0 and a warning is
    logged. Wrong cost is worse than a missing one — the operator
    needs to add the model to the provider's PRICES dict.
    """
    if price is None:
        LOG.warning(
            "no price configured for model %r; recording cost_usd=0", model
        )
        return 0.0
    cost = (
        usage.input_tokens * price.input_per_mtok
        + usage.output_tokens * price.output_per_mtok
        + usage.cache_read_tokens * price.cache_read_per_mtok
        + usage.cache_write_tokens * price.cache_write_per_mtok
    ) / 1_000_000
    return round(cost + usage.hosted_code_usd, 6)


# --- Provider protocol ----------------------------------------------------

class ProviderError(Exception):
    """Raised when a provider fails to produce a Completion."""


class CompletionProvider(Protocol):
    """One backend (Anthropic, Gemini, ...).

    Implementations are stateless aside from a constructed SDK client.
    `complete()` is called once per agent turn; the agent loop owns
    the message history. `tool_choice` names a declared tool the model
    MUST call (None = the model decides).
    """

    name: str

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema],
        model: str,
        max_tokens: int = 4096,
        tool_choice: str | None = None,
    ) -> Completion: ...

    def price_for(self, model: str) -> ModelPrice | None: ...


@dataclass(frozen=True)
class HostedCode:
    """Turns on the provider's hosted code for one call; `container` resumes the one an earlier reply opened."""
    container: str | None = None


@dataclass(frozen=True)
class HostedFile:
    """A file going into or coming out of hosted code."""
    name: str
    media_type: str
    data: bytes = field(repr=False)


class HostedCodeProvider(CompletionProvider, Protocol):
    """A provider that runs code itself. Only Anthropic does (ADR-0005); the agent loop is the only caller."""

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema],
        model: str,
        max_tokens: int = 4096,
        tool_choice: str | None = None,
        hosted_code: HostedCode | None = None,
    ) -> Completion: ...

    def upload_file(self, *, name: str, media_type: str, data: bytes, expires_in_s: int) -> str: ...

    def input_block(self, file_id: str) -> Block: ...

    def download_file(self, file_id: str, *, max_bytes: int) -> HostedFile: ...

    def delete_file(self, file_id: str) -> None: ...

    def container_cost_usd(self, seconds: float) -> float: ...


class BatchCapableProvider(CompletionProvider, Protocol):
    """A provider that also supports an async batch-submission API.

    Anthropic and OpenAI both implement this; Gemini and Qwen don't. Callers
    that need batch submission type against this protocol; the synchronous
    agent loop only needs `CompletionProvider`.
    """

    def build_batch_request_params(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        tool_choice: str | None = None,
    ) -> dict[str, Any]: ...

    def submit_batch(self, items: list[tuple[str, dict[str, Any]]]) -> str: ...

    def poll_batch(self, provider_batch_id: str) -> BatchStatus: ...

    def iter_batch_results(
        self, provider_batch_id: str
    ) -> "Iterator[BatchResultItem]": ...
