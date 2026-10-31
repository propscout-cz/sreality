"""Anthropic backend for the CompletionProvider protocol.

Wraps the official `anthropic` Python SDK. Owns the model-pricing
dict and the block-shape translation between Anthropic content
blocks and the neutral types in `api.providers.base`.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import replace
from typing import Any

from collections.abc import Iterator

from api.providers.base import (
    BatchResultItem,
    BatchStatus,
    Block,
    Completion,
    HostedCode,
    HostedFile,
    ImageBlock,
    Message,
    ModelPrice,
    OpaqueBlock,
    ProviderError,
    TextBlock,
    ToolCall,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    Usage,
    written_by,
)

LOG = logging.getLogger(__name__)


# Source: docs.anthropic.com. Update when prices change or when a
# caller adds a new model.
PRICES: dict[str, ModelPrice] = {
    "claude-haiku-4-5":  ModelPrice(1.0, 5.0, 0.10, 1.25),
    "claude-sonnet-4-5": ModelPrice(3.0, 15.0, 0.30, 3.75),
    "claude-sonnet-4-6": ModelPrice(3.0, 15.0, 0.30, 3.75),
    # Opus 4.5 (Nov 2025) cut the Opus tier to $5/$25; 4.7 kept the older
    # $15/$75. Confirm against docs.anthropic.com if it drifts.
    "claude-opus-4-5":   ModelPrice(5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-7":   ModelPrice(15.0, 75.0, 1.50, 18.75),
    # The agent platform's code agents (docs/design/agent-platform/MVP.md); cache write is 1.25x input.
    "claude-opus-5-5":   ModelPrice(4.0, 20.0, 0.20, 5.00),
    "claude-sonnet-5-5": ModelPrice(2.0, 10.0, 0.20, 2.50),
}

# The hosted code tool (ADR-0005). No beta header; the newest version the docs list, which every priced model accepts.
CODE_TOOL = {"type": "code_execution_20260521", "name": "code_execution"}
# Billed by execution time per container, minimum five minutes; the org's first 1,550 hours a month are free, which
# nothing in a response shows. A response reports no execution time either, so a call's container time is priced as
# the whole time the request took: an upper bound at list price (docs: pricing, code execution tool).
CONTAINER_USD_PER_HOUR = 0.05
CONTAINER_MINIMUM_S = 300.0
_FILE_TIMEOUT_S = 60.0
# The two result blocks that carry produced files; a text-editor result's `content` is not a list of them.
_FILE_RESULTS = ("bash_code_execution_tool_result", "code_execution_tool_result")

# Dated snapshot suffix, e.g. "-20251001" on "claude-haiku-4-5-20251001".
# Anthropic responses (and batch results) can carry the dated id while
# callers request the stable alias; strip it so both price off one row.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def normalise_model(model: str) -> str:
    """Drop a trailing -YYYYMMDD snapshot suffix to reach the stable alias."""
    return _DATE_SUFFIX.sub("", model)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client: Any | None = None

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
    ) -> Completion:
        client = self._sdk_client()
        tool_dicts = [_tool_to_anthropic(t) for t in tools]
        if hosted_code is not None:
            # First, so the cache breakpoint stays on the last tool that varies by agent.
            tool_dicts.insert(0, dict(CODE_TOOL))
        kwargs = self._request_kwargs(
            system=system,
            messages=[_msg_to_anthropic(m) for m in messages],
            tools=tool_dicts,
            model=model,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
        )
        if hosted_code is not None and hosted_code.container:
            kwargs["container"] = hosted_code.container
        started = time.monotonic()
        try:
            raw = client.messages.create(**kwargs)
        except Exception as exc:
            raise ProviderError(f"anthropic call failed: {exc}") from exc
        completion = self._completion_from_raw(raw, fallback_model=model)
        if hosted_code is None:
            return completion
        cost = _container_usd(completion.container, hosted_code.container, time.monotonic() - started)
        return replace(completion, usage=replace(completion.usage, hosted_code_usd=cost))

    def price_for(self, model: str) -> ModelPrice | None:
        price = PRICES.get(model)
        if price is None:
            price = PRICES.get(normalise_model(model))
        return price

    # --- hosted code: Files API -------------------------------------------

    def upload_file(self, *, name: str, media_type: str, data: bytes, expires_in_s: int) -> str:
        """Upload an input; `expires_in_s` is the backstop should the run die before it deletes the file."""
        try:
            return str(self._file_client().files.upload(file=(name, data, media_type), expires_in_seconds=expires_in_s).id)
        except Exception as exc:
            raise ProviderError(f"anthropic file upload failed: {exc}") from exc

    def input_block(self, file_id: str) -> Block:
        return OpaqueBlock(provider=self.name, data={"type": "container_upload", "file_id": file_id})

    def download_file(self, file_id: str, *, max_bytes: int) -> HostedFile:
        """A produced file as it came, its name and type unchecked; refused whole past `max_bytes`, whatever its metadata says."""
        files = self._file_client().files
        try:
            meta = files.retrieve_metadata(file_id)
            if meta.size_bytes > max_bytes:
                raise ProviderError(f"file {file_id} is {meta.size_bytes} bytes, over {max_bytes} bytes")
            data = bytearray()
            with files.with_streaming_response.download(file_id) as response:
                for chunk in response.iter_bytes():
                    data += chunk
                    if len(data) > max_bytes:
                        raise ProviderError(f"file {file_id} runs over {max_bytes} bytes")
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"anthropic file download failed: {exc}") from exc
        return HostedFile(name=str(meta.filename), media_type=str(meta.mime_type), data=bytes(data))

    def delete_file(self, file_id: str) -> None:
        try:
            self._file_client().files.delete(file_id)
        except Exception as exc:
            raise ProviderError(f"anthropic file delete failed: {exc}") from exc

    def container_cost_usd(self, seconds: float) -> float:
        return _container_usd("new", None, seconds)

    # --- Message Batches API ----------------------------------------------

    def build_batch_request_params(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        """Build one batch request's `params` from Anthropic-shaped dicts.

        `messages` / `tools` are already in Anthropic content-block /
        tool-schema form (what `toolkit.condition_scoring.build_scoring_request`
        emits), so no neutral-type round-trip is needed. The cache_control
        placement matches `complete()` exactly, so the shared system+tools
        prefix is a cache hit across every request in the batch.
        """
        return self._request_kwargs(
            system=system,
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
        )

    def submit_batch(self, items: list[tuple[str, dict[str, Any]]]) -> str:
        """Create a Message Batch. `items` is [(custom_id, params), ...].

        Bounded retry on TRANSIENT create failures (5xx/429/529/connection): a single
        upstream 502 used to kill the whole warm-up run after all the build work
        (2026-07-04, run 28693584770) — batch create is idempotent-safe to retry
        because nothing is recorded until it returns an id."""
        if not items:
            raise ProviderError("submit_batch called with no requests")
        client = self._sdk_client()
        requests = [
            {"custom_id": custom_id, "params": params}
            for custom_id, params in items
        ]
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                batch = client.messages.batches.create(requests=requests)
                return str(batch.id)
            except Exception as exc:  # noqa: BLE001 — SDK raises many types; filter below
                last_exc = exc
                status = getattr(exc, "status_code", None)
                transient = (status in (429, 500, 502, 503, 504, 529)
                             or type(exc).__name__ in ("APIConnectionError", "APITimeoutError"))
                if not transient or attempt == 2:
                    raise ProviderError(f"anthropic batch create failed: {exc}") from exc
                wait = 2.0 * (2 ** attempt)
                LOG.warning("batch create transient failure (%s); retry %d/2 in %.0fs",
                            exc, attempt + 1, wait)
                time.sleep(wait)
        raise ProviderError(f"anthropic batch create failed: {last_exc}") from last_exc

    def poll_batch(self, provider_batch_id: str) -> BatchStatus:
        client = self._sdk_client()
        try:
            batch = client.messages.batches.retrieve(provider_batch_id)
        except Exception as exc:
            raise ProviderError(f"anthropic batch retrieve failed: {exc}") from exc
        raw_status = str(getattr(batch, "processing_status", "") or "")
        rc = getattr(batch, "request_counts", None)
        counts: dict[str, int] = {}
        if rc is not None:
            for key in ("processing", "succeeded", "errored", "canceled", "expired"):
                counts[key] = int(_attr(rc, key) or 0)
        return BatchStatus(
            provider_batch_id=provider_batch_id,
            ended=(raw_status == "ended"),
            raw_status=raw_status,
            counts=counts,
        )

    def iter_batch_results(
        self, provider_batch_id: str
    ) -> Iterator[BatchResultItem]:
        client = self._sdk_client()
        try:
            results = client.messages.batches.results(provider_batch_id)
        except Exception as exc:
            raise ProviderError(f"anthropic batch results failed: {exc}") from exc
        for result in results:
            custom_id = str(getattr(result, "custom_id", "") or "")
            outcome = getattr(result, "result", None)
            rtype = str(_attr(outcome, "type") or "")
            if rtype == "succeeded":
                yield BatchResultItem(
                    custom_id=custom_id,
                    status="succeeded",
                    completion=self._completion_from_raw(
                        _attr(outcome, "message"), fallback_model="",
                    ),
                )
            elif rtype == "errored":
                yield BatchResultItem(
                    custom_id=custom_id,
                    status="errored",
                    error=str(_attr(outcome, "error") or "errored"),
                )
            else:
                yield BatchResultItem(
                    custom_id=custom_id,
                    status=rtype if rtype in ("canceled", "expired") else "errored",
                    error=rtype or "unknown",
                )

    # --- internals --------------------------------------------------------

    def _request_kwargs(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if tool_choice:
            # Force the named tool: guarantees a tool_use block instead of a
            # prose answer the caller can't parse (and can't cache against).
            kwargs["tool_choice"] = {"type": "tool", "name": tool_choice}
        if system:
            # Wrap as a list-of-blocks with cache_control so the system
            # prompt becomes part of Anthropic's cached prefix. String
            # form is NOT cache-eligible. Anthropic silently no-ops
            # cache_control when the prefix is below the model's minimum,
            # so this is safe to set unconditionally.
            kwargs["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
        if tools:
            tool_dicts = [dict(t) for t in tools]
            # One cache breakpoint at the end of tools captures
            # system + tools as a single cached prefix.
            tool_dicts[-1] = {**tool_dicts[-1], "cache_control": {"type": "ephemeral"}}
            kwargs["tools"] = tool_dicts
        return kwargs

    def _completion_from_raw(self, raw: Any, *, fallback_model: str) -> Completion:
        text_blocks, tool_calls, blocks = _split_anthropic_content(raw)
        hosted_calls, file_ids = _hosted_work(raw)
        usage = _extract_anthropic_usage(raw)
        stop_reason = _normalise_stop_reason(getattr(raw, "stop_reason", None))
        return Completion(
            text_blocks=text_blocks,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            model=getattr(raw, "model", fallback_model) or fallback_model,
            raw=raw,
            blocks=blocks,
            hosted_calls=hosted_calls,
            container=_container_id(raw),
            file_ids=file_ids,
        )

    def _sdk_client(self) -> Any:
        if not self._api_key:
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set; cannot call Anthropic"
            )
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _file_client(self) -> Any:
        return self._sdk_client().with_options(timeout=_FILE_TIMEOUT_S)


# --- block conversions ----------------------------------------------------

def _msg_to_anthropic(msg: Message) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for block in msg.content:
        own = written_by(block, AnthropicProvider.name)
        if own is not None:
            content.append(own)
        elif isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            content.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
        elif isinstance(block, ToolResultBlock):
            entry: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.content,
            }
            if block.is_error:
                entry["is_error"] = True
            content.append(entry)
        elif isinstance(block, ImageBlock):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.media_type,
                    "data": block.data,
                },
            })
    return {"role": msg.role, "content": content}


def _tool_to_anthropic(tool: ToolSchema) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _split_anthropic_content(raw: Any) -> tuple[list[str], list[ToolCall], list[Block]]:
    """The reply's text, its calls and all of it in order, every block as the SDK parsed it (thinking and its signature,
    #2068's server tools): the API refuses a later request whose earlier turns were edited."""
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    reply: list[Block] = []
    for block in getattr(raw, "content", None) or []:
        kind = _attr(block, "type")
        own = OpaqueBlock(provider=AnthropicProvider.name, data=block)
        if kind == "text":
            text = _attr(block, "text") or ""
            texts.append(text)
            if text:
                reply.append(TextBlock(text=text, opaque=own))
        elif kind == "tool_use":
            call = ToolCall(
                id=str(_attr(block, "id") or ""),
                name=str(_attr(block, "name") or ""),
                input=_attr(block, "input") or {},
            )
            tool_calls.append(call)
            reply.append(ToolUseBlock(id=call.id, name=call.name, input=call.input, opaque=own))
        else:
            reply.append(own)
    return texts, tool_calls, reply


def _hosted_work(raw: Any) -> tuple[tuple[ToolCall, ...], tuple[str, ...]]:
    """The commands the API ran itself in this reply and the ids of the files they left."""
    calls: list[ToolCall] = []
    file_ids: list[str] = []
    for block in getattr(raw, "content", None) or []:
        kind = _attr(block, "type")
        if kind == "server_tool_use":
            calls.append(ToolCall(id=str(_attr(block, "id") or ""), name=str(_attr(block, "name") or ""),
                                  input=_attr(block, "input") or {}))
        elif kind in _FILE_RESULTS:
            # An error result has no inner `content`.
            for entry in _attr(_attr(block, "content"), "content") or []:
                if file_id := _attr(entry, "file_id"):
                    file_ids.append(str(file_id))
    return tuple(calls), tuple(file_ids)


def _container_id(raw: Any) -> str | None:
    container_id = _attr(getattr(raw, "container", None), "id")
    return container_id if isinstance(container_id, str) else None


def _container_usd(opened: str | None, resumed: str | None, seconds: float) -> float:
    """What a call's container time costs at most: nothing with no container, the minimum when it is a new one."""
    if opened is None:
        return 0.0
    billed = seconds if opened == resumed else max(seconds, CONTAINER_MINIMUM_S)
    return round(billed * CONTAINER_USD_PER_HOUR / 3600, 6)


def _extract_anthropic_usage(raw: Any) -> Usage:
    usage = getattr(raw, "usage", None)
    if usage is None:
        return Usage()
    def _g(name: str) -> int:
        if isinstance(usage, dict):
            return int(usage.get(name, 0) or 0)
        return int(getattr(usage, name, 0) or 0)
    code_requests = _attr(_attr(usage, "server_tool_use"), "code_execution_requests")
    return Usage(
        input_tokens=_g("input_tokens"),
        output_tokens=_g("output_tokens"),
        cache_read_tokens=_g("cache_read_input_tokens"),
        cache_write_tokens=_g("cache_creation_input_tokens"),
        code_execution_requests=code_requests if isinstance(code_requests, int) else 0,
    )


def _normalise_stop_reason(raw: Any) -> str:
    # Anthropic also uses "stop_sequence"; "pause_turn" comes only from a request that declared a server tool.
    if raw == "stop_sequence":
        return "stop"
    if raw in ("end_turn", "tool_use", "max_tokens", "refusal", "model_context_window_exceeded", "pause_turn"):
        return raw
    return "end_turn"


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
