"""The waitlist's emails (docs/design/email/PROGRAM.md § 4): sender, words and the one-click unsubscribe headers."""

from __future__ import annotations

import html
import uuid

SENDER = "PropScout <tym@novinky.propscout.cz>"
REPLY_TO = "support@propscout.cz"
API_ORIGIN = "https://api.propscout.cz"
SIGNUP_URL = "https://app.propscout.cz/login?signup=1"

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


INVITATION_SUBJECT = "Jste na řadě: vaše pozvánka do PropScoutu"
_INVITATION_PREVIEW = "Nastavte hledání jednou a nové byty vám přijdou do mobilu."
_INVITATION_INTRO = (
    "Dobrý den,",
    "jste na řadě. Nastavte lokalitu, dispozici a cenu jednou. PropScout pak hlídá Sreality, Bazoš, Bezrealitky a dalších "
    "6 portálů a každý nový vhodný byt i zlevnění vám pošle do mobilu.",
)
_INVITATION_ADDRESS = "Účet si založte s touto adresou: {email}. Přes Google to funguje jen tehdy, když je to stejná adresa."
_INVITATION_PRICE = "Zaváděcí cenu 399 Kč měsíčně včetně DPH máte rezervovanou. Dnes nic neplatíte."
_INVITATION_STEPS = (
    "Vytvořte si účet.",
    "Přidejte si PropScout na plochu telefonu. Na iPhonu v Safari: Sdílet → Přidat na plochu.",
    "Nastavte první hlídání.",
)
_INVITATION_HELP = "Nebude něco jasné? Stačí odpovědět na tento e-mail, čteme to my."


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


def invitation_html(token: uuid.UUID, email: str) -> str:
    intro = "".join(f"<p>{p}</p>" for p in _INVITATION_INTRO)
    steps = "".join(f"<li>{s}</li>" for s in _INVITATION_STEPS)
    return (
        f'<div style="display:none;max-height:0;overflow:hidden">{_INVITATION_PREVIEW}</div>'
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.5;color:#222;'
        f'max-width:520px;margin:0 auto;padding:24px">{intro}'
        f"<p>{_INVITATION_ADDRESS.format(email=html.escape(email))}</p>"
        f'<p style="margin:28px 0"><a href="{SIGNUP_URL}" style="background:#222;color:#fff;text-decoration:none;'
        'padding:12px 20px;border-radius:6px;display:inline-block">Vytvořit účet</a></p>'
        f"<p>{_INVITATION_PRICE}</p><p>Jak začít:</p><ol>{steps}</ol><p>{_INVITATION_HELP}</p>"
        f'<p><a href="{unsubscribe_url(token)}">Odhlásit se</a></p>'
        f'<p style="color:#777;font-size:13px;margin-top:32px">{_OPERATOR}</p></div>'
    )


def invitation_text(token: uuid.UUID, email: str) -> str:
    steps = "\n".join(f"{n}. {s}" for n, s in enumerate(_INVITATION_STEPS, 1))
    return "\n\n".join((
        *_INVITATION_INTRO, _INVITATION_ADDRESS.format(email=email), f"Vytvořit účet: {SIGNUP_URL}", _INVITATION_PRICE,
        f"Jak začít:\n{steps}", _INVITATION_HELP, f"Odhlásit se: {unsubscribe_url(token)}", _OPERATOR,
    ))
