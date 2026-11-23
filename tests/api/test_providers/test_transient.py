"""Which provider failures may be sent again: only those that spent nothing, whatever SDK raised them."""

from __future__ import annotations

import pytest

from api.providers import ProviderError, is_transient


class _Sdk(Exception):
    def __init__(self, **attrs: object) -> None:
        super().__init__("x")
        self.__dict__.update(attrs)


class APIConnectionError(Exception):
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
])
def test_a_provider_refusing_before_it_works_is_transient(error: ProviderError) -> None:
    assert is_transient(error)


@pytest.mark.parametrize("error", [
    ProviderError("ANTHROPIC_API_KEY is not set"), ProviderError("x", status=400), ProviderError("x", status=401),
    _from(_Sdk(status_code=404)), _from(_Sdk(code=400)), _from(ValueError("x")),
])
def test_a_failure_retrying_cannot_mend_is_not(error: ProviderError) -> None:
    assert not is_transient(error)


@pytest.mark.parametrize("error", [
    ProviderError("x", status=500), ProviderError("x", status=502), ProviderError("x", status=504),
    _from(APIConnectionError()), _from(ReadTimeout()), _from(ConnectionResetError()),
])
def test_a_failure_that_may_have_billed_is_not_since_the_ledger_could_not_see_a_retrys_spend(error: ProviderError) -> None:
    assert not is_transient(error)
