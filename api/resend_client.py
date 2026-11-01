"""Resend's send call: one email, no retry. A failure is `http_json.HttpJsonError`, which names no address or key."""

from __future__ import annotations

import os

from api import http_json

URL = "https://api.resend.com/emails"
TIMEOUT = (5.0, 15.0)


def is_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY", "").strip())


def send(*, sender: str, to: str, reply_to: str, subject: str, html: str, text: str,
         headers: dict[str, str] | None = None) -> str:
    """Send one email; answers Resend's id for it."""
    body = {"from": sender, "to": [to], "reply_to": reply_to, "subject": subject, "html": html, "text": text,
            "headers": headers or {}}
    key = os.environ["RESEND_API_KEY"].strip()
    answer = http_json.fetch_json("POST", URL, json=body, timeout=TIMEOUT, headers={"Authorization": f"Bearer {key}"})
    return str(answer.get("id", ""))
