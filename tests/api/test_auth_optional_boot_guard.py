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

from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402

_REPO = Path(__file__).resolve().parents[2]
_QUIET = {"STUCK_ROW_SWEEP_DISABLED": "1", "HEALTH_TELLER_DISABLED": "1"}


def _railway_free(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*deps._RAILWAY_MARKERS, "API_AUTH_OPTIONAL"):
        monkeypatch.delenv(name, raising=False)
    for k, v in _QUIET.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("marker", deps._RAILWAY_MARKERS)
def test_startup_refuses_the_switch_on_railway(monkeypatch: pytest.MonkeyPatch, marker: str) -> None:
    """Railway documents RAILWAY_ENVIRONMENT_NAME / _ID, RAILWAY_PROJECT_ID, RAILWAY_SERVICE_ID; the spec names
    RAILWAY_ENVIRONMENT. Any one of them is Railway."""
    _railway_free(monkeypatch)
    monkeypatch.setenv("API_AUTH_OPTIONAL", "1")
    monkeypatch.setenv(marker, "production")
    with pytest.raises(RuntimeError, match=rf"API_AUTH_OPTIONAL=1 .*\({marker} is set\)"):
        with TestClient(api_main.app):
            pass


@pytest.mark.parametrize("env", [{"API_AUTH_OPTIONAL": "1"}, {"RAILWAY_ENVIRONMENT_NAME": "production"}])
def test_startup_runs_with_either_alone(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    _railway_free(monkeypatch)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with TestClient(api_main.app) as client:
        assert client.get("/health").status_code == 200


def test_the_server_process_exits() -> None:
    """The real runner: uvicorn treats a failed lifespan startup as fatal and exits non-zero before it
    binds a port, so Railway sees a crashed deploy instead of an open API."""
    env = {**{k: v for k, v in os.environ.items() if k not in deps._RAILWAY_MARKERS}, **_QUIET,
           "API_AUTH_OPTIONAL": "1", "RAILWAY_ENVIRONMENT_NAME": "production"}
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", "0", "--lifespan", "on"],
        cwd=_REPO, env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0
    assert "API_AUTH_OPTIONAL" in proc.stderr
