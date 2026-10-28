"""R2Client's optional timeouts (#2032): unset, every caller keeps botocore's defaults (a 60 s read, legacy retries);
the clip job's short ones bound a stalled GET to about 2 × (5 + 20) s."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("boto3")

from scraper import image_storage


def _config(**kwargs: Any) -> Any:
    client = image_storage.R2Client("acct123", "AKIAEXAMPLE", "secretexample", "bucket", **kwargs)
    return client._client.meta.config


def test_the_defaults_are_botocores() -> None:
    config = _config()
    assert (config.connect_timeout, config.read_timeout) == (60, 60)
    assert config.retries in (None, {"mode": "legacy"}), "a client resolves the unset retries to legacy mode"
    assert config.max_pool_connections == 32


def test_short_timeouts_and_attempts_reach_botocore() -> None:
    config = _config(max_pool_connections=20, connect_timeout_s=5, read_timeout_s=20, max_attempts=2)
    assert (config.connect_timeout, config.read_timeout) == (5, 20)
    assert config.retries == {"total_max_attempts": 2, "mode": "standard"}
    assert config.max_pool_connections == 20


def test_from_env_passes_them_through(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in image_storage.R2_ENV_VARS:
        monkeypatch.setenv(name, "x")
    config = image_storage.R2Client.from_env(max_pool_connections=12, connect_timeout_s=5, read_timeout_s=20,
                                             max_attempts=2)._client.meta.config
    assert (config.connect_timeout, config.read_timeout, config.max_pool_connections) == (5, 20, 12)
    assert config.retries == {"total_max_attempts": 2, "mode": "standard"}
