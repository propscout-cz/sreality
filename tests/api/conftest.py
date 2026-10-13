"""Every API test runs with a JWT verifier configured, as production is: the autouse
`jwks` fixture points `SUPABASE_URL` at a stub project and `deps._jwks_client` at the
per-process test key (`tests/api/_jwt.py`), so `mint({...})` yields a token
`verify_jwt` accepts. A test of the UNCONFIGURED posture deletes `SUPABASE_URL`."""

from __future__ import annotations

import pytest

from api import dependencies as deps


@pytest.fixture(autouse=True)
def jwks(monkeypatch) -> None:
    _jwt = pytest.importorskip("tests.api._jwt")  # PyJWT + cryptography (api extra)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(deps, "_jwks_client", lambda _url: _jwt.JwksClient())
