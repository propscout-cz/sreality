"""GitHub's workflow_dispatch POST, for the price-stats "run now" route; the caller keeps its own env and
judges the answer. The worker dispatches nothing since its count-probe lane went (#2015)."""

from __future__ import annotations

from typing import Any

import requests


def post_workflow_dispatch(
    repo: str, workflow: str, token: str, body: dict[str, Any], *, timeout: float,
) -> requests.Response:
    return requests.post(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        json=body, timeout=timeout,
    )
