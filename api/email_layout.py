"""The frame every PropScout email shares: a hidden preview line, one left-aligned 520 px column, the operator line.

Left-aligned (`margin:0`), never centred: a centred column sits off to one side of a wide Gmail reading pane.
"""

from __future__ import annotations

import html

API_ORIGIN = "https://api.propscout.cz"
OPERATOR = "PropScout.cz · Provozovatel: Limen Ventures s.r.o., IČO 14053179"


def page(preview: str, body: str) -> str:
    """`body` is HTML the caller escaped; the preview is text."""
    return (
        f'<div style="display:none;max-height:0;overflow:hidden">{html.escape(preview)}</div>'
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.5;color:#222;'
        f'max-width:520px;margin:0;padding:24px">{body}'
        f'<p style="color:#777;font-size:13px;margin-top:32px">{OPERATOR}</p></div>'
    )


def one_click_headers(url: str) -> dict[str, str]:
    """RFC 8058: a mail client's own unsubscribe button POSTs to this address."""
    return {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
