"""A run's files at the code provider: named safely, bounded in size and number, and gone from the provider when it ends."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from api.llm_client import LLMClient
from api.providers import Block, HostedCodeProvider, HostedFile, ProviderError, TextBlock

LOG = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_RUN_FILES = 10
MAX_NAME_BYTES = 255
# The floor the Files API allows, and well past the longest run: the backstop for a run that dies before it deletes.
UPLOAD_EXPIRY_S = 3_600

_UNSAFE_NAME = re.compile(r"[^\w .()-]")
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*")
_OCTET_STREAM = "application/octet-stream"


def safe_name(raw: str) -> str:
    """A model-written file name as one harmless path segment: never a directory, a hidden file or a control character."""
    base = unicodedata.normalize("NFC", re.split(r"[\\/]", raw)[-1])
    clean = _UNSAFE_NAME.sub("_", base).strip(" .") or "file"
    if len(clean.encode()) <= MAX_NAME_BYTES:
        return clean
    stem, dot, extension = clean.rpartition(".")
    tail = f".{extension}" if dot and len(extension.encode()) <= 16 else ""
    room = MAX_NAME_BYTES - len(tail.encode())
    return (stem if tail else clean).encode()[:room].decode(errors="ignore") + tail


def _safe_media_type(raw: str) -> str:
    lowered = raw.lower()
    return lowered if _MEDIA_TYPE.fullmatch(lowered) else _OCTET_STREAM


def describe(file: HostedFile) -> dict[str, Any]:
    """A file's facts, for anything that must not hold its bytes."""
    return {"name": file.name, "media_type": file.media_type, "bytes": len(file.data)}


def read_input(path: Path) -> HostedFile:
    """A local file as a run's input, for the runners."""
    return HostedFile(name=safe_name(path.name), media_type=mimetypes.guess_type(path.name)[0] or _OCTET_STREAM,
                      data=path.read_bytes())


def save(files: Sequence[HostedFile], folder: Path) -> list[Path]:
    """Write an answer's files into `folder`, for the runners: no name can land outside it."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / safe_name(file.name) for file in files]
    for path, file in zip(paths, files):
        path.write_bytes(file.data)
    return paths


class RunFiles:
    """What one run put at, and got from, the provider. Bytes are held here until the run ends; the ids go in `release`."""

    def __init__(self, llm: LLMClient, provider: str) -> None:
        self._llm = llm
        self._name = provider
        self._ids: list[str] = []
        self.produced: dict[str, HostedFile] = {}

    @property
    def _provider(self) -> HostedCodeProvider:
        return cast(HostedCodeProvider, self._llm.provider(self._name))

    def container_cost_usd(self, seconds: float) -> float:
        return self._provider.container_cost_usd(seconds)

    def upload(self, inputs: Sequence[HostedFile]) -> list[Block]:
        """Put `inputs` in the Files API; the blocks that bring them into the container, and the model the names."""
        if not inputs:
            return []
        blocks: list[Block] = []
        names: list[str] = []
        for file in inputs:
            name = safe_name(file.name)
            file_id = self._provider.upload_file(name=name, media_type=file.media_type, data=file.data,
                                                 expires_in_s=UPLOAD_EXPIRY_S)
            self._ids.append(file_id)
            blocks.append(self._provider.input_block(file_id))
            names.append(name)
        return [*blocks, TextBlock(text=f"Input files in your code container: {', '.join(names)}.")]

    def collect(self, file_id: str, *, late: bool) -> dict[str, Any]:
        """Fetch one produced file, a rerun of a name replacing the earlier one; a journal line either way, since a
        failure here must not lose the call already paid for: the model hears of it when it names the file."""
        self._ids.append(file_id)
        if late:
            return {"error": "past the run's deadline: not fetched"}
        if len(self.produced) >= MAX_RUN_FILES:
            return {"error": f"this run already holds {MAX_RUN_FILES} files: not fetched"}
        try:
            fetched = self._provider.download_file(file_id, max_bytes=MAX_FILE_BYTES)
        except ProviderError as exc:
            return {"error": str(exc)}
        file = HostedFile(name=safe_name(fetched.name), media_type=_safe_media_type(fetched.media_type), data=fetched.data)
        self.produced[file.name] = file
        return {**describe(file), "sha256": hashlib.sha256(file.data).hexdigest()}

    def release(self) -> None:
        """Delete everything this run left at the provider, which keeps a file until told otherwise (ADR-0005)."""
        for file_id in self._ids:
            try:
                self._provider.delete_file(file_id)
            except ProviderError as exc:
                LOG.warning("agent file %s stays at the provider until it is deleted by hand: %s", file_id, exc)
