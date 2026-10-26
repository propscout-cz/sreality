"""API_AUTH_OPTIONAL=1 opens every require_token route to anonymous callers; it is a local-dev switch,
and the API refuses to start with it on Railway, where RAILWAY_ENVIRONMENT is always set (M0, #2059)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api import main as api_main  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_QUIET = {"STUCK_ROW_SWEEP_DISABLED": "1", "HEALTH_TELLER_DISABLED": "1"}


def test_startup_refuses_the_switch_on_railway(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {**_QUIET, "API_AUTH_OPTIONAL": "1", "RAILWAY_ENVIRONMENT": "production"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match="API_AUTH_OPTIONAL"):
        with TestClient(api_main.app):
            pass


@pytest.mark.parametrize("env", [{"API_AUTH_OPTIONAL": "1"}, {"RAILWAY_ENVIRONMENT": "production"}])
def test_startup_runs_with_either_alone(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    for k, v in {**_QUIET, **env}.items():
        monkeypatch.setenv(k, v)
    with TestClient(api_main.app) as client:
        assert client.get("/health").status_code == 200


def test_the_server_process_exits() -> None:
    """The real runner: uvicorn treats a failed lifespan startup as fatal and exits non-zero before it
    binds a port, so Railway sees a crashed deploy instead of an open API."""
    env = {**os.environ, **_QUIET, "API_AUTH_OPTIONAL": "1", "RAILWAY_ENVIRONMENT": "production"}
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", "0", "--lifespan", "on"],
        cwd=_REPO, env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0
    assert "API_AUTH_OPTIONAL" in proc.stderr
