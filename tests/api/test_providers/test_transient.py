"""Which provider failures are worth the same request again, whatever SDK raised them."""

from __future__ import annotations

import pytest

from api.providers import ProviderError, is_transient


class _Sdk(Exception):
    def __init__(self, **attrs: object) -> None:
        super().__init__("x")
        self.__dict__.update(attrs)


class APIConnectionError(Exception):
    pass


class APITimeoutError(APIConnectionError):
    pass


class ReadTimeout(Exception):
    pass


def _from(cause: Exception) -> ProviderError:
    error = ProviderError("failed")
    error.__cause__ = cause
    return error


@pytest.mark.parametrize("error", [
    ProviderError("x", status=429), ProviderError("x", status=503), ProviderError("x", status=529),
    _from(_Sdk(status_code=429)), _from(_Sdk(status_code=529)), _from(_Sdk(code=503)),
    _from(APIConnectionError()), _from(APITimeoutError()), _from(ReadTimeout()), _from(ConnectionResetError()),
])
def test_rate_limits_overloads_and_broken_connections_are_transient(error: ProviderError) -> None:
    assert is_transient(error)


@pytest.mark.parametrize("error", [
    ProviderError("ANTHROPIC_API_KEY is not set"), ProviderError("x", status=400), ProviderError("x", status=401),
    _from(_Sdk(status_code=404)), _from(_Sdk(code=400)), _from(ValueError("x")),
])
def test_everything_else_is_not(error: ProviderError) -> None:
    assert not is_transient(error)
