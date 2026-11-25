"""R2Client's optional timeouts (#2032): unset, every caller keeps a 60 s read and legacy retries (pinned: botocore
1.44 made standard its default); the clip job's short ones bound a stalled GET to about 2 × (5 + 20) s."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("boto3")

from scraper import image_storage


def _config(**kwargs: Any) -> Any:
    client = image_storage.R2Client("acct123", "AKIAEXAMPLE", "secretexample", "bucket", **kwargs)
    return client._client.meta.config


def test_unset_keeps_a_60_s_read_and_legacy_retries() -> None:
    config = _config()
    assert (config.connect_timeout, config.read_timeout) == (60, 60)
    assert config.retries == {"mode": "legacy"}, "unset attempts pin legacy retries, whatever botocore defaults to"
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
