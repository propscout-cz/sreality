"""The price-stats "run now" dispatch: its own env (GH_DISPATCH_TOKEN, GH_REPO) through the GitHub
dispatch POST it shares with the worker's count-probe (scraper.github_dispatch)."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from fastapi import HTTPException

from api import price_stats


class _Cur:
    def __enter__(self) -> "_Cur":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, *args: Any) -> None:
        pass

    def fetchone(self) -> tuple[int]:
        return (1,)


class _Conn:
    def cursor(self) -> _Cur:
        return _Cur()


class _Resp:
    def __init__(self, code: int) -> None:
        self.status_code, self.text = code, "refused"


def test_run_now_posts_the_dispatch_with_its_own_repo_token_body_and_timeout(monkeypatch):
    monkeypatch.setenv("GH_DISPATCH_TOKEN", "tok")
    monkeypatch.setenv("GH_REPO", "o/r")
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: calls.append((url, kw)) or _Resp(204))
    assert price_stats.run_dataset_now(_Conn(), 7) == {
        "dispatched": True, "run_url": "https://github.com/o/r/actions/workflows/scrape_price_stats.yml",
    }
    assert calls == [(
        "https://api.github.com/repos/o/r/actions/workflows/scrape_price_stats.yml/dispatches",
        {
            "headers": {
                "Authorization": "Bearer tok",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            "json": {"ref": "main", "inputs": {"mode": "full", "dataset_id": "7"}},
            "timeout": 15,
        },
    )]

    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp(422))
    with pytest.raises(HTTPException) as err:
        price_stats.run_dataset_now(_Conn(), 7)
    assert err.value.status_code == 502 and "422" in str(err.value.detail)


def test_run_now_without_a_token_is_a_503_and_posts_nothing(monkeypatch):
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("posted without a token"))
    with pytest.raises(HTTPException) as err:
        price_stats.run_dataset_now(_Conn(), 7)
    assert err.value.status_code == 503
