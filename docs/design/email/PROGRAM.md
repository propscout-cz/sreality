# Email: password reset, the waitlist, notifications

> **Status: APPROVED 2026-10-31 (operator, in one interview). Nothing is built; PR 1 waits on the operator's Resend
> domains.** Every wave's PR updates the docs and skills it changes and `roadmap/public-release-track.md`.

**Terms.** Sign-up, waitlist entry, waitlist invitation and introductory price are defined in `GLOSSARY.md`.

## 1. Where things stand

- **Nothing can send email.** The old Resend stack sent nothing and was deleted (#1944, #1952); the code holds no
  Resend or SMTP reference. `auth.users` records no confirmation or reset email ever sent.
- **Production sign-up is off** (`disable_signup: true`, read live 2026-10-31), new Google accounts included.
  `docs/design/brand-domain-and-store/PROGRAM.md` still calls it open; W3 there is replaced by this program.
- **The waitlist is live and empty:** one test row, nobody invited.
- **The operator reports** a Resend account under a personal login, `RESEND_API_KEY` on the Railway API service
  (no code reads it yet) and the Resend connector authorized. No Resend DNS record exists yet.

## 2. Decisions

| # | Decision |
| --- | --- |
| Kinds | Four: password reset, waitlist confirmation, waitlist invitation, notification email. Launch news is not built. |
| Provider | Resend, sending from Ireland. Account data is stored in the US, and the privacy page says so. |
| Addresses | `mail.propscout.cz` for password reset and notifications; `novinky.propscout.cz` for waitlist email. The sender is `PropScout <tym@…>`. The bare domain stays Google Workspace only. |
| Replies | Reply-To `support@propscout.cz`. `/soukromi` shows `info@propscout.cz`. |
| Resend account | `info@propscout.cz` becomes a second admin. Supabase and the API each get their own sending-only key. |
| Who gets in | Only an invited address may create an account. Opening to the public is a later ruling. |
| Waitlist invitation | Permission to create an account under the waitlist entry's address, and the email saying so. It decides nothing about plans; any gate comes after login. |
| Enforcement | Supabase's Before User Created hook refuses an address with no invitation. No special link, so nothing expires. |
| Sign-in methods | Google and email/password. |
| Email confirmation | **Off.** Anyone who knows an invited address can register it: an accepted risk, revisited before public sign-up. The confirmation template is not written. |
| Waitlist confirmation | Sent on joining, no click needed; the lawyer checks that choice. Capped at 80 a day, 500 on Pro. Over the cap the entry is saved without the email. |
| Sending invitations | Admin only, one by one or in batches, from the page renamed Waitlist. Oldest first by default, but the admin may pick anyone. Unsubscribed entries cannot be invited. |
| Entry states | Waiting, invited, account created, unsubscribed. An invited entry has "Poslat znovu". No withdrawal for now. Resend's own block list handles bounces. |
| Signup-order promise | Stays on the pages, with the admin free to pick out of order. Operator ruling; on the lawyer's list. |
| Price | **Zaváděcí cena 599 Kč měsíčně včetně DPH.** The company is VAT-registered (ARES). The "nižší než ostatní" comparison goes. The app is untouched: plans, trial and gates stay open, and today's trial on a new account stays. |
| Notification setting | One account-wide choice: Vypnuto, Denní přehled (default, 7:00 Prague) or Okamžitě (at most one email per 15 minutes). It sits under the avatar on desktop and in the phone's account sheet, beside the existing appearance and push controls. The email's off link sets Vypnuto. |
| Language | Czech, including login, sign-up and password reset. |
| Resend plan | Free until ads start, invitations capped at 50 a day. Pro ($20 a month, recheck on Resend's pricing page) before the first paid ad. |
| Lawyer | Blocks the first real invitation, not the waitlist confirmation. Covers consent, privacy, the order promise, the no-click confirmation and the 599 wording. |

**Not building:** launch news, waitlist double opt-in, per-watchdog email choices, withdrawing an invitation, a Resend
webhook, plans or payment.

## 3. Build order

Each PR is usable alone.

**PR 1: sending works.** Operator, click by click: Resend domains and their DNS records in Cloudflare, Supabase custom
SMTP, the Czech reset template. Exit: a reset email reaches an inbox and shows in Resend's log. Supabase's SMTP settings
have no Reply-To, so the reset email names `support@propscout.cz` in its text instead.

**PR 2: waitlist email.** Declares `RESEND_API_KEY` in `.railway/railway.ts`: it was added in the dashboard, and an
undeclared name plans as a delete on the next apply. The confirmation from `POST /waitlist`, which never fails the signup when sending fails;
one-click unsubscribe (RFC 8058) setting `unsubscribed_at`; the privacy page (Resend as a processor, US storage, the
`info@` contact); the price and "zaváděcí" text on the landing page, thank-you page and copy deck. The deck is rebuilt
per `frontend/scripts/landing-copy-deck/README.md`. Must ship before any email goes out.

**PR 3: invitations.** The hook; the admin buttons and states setting `invited_at`; the Sign-ups → Waitlist rename; the
Czech screens, with the invitation button opening on account creation. **Test first:** that the hook covers Google
sign-ups and that hosted Supabase runs it as a database function. If either fails, fall back to creating the account
when the invitation link is clicked. **Switch-on order:** hook on, then email confirmation off, then sign-up on, so
nobody can register unchecked at any moment.

**PR 4: notification email.** The setting and the daily summary. `notification_deliveries` is keyed on (event, device)
and an email has no device, so the schema is settled here. The worker needs its own key, since its lanes run on a
separate Railway service. Updates rule 16 and the `CLAUDE.md` scope line, which still call email inactive. Comes last
because nobody can receive it until accounts exist.

## 4. Copy

Formal Czech, matching the landing page. {Braces} are placeholders; sample prices are invented. Notification lines
reuse the push wording (`api/notification_render.py`): no per-m² figure, no sender on a share.

**Password reset.** Subject: *Nastavte si nové heslo k PropScoutu*. Preview: *Odkaz platí jen krátce.*

> Dobrý den,
> přišla nám žádost o nové heslo k vašemu účtu.
> **[Nastavit nové heslo]**
> Pokud jste o změnu nežádali, nic se neděje. Vaše heslo zůstává stejné.
> Potřebujete pomoc? Napište nám na support@propscout.cz.

**Waitlist confirmation.** Subject: *Máte místo na čekací listině PropScoutu*. Preview: *Zaváděcí cenu máte
rezervovanou.*

> Dobrý den,
> děkujeme, jste na čekací listině. Zaváděcí cenu 599 Kč měsíčně včetně DPH máte rezervovanou.
> Pozvánky posíláme postupně, v pořadí, v jakém se lidé přihlásili. Ta vaše přijde na tuto adresu, i s krátkým návodem,
> jak si PropScout přidat na plochu telefonu. Přidejte si nás do kontaktů, ať vám neunikne.
> Hledá investiční byt i někdo z vašich známých? Pošlete mu odkaz: propscout.cz
> Nepřihlásili jste se vy? **[Odhlásit se]**

**Waitlist invitation.** Subject: *Jste na řadě: vaše pozvánka do PropScoutu*. Preview: *Nastavte hledání jednou a nové
byty vám přijdou do mobilu.*

> Dobrý den,
> jste na řadě. Nastavte lokalitu, dispozici a cenu jednou. PropScout pak hlídá Sreality, Bazoš, Bezrealitky a dalších
> 6 portálů a každý nový vhodný byt i zlevnění vám pošle do mobilu.
> Účet si založte s touto adresou: {email}. Přes Google to funguje jen tehdy, když je to stejná adresa.
> **[Vytvořit účet]**
> Zaváděcí cenu 599 Kč měsíčně včetně DPH máte rezervovanou. Dnes nic neplatíte.
> Jak začít:
> 1. Vytvořte si účet.
> 2. Přidejte si PropScout na plochu telefonu. Na iPhonu v Safari: Sdílet → Přidat na plochu.
> 3. Nastavte první hlídání.
>
> Nebude něco jasné? Stačí odpovědět na tento e-mail, čteme to my.
> **[Odhlásit se]**

"Dnes nic neplatíte" comes out of the template once payment exists.

**Daily summary.** Subject: *{3 nové byty a 1 zlevnění} pro vaše hledání*. Preview: *{Zlevněno · Brno – Žabovřesky:
4 490 000 Kč → 4 290 000 Kč}*

> Dobrý den,
> tohle se za posledních 24 hodin změnilo u nabídek, které hlídáte:
> **Nový inzerát · Praha 5:** Byt 2+kk · 5 150 000 Kč
> **Zlevněno · Brno – Žabovřesky:** 4 490 000 Kč → 4 290 000 Kč
> **Sdíleno s vámi:** Sbírka
> **[Otevřít v PropScoutu]**
> Tento přehled chodí jednou denně, protože máte zapnutá e-mailová upozornění. **[Vypnout]**

**Refused sign-up.**

> Tato adresa zatím nemá pozvánku. Přidejte se na čekací listinu na propscout.cz.

This reveals whether an address has been invited: accepted for now.

**Page text.**

| Where | Now | Becomes |
| --- | --- | --- |
| Every page and button | zakladatelská cena | zaváděcí cena |
| Landing button | Chci zakladatelskou cenu | Chci zaváděcí cenu |
| Offer item | Zvýhodněná cena jen pro lidi z čekací listiny. | 599 Kč měsíčně včetně DPH, jen pro lidi z čekací listiny. |
| FAQ, Kolik to bude stát? | Cenu zveřejníme před spuštěním. … | Zaváděcí cena pro lidi z čekací listiny je 599 Kč měsíčně včetně DPH. Přihlášení na čekací listinu je zdarma a k ničemu vás nezavazuje. |
| Thank-you page | Zakladatelskou cenu máte rezervovanou. | Zaváděcí cenu 599 Kč měsíčně včetně DPH máte rezervovanou. |
| Thank-you page | Do té doby vám nic dalšího nepřijde. | Kromě potvrzení vám do té doby nic dalšího nepřijde. |
