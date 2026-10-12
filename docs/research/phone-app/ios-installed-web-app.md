# What iOS does with an installed web app

Research for #1763 (map #1759), 2026-10-12. Question: what iOS does with our SPA once it is added to the
Home Screen from Safari — manifest, layout, links, back, storage, Google sign-in, keyboard, overscroll,
the share sheet, Chrome. Throwaway branch `research/phone-app-ios`; never merged.

Sources are primary unless marked **(secondary)**: WebKit blog and source, Apple developer docs, Safari release
notes, WWDC transcripts, support.apple.com, MDN browser-compat-data (BCD), bugs.webkit.org (bugs filed by users
are evidence of behaviour, not Apple statements; Apple-engineer comments are flagged as such), supabase-js
source, Google's OAuth docs.

## Version baseline (2026-10-12)

- **Current release: iOS 27.0.1** (late Sep 2026); iOS 27.0 and Safari 27.0 shipped 2026-09-14; iOS 26.7.x still
  maintained. Apple renumbered iOS 18 → iOS 26 in 2025. <https://support.apple.com/en-us/100100>,
  <https://support.apple.com/en-us/149076> (2026-09-29), <https://developer.apple.com/documentation/safari-release-notes/safari-27-release-notes>.
- **Safari 27.0 changes nothing about Home Screen web apps.** The full "WebKit Features for Safari 27.0" post
  (<https://webkit.org/blog/18325/webkit-features-for-safari-27-0/>, 2026-09-17), the WWDC26 beta post
  (<https://webkit.org/blog/17967/news-from-wwdc26-webkit-in-safari-27-beta/>, 2026-06-08), WWDC26 session 204 and
  the Safari 27 release notes have no web-app section; only scroll/rubber-band fixes touch us. Checked by grepping
  the downloaded post text.
- **The decisive change is iOS 26**: "By default, every website added to the Home Screen opens as a web app. If
  the user prefers to add a bookmark for their browser, they can disable 'Open as Web App' … there are now zero
  requirements for 'installability'." <https://webkit.org/blog/17333/webkit-features-in-safari-26-0/>
  (2025-09-15, iOS 26+). Before that (iOS 16.4–18.x) a site opened standalone only with manifest
  `display: standalone|fullscreen` or `<meta name="apple-mobile-web-app-capable">`; otherwise the icon was a
  bookmark opening the default browser (<https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/>,
  2023-02-16).
- **EU (Czechia):** iOS 17.4 betas removed Home Screen web apps in the EU; Apple reversed it on 2024-03-01 —
  "Home Screen web apps continue to be built directly on WebKit" (Apple's DMA page then; the text is gone from
  the current page, so quoted via <https://www.theregister.com/2024/03/02/apple_reverses_pwa_decision/>
  **(secondary)**; WebKit bug 268643 tracks the restore). iOS 26/27 docs carry no EU exception.

### Our SPA today

- `frontend/index.html`: no manifest, no `apple-touch-icon`, no `apple-mobile-web-app-*` meta, viewport meta
  lacks `viewport-fit=cover`; no service worker / PWA plugin. On iOS 26/27 it still installs as a web app (default
  scope); on iOS 18 its icon would just open Safari.
- `frontend/src/lib/supabase.ts`: `persistSession`, `autoRefreshToken`, `detectSessionInUrl: true`, no
  `flowType` → supabase-js default **implicit flow** (installed auth-js 2.105.1, `GoTrueClient` DEFAULT_OPTIONS
  `flowType: 'implicit'`; same on supabase-js master v2.117.3, 2026-10-07). `signInWithOAuth` does
  `window.location.assign(url)`; the session lives in `localStorage` under `sb-<ref>-auth-token`.
- Portal links in the SPA use `target="_blank"` (24 places); no `window.open`.

## 1. Manifest members iOS honours

| Member | iOS behaviour | Source |
| --- | --- | --- |
| `display` | `standalone` and `browser` since iOS 11.3; `fullscreen`, `minimal-ui`, `display_override` not supported (fullscreen still makes it a web app, no true fullscreen). On iOS 26+ the user's "Open as Web App" toggle decides, whatever the manifest says. | BCD `manifests/webapp/display.json`; WebKit `ApplicationManifestParser.cpp`; Safari 26.0 post |
| `start_url` | Supported since 11.3; loaded on launch. | BCD; WWDC23 10120 |
| `scope` | Supported; **default scope = the host** of the page used to create the app (Apple, WWDC23). WebKit's parser defaults to the start URL's directory and flags it `isDefaultScope` (commit 716495eb, 2023-10-04); what Safari does with the flag is closed. Set `scope: "/"` explicitly to remove doubt (an install from `/listing/123` could otherwise scope to `/listing/`). | <https://developer.apple.com/videos/play/wwdc2023/10120/> (Jun 2023); WebKit source |
| `id` | Since 16.4; with the user-edited name it identifies each install; multiple installs allowed ("to support multiple accounts"). | <https://webkit.org/blog/13966/webkit-features-in-safari-16-4/> (2023-03-27) |
| `icons` | Since 15.4, used only if there is no `apple-touch-icon` and `purpose` is `any`/omitted — **`apple-touch-icon` wins**. No icon at all → monogram (16.4+). Safari 17.2 improved icon loading (keeps all sizes; prefers a 1024×1024 or SVG full-bleed square). | <https://webkit.org/blog/12445/new-webkit-features-in-safari-15-4/> (2022-03-14); Web Push post (2023-02-16); Safari 17.2 notes |
| `theme_color` | Supported since iOS 15 (with `<meta name="theme-color">`, which wins in WebKit's code). For Safari 26+ BCD notes it is "only used for installed web apps" (community-tested, PR #28503, 2026-05-15). An Apple engineer (bug 317153, 2026-06-16): the status bar "should automatically appear in the same color as the webpage"; a 27.0.1 report (bug 325807, 2026-09-30) says runtime `theme-color` changes have no effect. Treat as unsettled. | <https://webkit.org/blog/11989/new-webkit-features-in-safari-15/>; BCD; bugs.webkit.org |
| `background_color` | BCD: not supported on iOS (parsed, exposed to Apple apps since 17.0, no documented use). | BCD; `_WKApplicationManifest.h` |
| `orientation` | BCD: not supported (parsed since 2023-03). | BCD |
| `shortcuts` | Not on iOS (macOS Sonoma, Safari 17.4, only). | <https://webkit.org/blog/15063/webkit-features-in-safari-17-4/> (2024-03-05); BCD |
| `share_target`, `protocol_handlers`, `launch_handler`, `file_handlers` | Not supported. | BCD; see §10 |
| `color_scheme_dark` | Landed in WebKit trunk 2026-05-29, not in Safari 27.0 notes — not shipped as far as documented. | WebKit commit 4335716855 |

`display-mode` media query: WebKit's code returns the manifest's `display` (or `browser` with no manifest), but BCD
says `fullscreen` matches in a `standalone` app (bug 264218, open since 2023-11). Contradictory; detect standalone
with `navigator.standalone === true` as a fallback and verify on the device.

## 2. Icons and launch screen

- Use `<link rel="apple-touch-icon" href="/apple-touch-icon.png">` (180×180 PNG, opaque) — it takes precedence over
  manifest icons (Safari 15.4 post; Web Push post 2023-02-16).
- **No generated splash.** No primary source says iOS builds a launch screen from the manifest (BCD:
  `background_color` unsupported). Apple's only guidance is `<link rel="apple-touch-startup-image">`, default "a
  screenshot of the web application the last time it was launched"
  (<https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/SafariWebContent/ConfiguringWebApplications/ConfiguringWebApplications.html>,
  archived 2016-12-12, iOS ≤10 era). It still works, with bugs: light/dark variant fixed at install time (bug
  259328, iOS 17); data/blob URLs broke in 17.6.1 (bug 280262); **iOS 27.0.1 regression** — `device-height` in the
  startup image's `media` query is evaluated as Safari's window height at add time, so images matched on it never
  show and the app launches black; workaround: match on `device-width` + pixel ratio only (bug 326500, 2026-10-06,
  imported to Apple as rdar://189304868). White flash on cold start despite dark `background_color` (bug 311569,
  2026-04-06, iOS 17/26).
- Practical: a launch screen is optional polish; the SPA should paint its background colour in inline CSS in
  `index.html` so the first frame is not white.

## 3. `viewport-fit=cover` and safe areas for a bottom tab bar

- `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">` lays the page out
  edge to edge; pad fixed UI with `env(safe-area-inset-*)`, e.g. tab bar
  `padding-bottom: max(8px, env(safe-area-inset-bottom))`. A full-width bottom bar under the home indicator
  without this "is very hard to use". `env()` since iOS 11.2.
  <https://webkit.org/blog/7929/designing-websites-for-iphone-x/> (2017-09-22); BCD.
- **Status bar:** `apple-mobile-web-app-status-bar-style` (`default` / `black` / `black-translucent`) only acts in
  standalone (<https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/SafariHTMLRef/Articles/MetaTags.html>,
  2014). An Apple engineer now calls `black-translucent` deprecated: "Please remove this key, and the status bar
  should automatically appear in the same color as the webpage … Home Screen web apps does not support drawing
  arbitrary content below the status bar." (bug 317153, 2026-06-16, iOS 26/27). → Don't use black-translucent;
  give the page a top header whose colour matches the page background and still pad with
  `env(safe-area-inset-top)` (it may be 0 or not, see below).
- **Regressions to design around (user bugs; 27.0.1 state unsettled):** iOS 26.1 lost edge-to-edge drawing,
  `env(safe-area-inset-top)` returned 0 (bug 300445, Apple: "an issue in Safari, not in WebKit"); reported fixed in
  26.2, reopened by an Apple engineer "Confirmed in iOS 27 beta" (bug 301994, 2026-08-04); letterboxed launch on 27
  betas 2–3, gone by beta 4 (bug 317153, closed 2026-10-09); iOS 27.0.1: system-picked band/blur under the status
  bar (bug 325807, 2026-09-30); an unreachable bottom strip the height of the status bar (bug 313800, 2026-04-30,
  26.x/27 beta). Safari 26.1 fixed "a bottom gap appearing on layouts with viewport-sized fixed containers on iOS"
  (<https://webkit.org/blog/17541/webkit-features-for-safari-26-1/>, 2025-11-03).
- Design rule that survives all of these: layout from `env()` values, never hard-coded pixel insets; test on the
  operator's iPhone on 27.0.1.

## 4. Links that leave the scope, and getting back

- **Apple, iOS:** "In Home Screen web apps on iOS, links outside the scope will open in Safari View Controller."
  (WWDC23 "What's new in web apps", <https://developer.apple.com/videos/play/wwdc2023/10120/>, June 2023, iOS
  16.4/17; verified in the transcript — the sentence is in the iOS part, not the Mac part.) Safari View
  Controller = the in-app Safari sheet with a Done button and an "open in Safari" control.
  - (a) same-window link to another origin and (c) a portal page: in-app sheet; user taps **Done** (or edge-swipes
    from the sheet's first page, per firt.dev **(secondary)**, 2019) and is back in the app where they left it.
  - (b) `target="_blank"` / `window.open`: no iOS-specific Apple statement. Marcos Cáceres (manifest editor):
    "Safari seems to do the right thing cross-origin popups at least - it opens an overlay window/browser"
    (<https://github.com/w3c/manifest/issues/989>, 2021-08-10). Community reports disagree (sheet vs Safari).
    The Mac rule "links loaded through window.open will always open in the web app regardless of scope" is
    stated for **macOS Dock apps only** (WWDC23). → device test.
  - If something ends up in Safari proper, the only way back is the app switcher or the "◀ app" status-bar
    breadcrumb.
- **An out-of-scope sheet navigating back into scope:** "When the external URL redirects or points to a URL within
  the scope … the PWA closes the browser and loads the content in the standalone window."
  (<https://firt.dev/ios-12.2/>, 2019-03-26, iOS 12.2 — **(secondary)**, no Apple statement for iOS 17–27.)
- **Universal links:** if a portal's native app is installed, a portal link might open that app instead
  (speculation; test).
- macOS-only, for contrast: Safari 18 "Added support for opening links directly in web apps on macOS"
  (<https://webkit.org/blog/15865/webkit-features-in-safari-18-0/>, 2024-09-16). Nothing equivalent on iOS
  through 27.0.

## 5. Back gesture in a single-page app

- No browser UI and no system back button in standalone ("Only a status bar appears", Apple archive doc 2016).
- **Edge-swipe back/forward exists and covers `pushState` routes:** "Progressive Web Apps now have the navigation
  gestures enabled, similar to Safari. It works for every URL within your scope, including client-side routes …
  there is no back button on the screen" (<https://firt.dev/ios-12.2/>, 2019, iOS 12.2+, **(secondary)**);
  corroborated by Ionic's Liam DeBeasi, "web apps installed to the home screen have swipe gestures … not
  currently possible to [disable]" (<https://github.com/w3c/manifest/issues/1041>, 2022-06-08, iOS 15/16).
- It fires `popstate` (Safari 18 added `PopStateEvent.hasUAVisualTransition` for swipe-animated navigations,
  Safari 18 notes, 2024-09-16; Navigation API since Safari 26.2 per BCD).
- **Gesture-less pushState entries are skipped**: "history entries added by JS (e.g. via history.pushState()) get
  skipped on user navigation unless they were added with a user interaction" — Chris Dumez (Apple/WebKit),
  <https://bugs.webkit.org/show_bug.cgi?id=248303> (2023-01-03, iOS 16+). So a programmatic `navigate('/browse')`
  after login is not a swipe-back target.
- → The phone shell needs its own visible back affordance on drilled-in pages; the swipe is a bonus. Whether the
  swipe behaves the same in iOS 26/27 standalone windows is undocumented → device test.

## 6. Storage persistence and the 7-day eviction

- **Exempt from the 7-day cap.** "Web applications added to the home screen are not part of Safari and thus have
  their own counter of days of use … We do not expect the first-party in such a web application to have its
  website data deleted" (<https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/>, 2020-03-24,
  iOS 13.4+). Current policy page, stronger: "The first-party domain of home screen web applications is exempt
  from ITP's 7-day cap on all script-writeable storage … In addition, the website data of home screen web
  applications is kept isolated from Safari." (<https://webkit.org/tracking-prevention/>, last modified
  2023-04-27). WebKit source agrees (`ResourceLoadStatisticsStore::domainsExemptFromWebsiteDataDeletion`).
- **Quota/eviction (Safari 17+):** standalone apps get the same quotas as the browser (origin up to ~60% of disk,
  overall 80%); eviction under storage pressure is whole-origin LRU, skipping origins with an active page or in
  persistent mode; default mode is best-effort. `navigator.storage.persist()`: "WebKit currently grants a request
  based on heuristics like whether the website is opened as a Home Screen Web App."
  (<https://webkit.org/blog/14403/updates-to-storage-policy/>, 2023-08-10, iOS 17+). Not automatic — the app must
  call it; source shows it succeeds for the Home Screen app's domain. `persist()` exists since Safari 15.2 (BCD).
- **Separate from Safari.** "Home Screen web apps have a standalone, app-like experience on iOS, with separate
  cookies and storage from the browser" (WWDC23). Brent Fulgham (Apple): "by design … isolated entities without
  shared state with the browser" (<https://bugs.webkit.org/show_bug.cgi?id=181849>, 2022-02-01). Since iOS 17.2
  Safari **copies cookies** at the moment of adding to the Home Screen ("Added support for copying cookies when
  saving a website to the Home Screen on iOS and iPadOS. (104438319)", Safari 17.2 release notes, 2023-12-11) —
  localStorage is not copied (WWDC23 says so for Mac; nothing says otherwise for iOS).
- A newer WebKit-trunk "time-based eviction" (180 days unused, 2026-05) is disabled whenever tracking prevention is
  on and outside full browsers; not in the Safari 27 notes — irrelevant here as far as documented.
- Removing the icon: no primary source says what happens to its storage (assume it is gone; unconfirmed).
- Known cookie bug: session cookies randomly lost in Home Screen apps on 17.2–17.5 (bug 272325) — cookies only.

**Does the Supabase session survive?** Yes, as far as primary sources go, on iOS 17–27: ITP skips the app's
domain, quota eviction is irrelevant for a few KB (and `persist()` removes it), and Supabase refresh tokens
"never expire but can only be used once" unless time-boxed sessions are configured
(<https://supabase.com/docs/guides/auth/sessions>). Caveats: the user must sign in **inside the installed app** (a
Safari sign-in does not carry over — our session is localStorage, not cookies); re-adding the icon likely means
signing in again.

## 7. Google OAuth redirect inside the installed app

The chain: `location.assign` → Supabase `/authorize` → `accounts.google.com` → Supabase callback →
`${origin}/browse#access_token=…&refresh_token=…` (implicit flow).

- **Is Google allowed?** Google bans OAuth in "an embedded user-agent under the developer's control"
  (<https://developers.google.com/identity/protocols/oauth2/policies>, updated 2026-08-05) and names
  SFSafariViewController as the supported iOS alternative
  (<https://developers.google.com/identity/gsi/web/guides/supported-browsers>, 2026-09-01). Out-of-scope pages
  open in Safari View Controller on iOS (WWDC23) → Google should not return `disallowed_useragent`. (Before iOS
  12.2, Google loaded inside the standalone view and was blocked — auth0 community 2017 **(secondary)**.)
- **Where does it land?** Apple documents the outbound half (out-of-scope → sheet) for tapped links; for a
  JavaScript `location.assign` there is no iOS statement. The return half — the sheet closing and the in-scope
  `/browse#…` loading in the standalone window, and the sheet sharing the app's storage — is documented only by
  firt.dev for iOS 12.2 (2019, **(secondary)**) and a 2019 firebase report ("google login on the home screen is
  fixed!", <https://github.com/firebase/firebase-js-sdk/issues/77>). 2026 GitHub PRs claim the session strands in
  the sheet; none was tested on an iPhone and they contradict each other. **No primary source settles it; a device
  test is required.**
- **Implicit flow helps:** the tokens travel in the URL fragment, so whichever context loads `/browse#…` signs
  itself in — no code verifier needed. If that context is the standalone window or a sheet sharing its storage,
  sign-in completes; if it is Safari proper (e.g. the user taps "open in Safari" mid-flow), the session strands
  in Safari. PKCE would be worse: the verifier sits in the starting context's storage and "the code exchange must
  be initiated on the same browser and device where the flow was started"
  (<https://supabase.com/docs/guides/auth/sessions/pkce-flow>). → keep the implicit flow for the phone.
- Related Safari 17.2 fixes, platform unstated (likely macOS): "sign in pages sometimes unexpectedly open in
  Safari instead of the web app (113520837)", "some login pages unexpectedly open in Safari (115527738)".
- **Email links** (password reset) open in the default browser, not the web app (WWDC23, Mac wording; very likely
  iOS too) — the reset completes in Safari, then the user signs in with the new password inside the app.
- **Fallbacks, in order:** (1) email + password, which never leaves the app — guaranteed; (2) explicit manifest
  (`scope: "/"`, `start_url: "/browse"`) so the return is unambiguously in scope; (3) popup flow (`window.open('')`
  synchronously in the tap handler, then `signInWithOAuth({ skipBrowserRedirect: true })` and point the popup at
  `data.url`; callback page writes the session and closes) — relies on unverified storage/opener sharing; (4) Google
  Identity Services + `signInWithIdToken` — on ITP browsers the button needs redirect mode and One Tap opens a
  popup (<https://developers.google.com/identity/gsi/web/guides/display-button>, 2025-05-19), so it does not
  avoid the problem.

## 8. Keyboard and `100dvh`

- `svh`/`lvh`/`dvh` since Safari 15.4 (<https://webkit.org/blog/12445/new-webkit-features-in-safari-15-4/>). The
  keyboard is not browser UI: it does not change viewport units (<https://web.dev/blog/viewport-units>,
  2022-11-29) and resizes only the **visual** viewport; `position: fixed` elements "remain in place and can be
  obscured by the OSK" (<https://developer.chrome.com/blog/viewport-resize-behavior>, 2022-10-28, describing iOS).
- `env(safe-area-inset-bottom)` stays non-zero while the keyboard is open (bug 217754, open since 2020).
- `interactive-widget` viewport key: BCD says unsupported on iOS; WebKit enabled it by default on Apple platforms
  on 2026-08-13 (commit 44ecc0e346, bug 321543), after Safari 27 branched — not shipped as far as documented.
  VirtualKeyboard API / `keyboard-inset-*`: not supported (bug 230225; standards position #16 open).
- Standalone keyboard bugs in 2026 (user reports): `visualViewport.height` ~68 px short after the first
  show/hide, bottom bars ride up (bug 322900, 26.x); resume with a phantom keyboard inset (bug 323322, 26.6.1);
  extra bottom inset while the keyboard is open (bug 292603, active 2026-10-07). With `viewport-fit=cover`,
  `100svh`/`visualViewport.height` exclude the insets while `vh` includes them (bug 254868).
- → Hide the bottom tab bar while a text field is focused (or size from `visualViewport` on `resize`); use
  `100dvh` for full-height shells, never `100vh`; keep inputs near the top on the paste-a-link screen.

## 9. Pull-to-refresh and overscroll

- Safari tabs have native pull-to-refresh (<https://support.apple.com/guide/iphone/browse-the-web-iph1fbef4daa/ios>,
  iOS 27). **Standalone web apps have none** — consistent community reports only **(secondary)**, no Apple page.
  → The feed needs its own refresh (a button, or a JS pull-to-refresh) and should refetch on `visibilitychange`.
- `overscroll-behavior` since Safari 16 (partial: no effect on containers without overflow, bug 243452); 16.4 fixed
  `none` on short pages (<https://webkit.org/blog/13152/webkit-features-in-safari-16-0/>, 2022-09-12; Safari 16.4
  post). The body can still rubber-band (Apple's Simon Fraser, bug 214781, 2020). Safari 27.0 "Fixed top and
  bottom fixed-position elements flickering during rubber-band scrolling".

## 10. Share sheet: can a shared link reach the installed app?

- **`share_target`: not supported.** BCD `manifests/webapp/share_target`: Safari/iOS `false`; WebKit bug 194593 "Add
  support for Web Share Target API" NEW since 2019-02-13, no assignee, last activity 2026-05-23 (only +1s)
  (<https://bugs.webkit.org/show_bug.cgi?id=194593>); WebKit's position "neutral" with spoofing concerns
  (<https://github.com/WebKit/standards-positions/issues/11>, closed 2023-03-23). Not in Safari 26.x or 27.0. The
  installed app cannot appear in the share sheet.
- **Outgoing `navigator.share()` works** (since Safari 12.1/iOS 12.2; files since 14 — BCD).
- **Shortcuts:** a Shortcut can sit in the share sheet ("Turn on Show in Share Sheet",
  <https://support.apple.com/guide/shortcuts/run-a-shortcut-from-another-app-apd163eb9f95/ios>), but "Open URLs
  takes any links passed as input and opens them in Safari"
  (<https://support.apple.com/guide/shortcuts/share-actions-apdaf74d75a5/ios>, iOS 27 edition). An in-scope URL like
  `https://<host>/add?url=…` therefore lands in **Safari**, not the installed app. Universal links need a native
  app's associated-domains entitlement (<https://developer.apple.com/documentation/xcode/supporting-associated-domains>).
  No source shows "Open App" listing a web app. An undocumented `webapp://<host>/` scheme opens the installed app
  but always at `start_url`, dropping the path and query (PairDrop #379/#391, 2025-02, iOS 18.3.1; Discourse
  2025-07 **(secondary)**) and reportedly failed on an iOS 26.x build in 2026-04 (Glide forum) — do not build on it.
- **The one documented route:** the share-sheet Shortcut runs "Get Contents of URL" as a POST of `{url}` to our API
  (<https://support.apple.com/guide/shortcuts/request-your-first-api-apd58d46713f/ios>), the backend files it in a
  per-user inbox, and the app shows it on next open / `visibilitychange`. Needs a per-user token in the Shortcut —
  the static `VITE_API_TOKEN` cannot identify a user (CLAUDE.md § Territories → Frontend).

## 11. Chrome on iOS

- Since iOS 16.4 third-party iOS browsers (Chrome, Edge, Firefox, Orion) can offer Add to Home Screen; a site that
  qualifies opens as a WebKit Home Screen web app "no matter which browser added the website" (Web Push post,
  2023-02-16). Google: "If web app is available, the shortcut opens the app. If web app isn't available, the
  shortcut opens in your default browser" (<https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DiOS>,
  undated). iOS 17: Add to Home Screen also from Safari View Controller (WWDC23). Whether iOS 26's "Open as Web
  App" toggle appears when adding from Chrome is undocumented — install from Safari.

## What W1 should do (derived)

1. Add `manifest.webmanifest` (`name`, `short_name`, `id: "/"`, `start_url: "/browse"` — or the feed route —,
   `scope: "/"`, `display: "standalone"`, icons) and `<link rel="manifest">`; add `apple-touch-icon` (180 px); add
   `viewport-fit=cover`; no `black-translucent`. This also makes iOS 18 open it standalone.
2. Tab bar padded with `env(safe-area-inset-bottom)`; hidden while typing; shell heights in `dvh`.
3. Visible back button on drilled-in screens; refresh control on the feed; refetch on `visibilitychange`.
4. Call `navigator.storage.persist()` once after sign-in in standalone.
5. Login screen offers email + password (guaranteed in-app) alongside Google until the device test passes.
6. Paste-a-link stays the input; a Shortcut → API inbox is possible later but needs a per-user token.

## Device test (operator's iPhone, iOS 27.0.1, ~15 min, no code)

1. Safari → `https://<prod>/browse` → Share → Add to Home Screen, Open as Web App on. Second icon from a deep page
   (`/listing/<id>`).
2. Launch → tap "Sign in with Google": sheet with Done, Safari, or full screen? Any Error 403
   `disallowed_useragent`?
3. Finish Google: does the sheet close and the app show `/browse` signed in? Or does `/browse` load inside the sheet
   (then tap Done — is the app signed in)? Force-quit, relaunch: still signed in? Safari on the same URL: signed out
   (proves isolation)?
4. Browse → listing by tap → swipe from the left edge: does it go back?
5. Tap a portal link (`target="_blank"`): sheet, Safari, or the portal's app? How back?
6. Repeat 2–3 from the deep-page icon (default-scope risk).
7. Share a portal link from Safari: the app is absent from the share sheet (expected).
8. Optional, with a Mac: Safari Develop → device → the web app, to read `localStorage` (`sb-<ref>-auth-token`)
   and `await navigator.storage.persist()`.

## Open / contradictory

- `display-mode` matching (BCD vs WebKit source); status-bar colour and `theme-color` on 26/27; safe-area-top on
  27.0.1; launch-screen behaviour (screenshot vs black/white) — all need the device.
- Undocumented: what `minimal-ui`/`browser` do on 26+; the toggle in third-party browsers; storage on icon removal;
  pull-to-refresh absence in standalone (community-only); the OAuth return hand-off on iOS 17–27.
- Manifest support start: WebKit 2025 posts say "iOS 11.4 in March 2018"; BCD says 11.3 (the March 2018 release).
