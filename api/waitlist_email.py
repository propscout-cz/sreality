"""The waitlist's emails (docs/design/email/PROGRAM.md § 4): sender, words and the one-click unsubscribe headers."""

from __future__ import annotations

import uuid

SENDER = "PropScout <tym@novinky.propscout.cz>"
REPLY_TO = "support@propscout.cz"
API_ORIGIN = "https://api.propscout.cz"

SUBJECT = "Máte místo na čekací listině PropScoutu"
_PREVIEW = "Zaváděcí cenu máte rezervovanou."
_OPERATOR = "PropScout.cz · Provozovatel: Limen Ventures s.r.o., IČO 14053179"
_PARAGRAPHS = (
    "Dobrý den,",
    "děkujeme, jste na čekací listině. Zaváděcí cenu 399 Kč měsíčně včetně DPH máte rezervovanou.",
    "Pozvánky posíláme postupně, v pořadí, v jakém se lidé přihlásili. Ta vaše přijde na tuto adresu, i s krátkým "
    "návodem, jak si PropScout přidat na plochu telefonu. Přidejte si nás do kontaktů, ať vám neunikne.",
    "Hledá investiční byt i někdo z vašich známých? Pošlete mu odkaz: propscout.cz",
)


def unsubscribe_url(token: uuid.UUID) -> str:
    return f"{API_ORIGIN}/waitlist/unsubscribe?t={token}"


def unsubscribe_headers(token: uuid.UUID) -> dict[str, str]:
    """RFC 8058: a mail client's own unsubscribe button POSTs to this address."""
    return {"List-Unsubscribe": f"<{unsubscribe_url(token)}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def confirmation_html(token: uuid.UUID) -> str:
    paragraphs = "".join(f"<p>{p}</p>" for p in _PARAGRAPHS)
    return (
        f'<div style="display:none;max-height:0;overflow:hidden">{_PREVIEW}</div>'
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.5;color:#222;'
        f'max-width:520px;margin:0 auto;padding:24px">{paragraphs}'
        f'<p>Nepřihlásili jste se vy? <a href="{unsubscribe_url(token)}">Odhlásit se</a></p>'
        f'<p style="color:#777;font-size:13px;margin-top:32px">{_OPERATOR}</p></div>'
    )


def confirmation_text(token: uuid.UUID) -> str:
    return "\n\n".join((*_PARAGRAPHS, f"Nepřihlásili jste se vy? Odhlásit se: {unsubscribe_url(token)}", _OPERATOR))
