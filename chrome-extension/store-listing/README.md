# Chrome Web Store listing kit — PropScout.cz extension

Everything the operator pastes into the Chrome Web Store developer dashboard (#2279, spec #2261). Drafted
2026-11-01 from the build after #2277: permissions `storage`, `identity`, `sidePanel`; content scripts on 10 portal
hosts; no host permissions. Listing texts are Czech; the reviewer-facing fields (single purpose, justifications, test
instructions) are English, because the store's reviewers read them. **Change this file in the same PR as any
change to the manifest's permissions or sites, or to what the extension sends** (the data-use answers and
https://propscout.cz/soukromi must keep matching the build).

## Store listing tab

| Field | Value |
| --- | --- |
| Item name | from the manifest: **PropScout.cz — výnos & pipeline** (31 of 75 characters; not editable in the dashboard) |
| Summary | from the manifest `description` (130 of 132 characters; not editable in the dashboard) |
| Language | Čeština (Czech). The package has no `_locales`, so the manifest's Czech texts are the listing's. |
| Category | **Workflow & Planning** |
| Homepage URL | https://propscout.cz |
| Support URL | — (owed: a public company contact e-mail) |
| Official URL | optional; needs the domain verified in Google Search Console |
| Mature content | No |

### Detailed description (Czech)

```
PropScout.cz ukáže u inzerátu na realitním portálu to, co v samotném inzerátu nenajdete.

Otevřete nabídku na Sreality, Bezrealitkách, iDNES Reality nebo dalším podporovaném portálu a vedle ní se zobrazí panel PropScout:

• Vývoj ceny napříč portály: kdy a o kolik se cena měnila, i když je stejná nemovitost inzerovaná na více místech.
• Výnos podle cenové mapy nájemného Ministerstva financí: odhad hrubého výnosu z nájmu pro byty na prodej.
• Vaše pipeline obchodů: nemovitost jedním kliknutím přidáte do pipeline a posunete ji do další fáze.
• Kolekce, štítky a poznámky: všechno, co si k nemovitosti uložíte, uvidíte u každého jejího inzerátu.
• Odhad výnosu: u bytu na prodej na požádání spočítá odhad nájmu a výnosu z porovnatelných nabídek.

Na stránkách s výsledky hledání označí inzeráty, které už máte v pipeline nebo kolekcích, a ukáže u nich výnos.

Rozšíření potřebuje účet PropScout.cz a přihlášení účtem Google. Na náš server posílá jen portál a čísla inzerátů, které otevřete nebo které jsou na stránce s výsledky, a to, co si sami uložíte. Mimo podporované realitní portály nic nedělá. Podrobnosti: https://propscout.cz/soukromi
```

## Privacy practices tab

### Single purpose (English)

```
Show the signed-in user's own PropScout.cz data next to the property ad they are viewing on a supported Czech real-estate portal: the ad's price history across portals, a rental-yield estimate, and the user's deal pipeline, collections, tags and notes for that property, which they can also edit from the panel.
```

### Permission justifications (English)

`storage`
```
Keeps the user's PropScout.cz sign-in session (access and refresh token) and two panel preferences (whether the panel is folded, a dismissed hint) in chrome.storage on the user's device, so the user stays signed in and the panel keeps its state between pages.
```

`identity`
```
Signs the user in with Google through chrome.identity.launchWebAuthFlow (OAuth 2.0 with PKCE against our Supabase auth service). This is the only way to sign in to the extension; without it the extension cannot show the user's own data.
```

`sidePanel`
```
Shows the PropScout.cz property panel in Chrome's side panel beside the portal's ad page, so the page narrows instead of being covered by an overlay.
```

Site access (content scripts on www.sreality.cz, reality.bazos.cz, www.bezrealitky.cz, www.bezrealitky.com, reality.idnes.cz, nemovitosti.maxima.cz, www.remax-czech.cz, www.mmreality.cz, www.ceskereality.cz, realitymix.cz)
```
The content script runs only on these ten Czech real-estate portals, the sites the extension supports. On an ad page it reads the portal and the ad number from the page address; on a search results page it reads the links to ads. It sends only those ad numbers to our API to look up the user's data, and draws the panel and the per-ad markers. It reads no other page content and runs on no other site.
```

### Remote code

**No, I am not using remote code.** (Every script ships in the package; the API returns JSON only.)

### Data usage

Tick:

| Category | Why |
| --- | --- |
| Personally identifiable information | the user's e-mail address from Google sign-in |
| Authentication information | the sign-in tokens kept in chrome.storage |
| Web history | the portal and ad number of the ad pages the user opens on supported portals |
| Website content | the ad links read from portal search pages (as ad numbers) |

Do not tick: Health information, Financial and payment information, Personal communications, Location, User activity.
(Location: the extension sends no location, and no IP address is stored with a user's data; the hosting
provider's request logs are short-lived. Re-check if that changes.)

Certify all three:
- not sold to third parties, outside of the approved use cases;
- not used or transferred for purposes unrelated to the item's single purpose;
- not used or transferred to determine creditworthiness or for lending purposes.

Privacy policy URL: **https://propscout.cz/soukromi** (it carries the Limited Use statement; #2274)

## Distribution tab

- Payments: free. Visibility: **Unlisted**. Regions: all (at least Czechia and the EU).

## Test instructions tab

Username / Password: the reviewer's Google account (fill in the dashboard only; never in the repo or an issue).

Additional instructions (English, ≤ 500 characters):
```
Sign-in is Google only. 1) Install, open any flat-for-sale ad on www.sreality.cz or www.bezrealitky.cz (accept the portal's cookie dialog). 2) Click the PropScout.cz icon to open the side panel, then "Přihlásit se přes Google" and sign in with the account above. 3) The panel shows the price history, the MF yield and pipeline/collections for the ad. Our service is invite-only, so this account was created for review.
```

## Images (this folder)

| File | Size | Use |
| --- | --- | --- |
| `screenshot-1-ad-panel.png` | 1280×800 | an ad on bezrealitky.cz with the side panel: price history across 6 portals, MF yield, Moje |
| `screenshot-2-search.png` | 1280×800 | a bezrealitky.cz search page: the card icons and the "Výnos MF" badge |
| `screenshot-3-sign-in.png` | 1280×800 | the sign-in card with the data disclosure |
| `promo-tile-440x280.png` | 440×280 | small promo tile (optional, but items without one are listed after items with one) |
| `icon-128-padded.png` | 128×128 | optional: the store wants 96×96 artwork with 16 px transparent padding; today's `icon-128.png` fills the square. Swapping it changes the package icon too (a separate, operator-approved PR). |

How they were made (2026-11-01): the build from #2277 loaded unpacked in headless Chromium, signed in as the
non-admin smoke-test account (no pipeline, collections or notes, so no private data); public ads only. The side panel
is Chrome's own UI, which a headless browser can't capture, so screenshots 1 and 3 put the panel's own page
(`sidepanel.html`, 380 px, following the ad's tab exactly as the side panel does) beside the portal page at 900 px.
To refresh them, repeat with a current build.

## Store requirements: re-checked 2026-11-01

Against developer.chrome.com/docs: `webstore/cws-dashboard-listing`, `webstore/best-listing`, `webstore/images`,
`webstore/cws-dashboard-privacy`, `webstore/program-policies/policies` (the July 2026 Limited Use update in force since
2026-08-01: data collection must be strictly necessary to the single purpose and prominently disclosed),
`webstore/cws-dashboard-test-instructions`, `webstore/troubleshooting`, `webstore/review-process`,
`extensions/reference/manifest/name`, `extensions/reference/permissions-list`. Findings that shaped this kit:

- Name ≤ 75 and summary ≤ 132 characters, both taken from the manifest and not editable in the dashboard.
- "Productivity" is no longer a category; the closest is Workflow & Planning.
- Screenshots: 1–5 at 1280×800 or 640×400, full bleed. The 440×280 promo tile is optional but ranks items with one first.
- Collecting browsing activity is allowed only for a user-facing feature described prominently in the listing AND in
  the extension's UI: the detailed description says it, and since #2277 the sign-in card says it too.
- The privacy page must carry the Limited Use statement (it does since #2274).
- Test instructions: username and password fields (100 characters each) plus 500 characters of instructions.
- Not verified on an official page: the `key` field being refused on upload (developer reports say so; the store
  package never has one), the detailed-description maximum, and whether content-script sites need their own
  justification box (one is drafted above in case).
