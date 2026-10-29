"""The one table that turns whatever a tool raised into a ToolError a model can read and act on."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

import psycopg
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from api.dependencies import DB_BUSY_ERRORS

LOG = logging.getLogger(__name__)


class ToolErrorCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    REFUSED = "refused"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    CONFLICT = "conflict"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    RESULT_TOO_LARGE = "result_too_large"
    INTERNAL = "internal"


_RETRYABLE = frozenset({ToolErrorCode.BUSY, ToolErrorCode.UNAVAILABLE})

# The write modules' HTTP refusals, by status; any other 4xx is REFUSED and any other 5xx INTERNAL.
_HTTP_CODES = {
    400: ToolErrorCode.INVALID_ARGUMENTS, 413: ToolErrorCode.INVALID_ARGUMENTS,
    415: ToolErrorCode.INVALID_ARGUMENTS, 422: ToolErrorCode.INVALID_ARGUMENTS,
    401: ToolErrorCode.FORBIDDEN, 403: ToolErrorCode.FORBIDDEN, 404: ToolErrorCode.NOT_FOUND,
    409: ToolErrorCode.CONFLICT, 503: ToolErrorCode.UNAVAILABLE,
}


class ToolError(Exception):
    """A tool call that did not produce a result, in words the caller may read."""

    def __init__(self, code: ToolErrorCode, message: str, *, details: Sequence[Mapping[str, Any]] = ()) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, tuple(details)

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE

    def payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": str(self.code), "message": self.message, "retryable": self.retryable}
        if self.details:
            error["details"] = [dict(d) for d in self.details]
        return {"error": error}


def invalid_arguments(exc: ValidationError) -> ToolError:
    """Each refused argument by location; the input itself is never echoed back."""
    details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
               for e in exc.errors(include_url=False, include_input=False)]
    where = "; ".join(f"{'.'.join(map(str, d['loc'])) or 'arguments'}: {d['msg']}" for d in details)
    return ToolError(ToolErrorCode.INVALID_ARGUMENTS, f"invalid arguments: {where}", details=details)


def _http_message(detail: Any) -> str:
    if isinstance(detail, Mapping):
        return str(detail.get("message") or detail.get("code") or "refused")
    return str(detail)


def to_tool_error(exc: Exception, *, tool: str) -> ToolError:
    """The only mapping from a raised exception to a ToolError."""
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, HTTPException):
        code = _HTTP_CODES.get(exc.status_code, ToolErrorCode.REFUSED if exc.status_code < 500 else None)
        if code is not None:
            return ToolError(code, _http_message(exc.detail))
    elif isinstance(exc, DB_BUSY_ERRORS):
        return ToolError(ToolErrorCode.BUSY,
                         "the database is busy or the query ran past its time limit; retry or narrow the request")
    elif isinstance(exc, psycopg.errors.DataError):
        # SQLSTATE class 22: a value the database cannot apply (an interval or a number out of its range).
        LOG.warning("tool %s refused a value the database cannot apply: %s", tool, exc)
        return ToolError(ToolErrorCode.REFUSED, "a value is outside what the database can apply; narrow it")
    elif isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return ToolError(ToolErrorCode.FORBIDDEN, f"{tool} may not read or change that data")
    elif isinstance(exc, psycopg.OperationalError):
        return ToolError(ToolErrorCode.UNAVAILABLE, "the database is unavailable; retry shortly")
    elif isinstance(exc, ValueError) and not isinstance(exc, ValidationError):
        # The toolkit's bad-input contract: its ValueError text is written for the caller.
        return ToolError(ToolErrorCode.REFUSED, str(exc))
    ref = uuid.uuid4().hex[:12]
    LOG.error("tool %s failed [%s]", tool, ref, exc_info=exc)
    return ToolError(ToolErrorCode.INTERNAL, f"internal error (ref {ref})")
