"""The waitlist's emails (docs/design/email/PROGRAM.md § 4): sender, words and the one-click unsubscribe headers."""

from __future__ import annotations

import uuid

from api import email_layout
from api.email_layout import API_ORIGIN

SENDER = "PropScout <tym@novinky.propscout.cz>"
REPLY_TO = "support@propscout.cz"

SUBJECT = "Máte místo na čekací listině PropScoutu"
_PREVIEW = "Zaváděcí cenu máte rezervovanou."
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
    return email_layout.one_click_headers(unsubscribe_url(token))


def confirmation_html(token: uuid.UUID) -> str:
    paragraphs = "".join(f"<p>{p}</p>" for p in _PARAGRAPHS)
    return email_layout.page(
        _PREVIEW, f'{paragraphs}<p>Nepřihlásili jste se vy? <a href="{unsubscribe_url(token)}">Odhlásit se</a></p>')


def confirmation_text(token: uuid.UUID) -> str:
    return "\n\n".join((*_PARAGRAPHS, f"Nepřihlásili jste se vy? Odhlásit se: {unsubscribe_url(token)}",
                        email_layout.OPERATOR))
