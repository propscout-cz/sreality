# PropScout.cz — Chrome extension (the listing side panel + search-page controls)

Shows what the app knows about a property — its facts, its price history
across every ad, the operator's pipeline, collections, tags and notes, the
**"Výnos MF"** gross yield (`mf_gross_yield_pct`) and the comparables estimate,
the same figures the SPA shows — beside listing pages
across **every portal we scrape** (sreality, bazos, bezrealitky, idnes,
maxima, remax, mmreality, ceskereality).

- **The side panel** is Chrome's own (`chrome.sidePanel`, operator ruling
  2026-10-31: it shrinks the page beside it, never covers it): our page
  `sidepanel.html` (`src/sidepanel.ts`; controller `src/panel.ts`, frame
  `src/frame.ts`, one stylesheet `src/panel.css` over the app's `tokens.css`,
  the document root in the user's scheme and theme). One global panel per
  window: once open it stays open across the window's tabs and shows the
  **active tab's subject** — a detail page's ad, or the search card last opened
  in it; any other tab shows "Vyberte inzerát na podporovaném portálu. Pokud
  ho máte otevřený, obnovte stránku." (signed out: the sign-in card). A tab
  switch keeps what is in progress: a half-typed note waits for its property,
  an edit to the yield assumptions is saved at once, and an estimate still
  running is shown again when its ad comes back (never a second run).
  Chrome opens a side panel only on a user action, so it never opens by
  itself: the toolbar icon toggles it, and a detail page's **edge tab**
  (`src/edge_tab.ts`, a closed shadow root on the right edge: the PS mark, the
  deal's stage badge, this ad's price drop and the MF yield) or a search
  card's yield badge opens it; the edge tab hides while the panel is open in
  that window (and reads its price history only while it shows), and drags up
  and down the edge, staying where it was left on every portal. After an
  extension update the open pages' scripts are cut off: the edge tab then
  says so and its click reloads the page. The top bar: the wordmark,
  **V aplikaci ↗** (the ad's page in the app, `/listing/{source}/{native_id}`),
  the **⋯** account menu (email + **Odhlásit**, which signs out in every tab)
  and **›**, which closes the panel. Under it:
  - **The header** (`src/panel_header.ts`): deal · status · days on market, the
    title (type, disposition, area), the address, the facts by property type —
    a flat its floor, condition and ownership; a house its plot, condition and
    house type; a commercial unit its type, floor and condition; a plot its
    ownership only (a fact the property lacks is left out). This ad's price in
    the live colour, its own last price move and its per-m² with the basis's
    unit; the MF gross yield beside it only when the lookup serves one.
  - **Vývoj ceny** (`src/panel_price.ts`): one step line per ad of the property
    from `GET /properties/{id}/price-history` through the app's
    `lib/priceHistory` and `lib/chartAxis` — the canonical ad first in the
    accent, the others in ink dashed, an ad taken down dashed to a hollow end,
    each labelled where it ends; the note counts portals and ads taken down; a
    history with no move says "Cena se od … nezměnila.".
  - **Moje** (`src/panel_mine.ts`), property-grain (rules #18, #22), every write
    through the one store (`src/store.ts`) to the same JWT-gated routes the SPA
    uses. Out of the pipeline: **Přidat do pipeline** (entry stage) and the
    **Skrýt** toggle (migration 536; absent while the property is a LIVE deal).
    In it: a step bar over the open stages (more than four: the stage
    `<select>`), "ve fázi od" (the lookup's `pipeline.entered_stage_at`) and
    **Uzavřít ▾** — the closed stages, then **Odebrat z pipeline…** behind a
    two-step confirm (never a one-click remove). Collections (mine only — a
    shared one is never "saved") and tags as chips, three then **+N**; **+** or
    **+N** opens the save checklist (`src/checklist.ts`, the search cards' too),
    re-read on opening; creating and renaming stay in the app. The notes: the
    latest and the count; **Všechny** / **Přidat poznámku** expand them in place
    to the full CRUD — edit, delete behind a confirm, another author's shared
    note read-only ("od {email} · {datum}"), and **Sdílet s {emails}** (off by
    default, offered only when someone would read it; migration 610). A note
    records the viewed ad as its `origin_listing_ref_id`; a read that fails
    every try says so with **Zkusit znovu**, never "no notes" (MS16).
  - **AI agenti** (`src/panel_agents.ts`), the accent card: on a flat for sale
    the **Výnos** row runs the comparables estimate (`POST /estimations`, polled
    2 s up to 6 min) and shows its yield with the assumptions editor below it
    — rent, fond oprav + SVJ, price, renovation, a live yield and **Reset**,
    saved to the run's `scenario`; the monthly quota (zbývá N), the trial line
    and, at the limit, **Upgradovat**. Flip agent, Agent přestavby and
    Regionální scout are marked **Brzy**. The chat box has no backend yet: a
    hover, focus or click lays "Chat s agentem připravujeme" over it.
  - A listing not in our DB shows "Tato nemovitost zatím není v naší
    databázi." and **Přidat do databáze**: it `POST`s the page URL to
    `/listings/ingest` (admins only in v1), polls `GET /listings/ingest/{source}/{native_id}`
    every 2 s on the estimate's budget (Hledám inzerát → Stahuji stránku →
    Zakládám nemovitost), and once the ad is held with a property re-reads the
    lookup, so the panel fills as for any listing we have. A refused, gone or
    failed ad shows the API's one Czech sentence above the button; a
    non-admin's first click answers the admin sentence and hides the button
    until the account changes. The flow is `src/ingest_flow.ts` (no DOM, no
    `chrome`), tested by `npm test`.
  - No ad in the active tab, signed out, a failed lookup (**Zkusit znovu**) and
    the first load (a static skeleton in the cards' shape) are one card each.
    Escape closes its menus.
  - **Two contexts, two stores.** Each tab's content script owns its subject
    and its own store (search cards, edge tab); the side panel has its own over
    the same transport. The panel opens a port to every tab of its window
    (`chrome.tabs.connect`; a tab that loads later calls it, one dragged in is
    connected as it arrives): the tab's subject goes up it, its closing shows
    the edge tab again, and after a write lands in either the other re-reads
    that property (`property_written`, property id only). No `tabs` permission:
    the panel never reads a tab's URL, it asks the tab.
- **Index / search pages — every category, not just flats** — get the Browse
  card's own controls on each listing card we have a property for, in the same
  order and with the same meaning as in the app (`src/index_cards.ts`):
  - **Funnel** (pipeline, rule #22): out of the pipeline, one click adds the
    property at the entry stage; in it, the funnel carries the stage badge and
    colour and a click opens the stage menu — move to another stage, or
    **Odebrat z pipeline** behind a two-step confirm. Never a remove toggle.
  - **Bookmark** (collections, rule #18): lit, and named on the photo mark, only
    for the operator's own collections; opens the checklist of every own
    collection, monitored ones first and bell-marked; a click on a row adds or
    removes the property. The list is re-read each time the checklist opens.
  - **Eye** (hide, migration 536): one click hides the property, one click
    restores it. Absent while the property is a LIVE deal, like everywhere else.
  - **Photo mark** (`src/index_photo_mark.ts`), so the state reads from
    across the grid. In the pipeline: the photo toned in the stage colour (the
    operator's, copper when none) with the stage badge and name over it;
    hovering the card lifts the tone to show the photo. In a collection: the
    same tone in copper, a bookmark and the collection's name (`+N` for more)
    where a stage sets its badge — a property already sorted must not shine
    like a new one; in the pipeline too, the stage tones the photo and a copper
    label in the top-right corner names the collection. Tagged: the tag chips under that label
    (up to four, then `+N`; names and colours from `GET /tags`, read once a
    card on the page carries a tag). A property can carry all three. The photo is
    found by geometry (the largest image the card shows, cut to whatever clips
    it, corners matched), not by portal markup. A hidden card drawn barely
    visible gets no mark, and neither does a card guess that holds several
    listings (it could mark another listing's photo).
  - All three write through the same routes as the side panel and the app, are
    optimistic (a failure puts the state back and says why in the corner), and
    are property-grain: a write repaints every card of that property on the
    page (they draw from the page's store, `src/store.ts`), and the side panel
    re-reads it when it shows the same property (and the other way round).
  - A card is the outermost list item / article that still holds ONE listing,
    so a photo carousel that repeats the link on every slide gets one set of
    controls, not one per slide. The controls live in a closed shadow root per
    card; the two menus float in one page-level shadow host.
- **Non-listing cards** (`src/index_promo.ts`): a card-sized child of the
  results grid that holds no listing link — a paid ad, a project promo — is
  washed whitish (opacity), lifted on hover. Found by structure: each listing
  card's item is its highest ancestor holding no other listing, and the
  parent of three or more items is the grid; smaller children (sort bars,
  pagination) are left alone.
- **The corner dock** (`src/index_dock.ts`) sits **bottom-left** on every index
  page with listing cards: a **Skrýt skryté**
  switch plus how many hidden properties this page holds. On (the default), a
  hidden property's card stays where it is but is drawn barely visible (10 %,
  greyscale; hovering lifts it enough to see what it is); off, it is drawn like
  any other card and told apart only by its filled eye. The card is never
  removed — that would break the portal's own grid and paging, and a card that
  is gone can't be un-hidden from where it stood. The preference is per
  browser (`chrome.storage.local`) and follows the operator across portals and
  open tabs.
- **Sale apartments** additionally get the per-card yield badge, after the
  three controls: `Výnos MF X.X %` (or the
  town's `X.X–Y.Y %` range, its note in the tooltip) when the property's MF
  result has a yield, otherwise a clickable **Odhadnout výnos** badge; either
  opens the side panel on that ad. Only an ad we hold gets one. Controls and
  badges come only from a successful lookup; when it fails the page is not left
  silent — one small
  notice in the dock, above the switch, says
  why: **Přihlásit se přes Google** when signed out (a click first checks for
  a session made elsewhere — the side panel, another tab — and skips the Google
  round trip if there is one), **Obnovit stránku** when the extension was
  reloaded, updated, disabled or removed under the open tab (the overlay then
  stops scanning), the error + **Zkusit znovu** otherwise (no button for a
  build without an API URL, which only a rebuild fixes). Automatic re-lookups
  back off 60 s; the tab becoming visible again asks at once; a lookup
  unanswered for 20 s shows as failed, though a late answer still badges. The
  notice appears while any listing card is waiting on the failed lookup — every
  card gets controls from it, so a rental or house search is as stuck as a flat
  one — and never on a page without cards. × hides it until the next page load.

The default display is a **read** of data we already have — no LLM call. It
maps each portal listing to our row by `(source, native id)` through the
FastAPI `POST /listings/lookup` endpoint.
The Výnos editor shares its scenario state (rent / fond oprav / price /
renovation) with the SPA's `/estimation/:id` page via the `scenario` JSONB
column on `estimation_runs`.

### Per-m² figures carry their basis (measure sprint W7)

"One measure, one definition, one label" — the panel **reads** per-m² numbers,
it never derives them:

- The header's per-m² is the lookup's `price_per_m2`, rendered with its
  `price_per_m2_basis` through the app's `fmtMeasuredPricePerM2`, so a sale,
  a rent and a plot each carry their own unit.
- The *Fond oprav + SVJ* rate is labelled with the app's monthly unit
  (`PPM2_UNIT.rent`, the same string `toolkit/measures.PPM2_UNIT_CS` uses). A
  bare `Kč/m²` is the *capital* unit.
- A plot's headline area says it is a plot (`905 m² pozemku`); the title's
  hover names the area's basis from `listings.area_basis` (migration 423).
- The fond is **not multiplied by a parcel**, and *whether a fond applies* is
  decided in exactly one place — the server, on `area_basis` **or**
  `category_main` (`api/portal_lookup._fond_default_czk_per_m2`). The panel
  reads that answer rather than re-deriving it, because migration 423 added
  `area_basis` with no backfill: a land row not yet re-scraped has no stamp,
  and a client rule keyed on the stamp alone would disagree with the server.
  Where no fond applies the field is disabled, the hint says so and nothing is
  written to the run's scenario; otherwise a 10 Kč per m² monthly charge
  against a 905 m² plot would manufacture ~9 050 Kč/month of cost, which the
  yield subtracts from the rent and which can turn the figure negative.
- The default fond rate is **served per subject** by `POST /listings/lookup`
  (`fond_per_m2_czk_default`) from the one definition in
  `api/schemas.DEFAULT_FOND_CZK_PER_M2`, on every row; the extension holds no
  copy of the number. A number is the rate, `null` means "no fond can apply to
  this subject".
- `POST /estimations` takes the ad's `listing_id` from the lookup (an
  estimation is always of an ad we hold; an ad we lack is added first) and is
  **category-aware**: the panel sends the subject's `category_main` and picks
  `estimate_kind` from it (a subject being sold needs a rent, one already let
  needs a sale price, land is never estimated as a rent). **The category arm is
  latent today.** The estimation block only renders for apartments for sale we
  hold, so every reachable call still resolves to `rent` + `byt`. It takes
  effect the moment that category gate widens; no house, commercial unit or
  plot can reach this surface's estimate button in the meantime.

> **Backend dependency:** needs the `POST /listings/lookup` endpoint
> (shipped in the `feature/portal-mf-lookup` PR). Make sure that's deployed
> to the Railway API before loading this build.

## Sign-in (Wave 1)

The extension runs its **own** Supabase session — a separate sign-in from
the SPA's, never the SPA's refresh token (Supabase rotates refresh tokens
with reuse-detection, so sharing one between two independently-refreshing
sessions would eventually log both out). Every API route now requires a
real per-user session; the panel shows a **"Přihlásit se přes Google"**
prompt until you sign in, and once you are, its **⋯** menu names the
account and offers **Odhlásit**.

Under the hood: `chrome.identity.launchWebAuthFlow` opens the Google
consent screen via Supabase GoTrue's PKCE flow; the code exchange and the
token refresh happen entirely in the background worker. No secret ships in
the bundle for this — the Supabase anon key is a public client key, same
posture as the SPA.

**The session survives idle time and network blips** (`src/token_source.ts`,
tested in `token_source.test.ts` against a fake GoTrue that rotates refresh
tokens with reuse detection):

- Every API call takes its token from it: a token near expiry is refreshed
  first, so a session left idle for days is refreshed by the next call. No
  timer, so no `alarms` permission.
- One refresh at a time: concurrent calls share it (two refreshes on one
  token would trip Supabase's reuse detection and revoke the session).
- A refresh with no answer, a timeout (15 s), a 5xx, a 429, another 4xx from
  something in between (a proxy, a firewall) or a 200 without a token
  **keeps** the stored session; that call fails with "zkuste to znovu" and the
  next one refreshes. (If GoTrue rotated the token but its answer was lost,
  the kept token is already used and the next refresh is refused.)
- Only GoTrue's refusal (its 400, or a body naming a refresh-token or session
  error: invalid, reused, revoked) signs out, and the panel shows the sign-in
  card.

**Permissions:** `storage` (the session and two preferences: the search pages'
"Skrýt skryté" switch and where the edge tab was left),
`identity` (the Google sign-in) and `sidePanel`; no host permissions. The
content script runs on the portal hosts that serve pages (below). The
sign-in card says what the extension sends once signed in and links
https://propscout.cz/soukromi: the store allows browsing data only for a
feature described in both its listing and the extension's own UI (program
policies, 2026-11-01).

**One-time setup for a NEW deployment** (already done for the operator's
existing Railway + Supabase project — skip unless standing up a fresh one):

1. **Pin the redirect URL.** This build's extension ID is fixed by the
   `key` field in `manifest.json` (so "Load unpacked" gives the same ID on
   every machine/CI download): `eibnegoankipleeegjilnjhpnbaedpjd`. Its auth
   redirect URL is `https://eibnegoankipleeegjilnjhpnbaedpjd.chromiumapp.org/`.
2. **Supabase dashboard** → your project → **Authentication** → **URL
   Configuration** → **Redirect URLs** → add that exact URL to
   **Additional Redirect URLs** → **Save**.
3. **Supabase dashboard** → **Authentication** → **Providers** → confirm
   **Google** is enabled.
4. Add the extension's origin to the API's CORS allowlist (below, "Allow the
   extension's origin in CORS").

Google Cloud needs nothing for the extension: Google only ever sees Supabase's
own callback, never the extension's redirect URL.

If you ever need a *different* stable extension ID (e.g. a second
deployment), generate a new keypair and repeat steps 1, 2 and 4 with the new ID:

```sh
openssl genrsa -out extension-key.pem 2048
openssl rsa -in extension-key.pem -pubout -outform DER | openssl base64 -A
```

Paste that output into manifest.json's `"key"` field. **Keep `extension-key.pem`
out of git** (already gitignored) — it isn't needed again unless you locally
pack a signed `.crx`; the committed `key` field (a public key) is all Chrome
needs to keep deriving the same ID.

## Distribution policy

No secret ships in the bundle — the Supabase anon key + FastAPI base URL are
both non-sensitive public client config (same posture as the SPA), and the
manifest names no deployed host. The extension goes to the Chrome Web Store as
an Unlisted item: spec #2261 and `docs/design/brand-domain-and-store/PROGRAM.md`.

## Build option A — GitHub Actions (recommended)

No local toolchain required. The repo has a workflow at
`.github/workflows/build-extension.yml` that builds the extension
in CI and uploads the result as a downloadable artifact.

One-time setup — repository secrets:
1. GitHub repo → **Settings** → **Secrets and variables** → **Actions**.
2. Add `EXT_API_BASE_URL` = the Railway FastAPI URL (no trailing slash).
3. Add `EXT_SUPABASE_URL` = the same value as the SPA's `VITE_SUPABASE_URL`.
4. Add `EXT_SUPABASE_ANON_KEY` = the same value as the SPA's
   `VITE_SUPABASE_ANON_KEY` (the anon/publishable key — not a secret key).
5. (Optional) Add `EXT_APP_BASE_URL` = the SPA (browser app) URL, no trailing
   slash — powers the "Otevřít v aplikaci" link to a listing's page in our app
   (`/listing/{source}/{native id}`). Leave it unset to hide the link.

Building:
1. The workflow runs automatically on every push to `main` that
   touches `chrome-extension/**`, or you can trigger it manually:
   GitHub → **Actions** → **Build Chrome extension** → **Run
   workflow**.
2. Wait for the green check (~1 minute).
3. On the run page scroll to **Artifacts** at the bottom →
   download **chrome-extension-dist**.
4. Unzip it. The unzipped folder is what you "Load unpacked" into
   Chrome.

The same run also uploads **chrome-extension-store**, the store package (see
"The store package and its permission guard" below). Don't load that one
unpacked: it has no `key`, so Chrome would give it a different ID and sign-in
would fail. **extension-e2e-shots** holds the side panel harness's screenshots
("End-to-end harness" below).

## Build option B — Local

If you have Node 20 installed locally:

```sh
cd chrome-extension
cp .env.example .env
# edit .env: set VITE_API_BASE_URL, VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
npm install
npm run build
```

The output lands in `chrome-extension/dist/`. While working on one entry,
`npm run watch -- sidepanel` (or `content`, `background`) rebuilds just that one
on every save, after one full `npm run build`; reload the extension (and the
portal page, for `content`) to pick it up. `npm test` runs the extension's
unit tests (`src/*.test.ts`) with Node's built-in runner, which needs Node
≥ 22.18 to strip the types; `npm run type-check` type-checks them with the rest.

`npm run build` runs vite **once per entry** — `--mode content`, then
`--mode background`, then `--mode sidepanel` (the side panel's page script; the
build copies `sidepanel.html` beside it) — and that is not a style choice. A manifest-declared content script is a
*classic* script (MV3 has no module mode for `content_scripts[].js`), so
`content.js` may not contain a static `import`. Rollup hoists any module shared
between entries into a chunk, and the portal-id rules (`src/portals.ts` then,
`frontend/src/lib/portalIds.ts` now) are imported by both entries, so a
single two-entry build emitted `import … from "./chunks/portals-*.js"` as line 1
of `content.js`; Chrome refused the script outright and the panel silently never
mounted (2026-08-04 → 2026-09-18). Separate passes keep each entry
self-contained, and a build-time guard in `vite.config.ts` re-parses `content.js`
as a classic script and fails the build if a static import ever comes back. A
bare `vite build` with no `--mode` now errors rather than emitting half a bundle.

The bundles import what the app already defines straight from `frontend/src`
(formatters, the per-m² vocabulary, the MF result, the stage look, the
collection rules, the appearance values, the palette `styles/tokens.css`) — one
definition each, never a copy. A second guard in `vite.config.ts` keeps that
safe: an app module the extension bundles may import only another plain `.ts`
module by relative path, so a React, Supabase or `@/` import fails the build
instead of landing in every portal page. The search-page controls take the
user's colour scheme and light/dark side from their profile (the same choice
as in the app, read from the session at its next refresh; Měď light when
signed out).

## End-to-end harness

`e2e/sidepanel.mjs` drives the **built** extension in headless Chromium with
`chrome.*` stubbed: the side panel page (`dist/sidepanel.html` at 400 px, a mock
background answering its messages, mock content scripts answering its
`tabs.connect` ports) and `dist/content.js` on routed fake sreality pages. It
checks that the panel follows its window's active tab, the signed-out and
no-ad states, what a tab switch keeps (a note draft, a debounced scenario edit,
a run in flight), the menus' focus and the in-place yield editor; on the page,
the edge tab's marks, opening the panel, dragging it, hiding while it is open
(bfcache included), the reload offer of an orphaned script and a search card's badge;
and that no page threw.

```sh
npm run build && npm run e2e        # or: npm run e2e -- <dist-dir> <out-dir>
```

It prints one PASS/FAIL line per check, writes screenshots and `checks.txt` to
`e2e-shots/` (gitignored) and exits 1 on any FAIL. Fixture dates count back
from today. CI runs it after every build and uploads that folder as the
**extension-e2e-shots** artifact, pass or fail.

The browser is Playwright's headless shell for the pinned `playwright-core`:
`npx playwright-core install chromium-headless-shell` once, plus its system
libraries (`npx playwright-core install-deps chromium-headless-shell`, which
needs `sudo`). On a machine without those libraries and without `sudo`, unpack
them somewhere and set `LD_LIBRARY_PATH=<that>/usr/lib/x86_64-linux-gnu`;
`CHROME_PATH=<binary>` runs any other Chromium instead of the cached one.

## Install in Chrome (unpacked)

1. Open `chrome://extensions` in Chrome.
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked**.
4. Pick the `dist/` folder you downloaded (Option A) or built
   (Option B).
5. Confirm the extension card shows "PropScout.cz" with the dark PS icon
   (drawn by `frontend/scripts/make-icons.py`). Pin it (the puzzle-piece menu → the pin) so its
   toolbar icon, which opens and closes the side panel, stays in reach.
   Chrome 116 or newer.

**Unzip somewhere Windows won't decorate.** Chrome refuses to load a folder
containing `desktop.ini` ("The filename is illegal"), and File Explorer creates
one in any folder it has rendered — OneDrive-synced folders especially. Unzip to
a plain local path (or delete `desktop.ini` from `dist/` before loading); on WSL,
`\\wsl.localhost\...` paths avoid it entirely.

The `key` field pinned in `manifest.json` means the extension ID is always
`eibnegoankipleeegjilnjhpnbaedpjd` (same on every machine, every CI
download) — the "one-time setup" steps above already registered it with
Supabase, and the API's CORS allowlist already names its origin.

## Keeping it up to date (auto-update)

**"Load unpacked" has no update channel.** Chrome never fetches anything for an
unpacked extension — a new build only lands when you replace the folder and click
**Reload**. That's the right loop while *developing*; it's the wrong one for
*using* the extension day to day. To stop re-downloading, move the everyday
install onto a real update channel.

### The version number (prerequisite for any channel)

Chrome only auto-updates when the offered version is **strictly greater** than the
installed one, and the Web Store rejects re-uploading a version that already
exists. Hand-bumping `manifest.json` was being missed (PR #942 shipped with no
bump), which would silently strand every installed copy.

So `vite.config.ts` now stamps it: `manifest.json`'s `MAJOR.MINOR` stays the
hand-owned feature declaration, and CI appends the GitHub run number as the patch
component (`0.9.0` committed → `0.9.417` built in CI). The run number is monotonic
and never resets, so **every CI build is automatically newer than the last** with
nothing to remember. Local builds keep the committed version, so a dev reload
doesn't churn it. Bump `MAJOR.MINOR` by hand only to mark a meaningful release.

### Option 1 — Chrome Web Store, "Unlisted" (recommended)

Unlisted means: **not searchable, not public** — installable only by direct link,
but distributed and auto-updated by Google's infrastructure exactly like a public
extension. Chrome polls for updates roughly every 5 hours, and you can force one
from `chrome://extensions` → **Update**.

This is the industry-standard way to run a pre-launch/internal extension, and it
makes going public later a **visibility dropdown change** — same item, same
extension ID, same update channel, no reinstall.

1. Register once at the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole)
   (one-time US$5 fee, any Google account).
2. **+ New item** → upload the store package: the `chrome-extension-store`
   artifact from the Actions build, downloaded as a ZIP with `manifest.json` at
   its root, which is the shape the store takes. Never upload `dist/`: the
   store refuses a manifest with a `key`.
3. Fill the listing: description, an icon, at least one screenshot, a **privacy
   policy URL**, a **single-purpose statement**, and a justification for each
   permission in `release/allowed.ts` (there are no host permissions). See
   "Chrome Web Store submission" below.
4. **Visibility → Unlisted**. Submit for review. (Unlisted items are still
   reviewed; expect hours to a few days.)
5. Install once from the item's link. From then on it updates itself.

> ⚠️ **The extension ID will probably change — check before you cut over.** The
> store assigns the item its own ID, which will most likely differ from the
> self-generated `eibnegoankipleeegjilnjhpnbaedpjd` pinned in the `key` field.
> That ID is baked into two places, and sign-in plus every API call break if
> they aren't updated: the **Supabase** redirect-URL allowlist
> (`https://<id>.chromiumapp.org/`) and the API's `CORS_ALLOW_ORIGINS`
> (`chrome-extension://<id>`). After creating the draft item, read its assigned
> ID off the dashboard URL and **add** the new values alongside the existing
> ones (both accept multiple entries) *before* installing the store build. Adding rather than replacing means the unpacked dev
> copy keeps working throughout — no downtime, and no cutover to time.

### Option 2 — self-hosted `.crx` + `update_url`

The classic off-store route: sign a `.crx`, host it plus an `updates.xml` manifest
(Cloudflare R2 is already in this stack and would serve both fine), and point
`manifest.json`'s `update_url` at the XML. Chrome then polls it directly — no
review, no store, full control of cadence.

**The catch:** Chrome on **Windows and macOS blocks installing extensions that
don't come from the Web Store.** Getting around it legitimately means pushing an
enterprise policy (`ExtensionSettings` / `ExtensionInstallForcelist`) onto the
machine — a registry edit on Windows — per machine you use.

Worth it only if Web Store review latency becomes intolerable during heavy
iteration. Otherwise it's infrastructure you'd build now and throw away at public
launch, so Option 1 is the better default.

### Fully automated publishing (once Option 1 is live)

The Web Store has a publish API, so `.github/workflows/build-extension.yml` can be
extended to upload and publish each `main` build — making "merge a PR" the only
step, with the operator's browser updating itself a few hours later. Needs four
repository secrets (`CWS_CLIENT_ID`, `CWS_CLIENT_SECRET`, `CWS_REFRESH_TOKEN`,
`CWS_EXTENSION_ID`); ask for it once the store item exists.

## Allow the extension's origin in CORS (Railway)

The manifest grants **no host permissions**, so every call the background
worker makes to the API is an ordinary cross-origin request from
`chrome-extension://<id>`. The API answers it only when that origin is in its
`CORS_ALLOW_ORIGINS` allowlist; without it, every API call fails. The auth
service (Supabase) answers any origin and needs no entry.

Today the allowlist holds the app (`https://app.propscout.cz`) and the pinned
extension ID, `chrome-extension://eibnegoankipleeegjilnjhpnbaedpjd` (added
2026-10-31, #2275). The landing's sign-up forms are plain form posts and need
no entry. The store's ID gets its own entry before the store review (#2280).

`CORS_ALLOW_ORIGINS` is a **kept** Railway variable: its name is declared in
`.railway/railway.ts`, its value lives in Railway, and the operator changes the
value there (an agent never does):

1. **Railway** → project **API** → service **sreality** → **Variables**.
2. `CORS_ALLOW_ORIGINS` → **⋯** → **Edit**. Never rename it or add a second
   variable: names belong to `.railway/railway.ts`.
3. Append `,chrome-extension://<id>` to the end of the value; keep every
   existing entry.
4. Save, then click **Deploy** on the staged change (a variable edit alone
   doesn't redeploy).
5. Check, read-only: this prints `200` once the origin is allowed (`400`
   before):
   ```sh
   curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS https://api.propscout.cz/health \
     -H 'Origin: chrome-extension://<id>' -H 'Access-Control-Request-Method: GET'
   ```

## Use

**First run:** open the side panel (the toolbar icon, or the PS tab on a
listing's right edge); it shows **"Přihlásit se přes Google"** — click it,
complete the Google consent screen in the popup window that opens, and the
panel reloads signed in. Its **⋯** menu then names the account; **Odhlásit**
there signs out in every tab (search pages drop their badges and card
controls; the panel prompts).

**On a listing detail page** (any supported portal):

1. A small PS tab sits on the right edge with the deal's stage, this ad's
   price drop and the MF yield; click it (or the toolbar icon) and Chrome's
   side panel opens beside the page, which narrows to make room. It stays
   open as you move between listings and tabs in that window and follows
   the tab in front; the **›** in its top bar (or the toolbar icon) closes it.
2. The header says what the property is and what this ad asks; **V aplikaci ↗**
   opens it in the app.
3. **Vývoj ceny** charts every ad of the property; **Moje** holds the pipeline
   (**Přidat do pipeline**, then the stage bar and **Uzavřít ▾**), the hide
   toggle, collections, tags and notes.
4. On a flat for sale, **AI agenti › Odhad výnosu** runs the comparables
   estimate, or opens the one already made; its editor's fields debounce
   500 ms and PATCH the scenario back — the SPA's `/estimation/:id` page picks
   up the same state on next load.

**On a search / index page** (any supported portal, any category):

1. Each card we have in our database gets three small icons in its top-left
   corner — the same three as a Browse card in the app: the **funnel** (add to
   the pipeline; once added, click again to change the stage), the **bookmark**
   (tick the collections the property belongs in) and the **eye** (hide the
   property / show it again). A property in the pipeline also has its photo
   tinted in the stage's colour with the stage number over it (point at the
   card to see the photo through); one saved to a collection gets the same
   tint in copper with a bookmark and the collection's name; its tags show as
   coloured chips in the photo's top-right corner. Cards in the results that
   are not listings — paid ads, developer-project promos — are washed white
   (point at one to see it).
2. Sale-apartment cards also get a badge right after the icons:
   `Výnos MF X.X %` (or a range) when we have the yield (rent or note in the
   tooltip); otherwise a
   clickable **Odhadnout výnos** badge; either opens the side panel on that
   ad, where the estimate runs.
3. In the bottom-left corner, the **Skrýt skryté** switch: on, the properties
   you have hidden stay on the page as barely visible cards (hover one to peek
   at it, click its eye to bring it back); off, they look like every other
   card. The number beside it says how many hidden ones the page holds.
4. Cards aren't matched by portal-specific markup — the overlay scans each
   card's detail link and resolves it through `/listings/lookup`, so it
   survives the portals reshuffling their card HTML. Cards not in our DB get no
   icons and no badge; an ad is added from its own detail page (**Přidat do
   databáze**).

### Supported portals

sreality, bazos, bezrealitky (.cz and the English .com), idnes, maxima, remax,
mmreality, ceskereality and realitymix (a missed id just shows no badge, never a
wrong one).

The host list and the URL→id rules live in ONE module the SPA shares,
`frontend/src/lib/portalIds.ts` (imported cross-tree like `lib/brand.ts`; the
SPA's paste-a-link uses the same rules). Each portal names all its `hosts` (a
pasted link on any of them resolves) and its `pageHosts`, the ones that answer
with pages. The build writes `content_scripts.matches` from the page hosts
only: `sreality.cz`, `bezrealitky.cz`, `bezrealitky.com`, `remax-czech.cz`,
`mmreality.cz`, `ceskereality.cz` and `www.realitymix.cz` redirect every path
to their page host (checked 2026-11-01), so the script never runs there and the
install warning doesn't name them. The checked-in `manifest.json` list is a
template that `frontend/src/lib/portalIds.test.ts` keeps equal to the page
hosts, so a new page host goes in both (and in `release/allowed.ts`).

**Soft navigation.** sreality and bezrealitky change the address through the
History API, without a new document, and a manifest content script is injected
only into a new document. The content script watches the Navigation API itself
(`currententrychange`), which reaches its isolated world, and re-decides what to
show; no `webNavigation` permission. Live-checked headless on nine of the ten
page hosts (#2277): index → ad and ad → ad, soft on sreality and bezrealitky
(.cz and .com), full page loads on the rest. ceskereality answers a headless
browser with an error page, so its check is a manual one in Chrome.

> **Note:** index pages on the React/Next.js portals (sreality, bezrealitky)
> render cards client-side, so the icons and badges appear a moment after the
> results do and re-attach as you scroll/paginate (a `MutationObserver` watches
> for new cards).

## The store package and its permission guard

`npm run package` (after `npm run build`) writes `store/` from `dist/`: the same
files without the manifest's `key` (the store assigns its own ID; the repository
keeps the key so unpacked builds keep theirs). It then refuses the package
(`release/package.ts`) when it carries:

- a permission, a content-script site or a host permission other than the
  hand-written list in `release/allowed.ts` (compared for **equality**, so a
  dropped permission fails too);
- a `key`, a source map (`.map` file or `sourceMappingURL` comment), or one of
  our deployed hostnames (the build's API, auth and app hosts);
- a description over 132 characters.

CI runs it on every pull request against the real build and uploads `store/` as
the `chrome-extension-store` artifact. **Adding a permission or a portal site is
a deliberate edit to `release/allowed.ts`**, reviewed in its PR: Chrome disables
every installed copy until its user approves a new permission, so the install
warning only grows on purpose. Never generate that list from the build or the
portal registry.

## Chrome Web Store submission — what's left

The bundle carries no secret, every write is per-user + RLS-scoped, and the
store package above passes the store's package rules. The rest is spec #2261:
the privacy page, the listing kit, the first upload,
and releases through the store API.

Staged rollout: from the first store release on, keep API changes the extension
relies on additive until the store has published the version that stopped
needing the old shape. **No auth safety net remains**: the `verify_jwt`
dual-auth window closed 2026-08-04 (PR #941), so a build still presenting a
static `API_TOKEN` gets a flat `401` (`tests/api/test_auth.py` asserts a non-JWT
bearer is refused). Every install must carry a real Supabase JWT.
