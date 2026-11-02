"""A run's produced files in Supabase Storage (ADR-0006): stored by the executor, served by the run route, swept when no row wants them."""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
import requests
from fastapi import Response

from api import attachments
from api.agents.hosted_code import safe_name
from api.providers import HostedFile

LOG = logging.getLogger(__name__)

BUCKET = "agent-files"
STORED_TYPE = "application/octet-stream"
# An answer is already paid for, so storing it may not hold a run past its lease's heartbeat (executor.HEARTBEAT_CEILING_S:
# 900 s, against 600 s at most of wall clock).
STORE_BUDGET_S = 120.0
_TIMEOUT_S = (10, 60)
_TRIES = 2
# Past HEARTBEAT_CEILING_S: an object this old with no row has no worker left that could still write its row.
ORPHAN_AGE_S = 3_600
SWEEP_EVERY_S = 3_600
SWEEP_BATCH = 100

_monotonic = time.monotonic
_next_sweep = 0.0
_ORPHANS = """
SELECT o.name FROM storage.objects o
 WHERE o.bucket_id = %s AND o.created_at < now() - make_interval(secs => %s)
   AND NOT EXISTS (SELECT 1 FROM agent_artifacts a WHERE a.storage_key = o.name)
 ORDER BY o.created_at LIMIT %s"""


class StoreError(Exception):
    """The store refused or could not be reached. Its words carry a status, never a key or a credential."""


class FileStore(Protocol):
    """The bucket: nothing about an object comes from the model. Its key is ours and its stored type is bare bytes."""

    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, keys: Sequence[str]) -> None: ...


class SupabaseStore:
    """Storage's REST API under the project's secret key: server-side only, and never in a browser (no `VITE_` copy exists)."""

    def __init__(self, url: str, key: str) -> None:
        self._base = f"{url.rstrip('/')}/storage/v1/object/{BUCKET}"
        # `apikey` alone: Supabase refuses a secret key on `Authorization: Bearer`, since it is not a JWT.
        self._auth = {"apikey": key}

    def _call(self, method: str, url: str, *, headers: Mapping[str, str] | None = None, **body: Any) -> requests.Response:
        try:
            return requests.request(method, url, headers={**self._auth, **(headers or {})}, timeout=_TIMEOUT_S, **body)
        except requests.RequestException as exc:
            raise StoreError(f"storage unreachable: {type(exc).__name__}") from None

    def put(self, key: str, data: bytes) -> None:
        """A repeat replaces: a key names one attempt's one set of bytes, so a retry writes what is already there."""
        response = self._call("POST", f"{self._base}/{key}", data=data,
                              headers={"Content-Type": STORED_TYPE, "x-upsert": "true"})
        if not response.ok:
            raise StoreError(f"storage answered {response.status_code} to an upload")

    def get(self, key: str) -> bytes | None:
        response = self._call("GET", f"{self._base}/{key}")
        if response.ok:
            return response.content
        if _is_missing(response):
            return None
        raise StoreError(f"storage answered {response.status_code} to a read")

    def delete(self, keys: Sequence[str]) -> None:
        response = self._call("DELETE", self._base, json={"prefixes": list(keys)})
        if not response.ok:
            raise StoreError(f"storage answered {response.status_code} to a delete")


def _is_missing(response: requests.Response) -> bool:
    """Storage has answered a missing object both ways: a 404, and a 400 with the 404 in its body."""
    if response.status_code != 400:
        return response.status_code == 404
    try:
        return str(response.json().get("statusCode")) == "404"
    except ValueError:
        return False


def store() -> FileStore | None:
    """The bucket, or None on a service that holds no key: a run then marks its files, and the route answers 503."""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    return SupabaseStore(url, key) if url and key else None


@dataclass(frozen=True)
class StoredFile:
    """One file of an accepted answer; `storage_key` is None when its bytes could not be kept."""

    name: str
    media_type: str
    size_bytes: int
    sha256: str
    storage_key: str | None


def _put(target: FileStore, key: str, data: bytes) -> str | None:
    for attempt in range(1, _TRIES + 1):
        try:
            target.put(key, data)
            return key
        except StoreError as exc:
            LOG.warning("AGENT_FILE upload failed key=%s try=%s: %s", key, attempt, exc)
        except Exception:  # noqa: BLE001 - whatever the store does, the answer it holds is paid for
            LOG.exception("AGENT_FILE store broke key=%s", key)
            return None
    return None


def store_files(files: Sequence[HostedFile], *, target: FileStore | None, account_id: uuid.UUID, run_id: int,
                attempt: int) -> list[StoredFile]:
    """Upload an accepted answer's files, each under a key of ours. Never raises: a file that cannot be kept is marked and
    the run still succeeds, since its answer is paid for and the provider has already dropped the bytes."""
    if target is None and files:
        LOG.error("AGENT_FILE no store on this service: run=%s keeps %s file(s) as names only", run_id, len(files))
    deadline = _monotonic() + STORE_BUDGET_S
    stored = []
    for file in files:
        sha = hashlib.sha256(file.data).hexdigest()
        key = None
        if target is not None:
            if _monotonic() < deadline:
                key = _put(target, f"{account_id}/{run_id}/{attempt}/{sha}", file.data)
            else:
                LOG.warning("AGENT_FILE out of time run=%s attempt=%s sha=%s: kept as a name only", run_id, attempt, sha)
        stored.append(StoredFile(file.name, file.media_type, len(file.data), sha, key))
    return stored


def file_response(data: bytes, media_type: str, name: str) -> Response:
    """The one place a produced file meets a browser: always a download, never sniffed, and typed as itself only when
    that type is on the list of inert ones the attachment store already serves. HTML, SVG and script are not on it. Never
    cached: the URL is the same for every login on a browser, and only the row's RLS may answer it."""
    plain = media_type.split(";")[0].strip().lower()
    response = attachments.file_response(data, plain if plain in attachments.ALLOWED else STORED_TYPE, safe_name(name))
    response.headers["Cache-Control"] = "no-store"
    return response


def sweep(conn: psycopg.Connection) -> None:
    """Remove objects no row wants, at most hourly: an attempt that lost its run left them, or an account's deletion
    cascaded their rows away (nothing else reaches the bucket). Never raises: a run's claim must not depend on it."""
    global _next_sweep
    target = store()
    if target is None or _monotonic() < _next_sweep:
        return
    _next_sweep = _monotonic() + SWEEP_EVERY_S
    try:
        names = [row[0] for row in conn.execute(_ORPHANS, (BUCKET, ORPHAN_AGE_S, SWEEP_BATCH)).fetchall()]
        if names:
            target.delete(names)
            LOG.info("AGENT_FILE asked storage to remove %s orphan(s)", len(names))
    except (StoreError, psycopg.Error) as exc:
        LOG.warning("AGENT_FILE sweep failed, next hour tries again: %s", exc)
