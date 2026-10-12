# The Phone app's service worker beside buildSkew and lazyChunk

Research for [#1764](https://github.com/waiff/sreality/issues/1764), part of the Phone app map
[#1759](https://github.com/waiff/sreality/issues/1759). Written 2026-10-12 against `main` at
`16bb77a4`. Throwaway research branch `research/phone-app-sw`; never merged.

## Recommendation

**A hand-written, version-agnostic service worker: `frontend/public/sw.js`, about 90 lines, no
new dependency.** It touches exactly two kinds of request on its own origin, navigations and
`/assets/*`, and lets everything else go to the network untouched. Navigations are
network-first (served from navigation preload) with the last good `index.html` as a fallback
after 3 s, on a network error or on a 5xx; hashed `/assets/*` are cache-first. It never precaches, never
reloads a page, and never decides which build runs, so it has no update flow of its own to
coordinate: buildSkew stays the one place that notices a new build and lazyChunk stays the one
place that recovers from a missing chunk. The fonts move from Google Fonts into the build
(six woff2 files under `/assets/`), so the worker needs no cross-origin rule at all.

`vite-plugin-pwa` is rejected: its precache makes the worker change on every deploy, which
turns the worker's own lifecycle into a second app-update mechanism competing with buildSkew,
downloads every chunk of every build to the phone (maps and charts included, which the phone
never opens), and adds a build-time dependency tree for what is a 90-line file here.

## What the code does today (the constraints)

Read on `main` `16bb77a4`:

- **Every deploy renames every chunk.** `frontend/src/lib/lazyChunk.ts` (header) and
  `docs/architecture.md` record that a one-character change rotates 30 of 30 chunk hashes.
  `vite.config.ts` deliberately has no `manualChunks`; the entry is "the whole app shell" and
  index.html emits zero `modulepreload` links. So a precache of a whole build is thrown away
  by the next deploy, while a URL-keyed cache of what was actually fetched is exact (a hashed
  name never changes content).
- **`Caddyfile`**: `/healthz` exclusive; `handle_path /assets/*` serves real files only and
  stamps `Cache-Control: public, max-age=31536000, immutable` on a file that EXISTS (a missing
  chunk gets a 404 with `no-store` from `handle_errors`); everything else goes to a `handle`
  block with `Cache-Control: public, max-age=0, must-revalidate`, three security headers and
  `try_files {path} /index.html`. So today `/sw.js`, `/manifest.webmanifest` and
  `/apple-touch-icon.png` all answer **200 `text/html`** with the SPA shell when the file is
  absent (read from the config; the ticket states the same).
- **`Dockerfile`**: node build stage runs `npm run build`, then `COPY --from=build /app/dist /srv`
  into `caddy:2-alpine`. `frontend/.dockerignore` does not exclude `public/`.
- **buildSkew** (`lib/buildSkew.ts` + `lib/useBuildSkew.ts`, mounted once in `Shell`): on
  `visibilitychange` and `focus`, throttled to once per 5 minutes, it compares the DOM's entry
  `<script src>` with the one in `fetch('/index.html', { cache: 'no-store' })` and, if they
  differ, pushes a sticky toast with a Reload button, once per document. It deliberately does
  **not** probe on mount: "this tab just fetched the very index.html the probe would read, so it
  is current by construction." A service worker that can serve a cached `index.html` breaks
  that sentence, which is the one change buildSkew needs (below).
- **lazyChunk**: a rejected chunk import reloads the page (`location.reload()`), rate-limited
  to once per 60 s per tab through `sessionStorage`; a second failure inside the minute throws
  `StaleBuildError('blocked')`; `navigator.onLine === false` throws `StaleBuildError('offline')`
  instead of reloading. The other reload sites are the buildSkew toast and the `ErrorBoundary`
  Reload button. All three are ordinary navigations, so all three pass through the worker's
  navigation handler.
- **Cross-origin traffic**: the API is `VITE_API_BASE_URL` (another Railway host than the SPA;
  after the brand-domain move still another host), Supabase is `VITE_SUPABASE_URL`, listing
  photos are `${VITE_API_BASE_URL}/images/<key>?v=2` (`lib/imageUrl.ts`): a 302 cached 1 h
  (`api/routes/images.py`, `_REDIRECT_MAX_AGE`) to a presigned R2 URL whose bytes carry
  `max-age=2592000`. Map tiles come from `tiles.openfreemap.org` (`lib/basemap.ts`). The HTTP
  cache already does the right thing for all of these; a worker can only make them worse.
- **Same-origin non-asset reads**: `/index.html` (the buildSkew probe) and
  `/workflow-docs.json` (`lib/workflowDocs.ts`, ~180 KB, changes with backend work).
- **Fonts**: `index.html` preconnects to `fonts.googleapis.com` / `fonts.gstatic.com` and loads
  one stylesheet for Fraunces (opsz 9..144, wght 500/600), Inter (400/500/600) and JetBrains Mono
  (400/500), `display=swap`. Fetched 2026-10-12 with an iPhone user agent: the CSS answers
  `access-control-allow-origin: *` and `cache-control: private, max-age=86400`, lists 39
  `@font-face` blocks over 16 files (the `subset=` parameter is ignored by `css2`; `unicode-range`
  picks the files), of which Czech needs only the `latin` + `latin-ext` ranges: **6 variable
  woff2 files, 303 KB in total** (Fraunces 67 + 60 KB, Inter 48 + 85 KB, JetBrains Mono 31 + 12
  KB), each `cache-control: public, max-age=31536000` with `access-control-allow-origin: *`.
  All three families are SIL Open Font License, which permits self-hosting.
- **Map decisions that bind** (#1759): one SPA, one address, one deploy; online only, app shell
  cached; offline reading and queued writes out of scope; web push is the first thing after this
  map (so the worker must be a file we can add a `push` handler to); install from the Railway
  address now, reinstall once after the brand-domain move (#1694).

## Hand-written versus vite-plugin-pwa

What the plugin is, from its own docs and the npm registry (checked 2026-10-12):
`vite-plugin-pwa` 2.0.0 depends on `workbox-build ^7.4.1` and `workbox-window ^7.4.1`
[R10]; `workbox-build` 7.4.1 alone has 37 direct dependencies, among them `@babel/core`,
`@babel/preset-env`, `rollup` 4 and four `@rollup/plugin-*` packages [R11]; the client runtime
`workbox-window` is 2.3 KB gzipped [R12]. `generateSW` "will generate the service worker for
you"; `injectManifest` "will compile your custom service worker and inject its precache
manifest" [R13]. `registerType: 'prompt'` is the default [R14]; `'autoUpdate'` forces
`skipWaiting` + `clientsClaim` and warns that "the user can lose data in any browser windows/tabs
in which the application is open and is filling in a form" [R15]. The default precache glob is
`**/*.{js,css,html}` [R16], and Workbox precaching downloads every manifest entry at install,
later only entries whose revision changed [R17] (here: all of them, every deploy). Workbox's own
update guide advises a reload prompt rather than silent `skipWaiting` when an app "uses
precaching extensively" with lazy-loaded assets, and says skipping the prompt is fine when
relying primarily on runtime caching [R18], which is the hand-written design's position.

| | Hand-written `public/sw.js` | `vite-plugin-pwa` (`generateSW`) | `vite-plugin-pwa` (`injectManifest`) |
| --- | --- | --- | --- |
| New dependencies | none | dev: `vite-plugin-pwa` → `workbox-build` (pulls Rollup, Babel, Terser plugins); runtime: `workbox-window` + Workbox modules in the SW | same tree; you write the SW but still import Workbox modules or a precache manifest |
| Does the SW change on every deploy? | **no** (only when we edit it) | **yes**: the precache manifest is in the SW file, and every deploy rotates every hash | yes, if the injected manifest is used |
| Who decides which build runs? | the network (`index.html` network-first) | the SW: the precached `index.html` is served until the new SW activates | the SW |
| What a deploy costs the phone | the entry + the chunks it actually opens | the whole precache list on install (default glob `**/*.{js,css,html}`: every chunk, maplibre and recharts included) | same unless the glob is trimmed |
| Update UX | buildSkew's existing toast; nothing new | `registerType: 'prompt'` adds a second "new content" prompt (`needRefresh`); `'autoUpdate'` reloads pages on its own, against the "offered, never imposed" rule in `docs/architecture.md` | same as `generateSW` |
| Future web push | add two listeners | only through the `importScripts` option | add listeners |

The deciding line is the second row. With a precache, a deploy means a new worker; a new worker
means `installing → waiting → activate`, and the app must choose between serving the old
precached shell until every tab closes (stale for days in an iOS app that is rarely cold-started)
or `skipWaiting` + reload (an imposed reload, and an old document then lazy-loading chunks that
the new worker's cleanup just deleted). buildSkew already answers "is there a newer build?" from
the source of truth. A worker that caches by URL and fetches `index.html` from the network has
nothing to answer: the next navigation is the newest build whenever the network is up.

## The worker, per URL class

All rules apply to `GET` requests only; any other method returns from the fetch listener without
calling `respondWith`, which hands the request to the network exactly as if there were no worker.

| URL class | Strategy | Notes |
| --- | --- | --- |
| Navigations (`request.mode === 'navigate'`): every SPA route, `/`, `start_url`, OAuth returns (`/browse?code=…`, `#access_token=…`) | **Network-first** using `event.preloadResponse` (navigation preload on), else `fetch(request)`. On a 200 `text/html`, non-redirected response, store a copy under one shell key (`/index.html`) in `shell-v1`. Fall back to that copy after **3 s**, on a network error or on a 5xx; with no copy yet, wait for the network. | One key for every route: Caddy answers every route with the same `index.html`. The URL, query and hash are never rewritten, so Supabase's `detectSessionInUrl` still sees its tokens. A late network response still refreshes the cache (`waitUntil`). |
| `index.html` fetched by script (buildSkew's probe) | **Not handled** (network) | The probe must see the deployed build; serving it from the cache would make every probe agree with itself. |
| `/assets/*` (same origin; JS, CSS, and the self-hosted fonts) | **Cache-first**, `assets-v1`. On a miss, fetch; store only `status === 200 && type === 'basic'`; return whatever the network said (a 404 goes back to lazyChunk untouched and is never stored). | Content-addressed names, so a hit is always correct. Prune: keep the newest 200 entries by the stored `Date` header, drop any older than 14 days; run in `activate` and once per worker lifetime after a shell refresh. Pruning is always safe: a pruned chunk is refetched, or 404s into lazyChunk's reload. |
| Google Fonts | **Removed**: self-host the six woff2 under `src/assets/fonts/`; Vite hashes them into `/assets/`, so they ride the `/assets/*` rule | If W1 keeps Google Fonts instead: add `crossorigin` to the stylesheet link (the CSS answers `access-control-allow-origin: *`, so the response is CORS, not opaque), stylesheet stale-while-revalidate, `fonts.gstatic.com` cache-first, one year, cap 30 entries (Workbox's Google Fonts recipe [R33]); that is the only cross-origin rule the worker would carry. |
| `/manifest.webmanifest`, `/icons/*`, `/apple-touch-icon.png` | **Not handled** (network + HTTP cache) | Tiny, and iOS reads them at install: "the only way to get the new manifest metadata is to re-install the app" [R19]. Caddy serves the manifest `no-cache` and icons `max-age=86400` (diff below). |
| `/sw.js` | n/a | The browser fetches the worker script itself, outside any fetch handler. |
| `/workflow-docs.json`, `/healthz`, any other same-origin path | **Not handled** | |
| API (`VITE_API_BASE_URL`, including `/images/*`), Supabase (REST, Auth, Storage; Realtime is a WebSocket, which a worker never sees), map tiles, portal CDNs | **Not handled** | Never cached by the worker. The `/images/*` 302 and the R2 bytes keep their HTTP caching exactly as designed in `api/routes/images.py`; not calling `respondWith` means the worker never sees the redirect hop. |

Why a 3-second timeout rather than plain network-first: the offline cookbook's own caveat for
"network falling back to cache" is that users on an "intermittent or slow connection … have to
wait for the network to fail" [R34]; Workbox's `NetworkFirst` has `networkTimeoutSeconds` for
exactly this ("any network requests that fail to respond within the timeout will fallback to the
cache"), and its page-cache recipe uses 3 s [R35].

### Lifecycle

- `install`: `self.skipWaiting()`. `activate`: enable navigation preload, delete caches whose
  name is not `shell-v1` / `assets-v1`, prune `assets-v1`, `self.clients.claim()`. Both are safe
  only because the worker is version-agnostic: an old document controlled by a new worker gets
  the same answers it got before (same URL-keyed cache, same network), and nothing in `activate`
  deletes an asset a running build still needs except by the age/count prune, which lazyChunk
  absorbs.
- No `controllerchange` listener, no `postMessage` protocol, no reload from the worker.
- Cache names carry a revision (`-v1`) that changes only when the worker's storage format
  changes; editing the routing logic does not need a rename.

Lifecycle facts this relies on: `skipWaiting` "allows this service worker to progress from the
registration's waiting position to active even while service worker clients are using the
registration" [R1]; its known risk is that the new worker is "likely controlling pages that were
loaded with an older version" [R2], which matters only when the worker's answers depend on its
version (they do not here). Navigation preload starts the navigation's network request "while the
service worker is booting up" (worker boot can cost up to about 500 ms on slow devices) [R3];
`event.preloadResponse` is `undefined` when preload is off or the request is not a navigation
[R4]; Safari and iOS have it since 15.4 [R5]; `skipWaiting` and `clients.claim` since 11.1 [R6].
A navigation must not be answered with a redirected response: Fetch turns a worker's response
into a network error when "request's redirect mode is not "follow" and response's URL list has
more than one item" [R7] (WebKit: bug 190041 [R8]); an `<img>` or `fetch()` request uses
`follow`, so the `/images/*` 302 would be fine even if the worker did touch it, but it does not.

Accepted cost: a fetch listener receives every request a controlled page makes, its own origin
or not [R9], so each feed photo and API call passes through the listener's early `return` (no
`respondWith`, so the network behaves exactly as without a worker). This adds a worker dispatch,
plus a wake-up when the worker was stopped. Not measured here; the W1 walk on the iPhone is the
check (scroll the feed with and without the worker).

## How updates land (coexistence with buildSkew and lazyChunk)

Three things can be stale, each with one owner:

1. **The running document** (the JS already in memory). Owner: **buildSkew**, unchanged in shape:
   on `visibilitychange` / `focus`, throttled 5 min, a `no-store` probe the worker passes through,
   a sticky toast, Reload. The Reload is a navigation, the worker answers it network-first, so
   it lands on the deployed build.
2. **The cached shell** (`index.html` in `shell-v1`). Owner: **the navigation handler**. It is
   refreshed on every online navigation and only served when the network is down or slower than
   3 s. The one consequence for buildSkew: a document can now START stale (it booted from the
   fallback). So `useBuildSkew` gains one deferred probe on mount, only when
   `navigator.serviceWorker.controller` is set (10 s after mount, off the critical path; the
   existing 5-minute throttle then covers the next focus).
3. **The worker script** (`/sw.js`). Owner: **the browser's update check**, plus one explicit
   `registration.update()` on `visibilitychange → visible`, throttled to once an hour, in
   `lib/serviceWorker.ts`. The worker changes rarely and holds no build, so a worker a few days
   old is harmless; the hourly check exists so a fix or the kill switch reaches an installed
   iPhone that resumes for weeks without a cold start. The browser checks for a new worker "on a
   navigation to an in-scope page", on a functional event (`push`, `sync`) when no check ran in
   the previous 24 hours, or on `register()` with a changed URL [R2], and installs it only if the
   script is not "byte-for-byte identical" [R1]. A resumed home-screen app is NOT a navigation:
   no WebKit document says so outright (UNCONFIRMED from a WebKit source), but apps in the App
   Switcher "continue from where they left off rather than reloading" (Apple Developer Forums
   thread 123606, a user report) [R20], and the spec only checks on navigations and stale
   functional events. Hence the explicit `update()` on resume, the same idea as the plugin's own
   "periodic service worker updates" guide (an hourly `r.update()`) [R21]. By default the browser
   does not consult the HTTP cache for the worker script (`updateViaCache` defaults to
   `'imports'`; Safari since 11.1) [R1][R22], so `no-cache` on `/sw.js` is hygiene for proxies,
   not what makes updates work.

Why this cannot reload-loop:

- The worker never reloads anything. The only reloads remain lazyChunk's (60 s rate limit,
  `sessionStorage`), the buildSkew toast's and the error boundary's, all user- or
  failure-triggered.
- A chunk 404 after a deploy: the asset handler misses, fetches, gets 404, stores nothing, returns
  the 404. lazyChunk reloads. The reload is a navigation; network-first fetches the new
  `index.html`; the new entry names new chunks. One reload, as today.
- The loop that a cache-first or stale-while-revalidate navigation would create: reload → the
  same cached old `index.html` → the same missing chunk → lazyChunk blocked after 60 s →
  `StaleBuildError`. Network-first is what rules it out, and it is why the shell is not served
  stale-while-revalidate even though that would open faster.
- Degraded case, accepted: network slower than 3 s right after a deploy → the fallback shell is
  the old build; its cached chunks still load; a chunk it never cached 404s → lazyChunk reloads
  → network-first again. If the network is still that slow, the second failure inside 60 s shows
  `StaleBuildError('blocked')` with its Reload button, never a loop.
- Offline: a cached chunk now loads offline (better than today); an uncached one still hits
  lazyChunk's `navigator.onLine === false` branch. buildSkew's probe fails → "no news".

Why no stale shell for days on the iPhone: online, every cold start and every reload gets the
deployed `index.html` from the network, because the shell is never served from the cache first.
A resumed (not cold-started) app is the running-document case, which buildSkew's
`visibilitychange` probe already covers. `visibilitychange` in standalone apps was broken on
iOS 11 and fixed in iOS 12 (WebKit bug 180523), misfired on iOS 13 (bug 201737, fixed January
2020) [R23]; that it fires reliably on foregrounding in iOS 18 is implied by those fixes, not
stated by WebKit (UNCONFIRMED; the W1 walk checks it: deploy, resume, expect the toast). The
probe also listens to `focus`. A worker that installs straight to active also sidesteps WebKit
bug 199110, where a WAITING worker failed to activate if the network process stopped between
two runs of a home-screen app (fixed in iOS 16) [R24]. The residual: the toast shows once per document; an
operator who dismisses it keeps the old build until the next cold start or reload. Whether the
phone shell should also reload by itself when a stale build is found after a long absence (say,
hidden for more than 30 minutes, standalone display mode only) is a change to the "offered, never
imposed" rule in `docs/architecture.md`, so it is listed as an open decision, not built.

## Caddy

Caddy facts the diff relies on: the default directive order puts `header` before the
`try_files` rewrite and both before `handle` [R25]; same-named directives are sorted by matcher,
"a directive with no matcher (i.e. matching all requests) is sorted last", a named matcher or a
multi-path matcher "keeps the order it appears in the Caddyfile" [R26], and `handle_path` is
sorted "at the same level as `handle`" [R27]; `handle` "evaluates a group of directives mutually
exclusively" and only the first matching block runs [R28]; `file_server` answers a missing file
with 404 unless `pass_thru` is set [R28].

The worker file and the manifest must never fall through to `index.html`: a missing `sw.js`
answering 200 `text/html` fails registration with a `SecurityError` (not a JavaScript MIME type),
and a failed update check removes the registration only when it has no worker yet ("If
newestWorker is null, then remove registration map[…]", Update algorithm [R1]). So a worker whose
script disappears, or turns into HTML, stays installed and keeps answering. The retirement path is
the kill-switch file below, never deleting `sw.js`.

Content types in the image: `caddy:2-alpine` (today Alpine 3.23 + Caddy 2.11.7,
[caddy-docker `2.11/alpine/Dockerfile`](https://github.com/caddyserver/caddy-docker/blob/master/2.11/alpine/Dockerfile))
installs `mailcap`, whose `/etc/mime.types` (mailcap 2.1.54 on Alpine 3.23,
[aports APKBUILD](https://gitlab.alpinelinux.org/alpine/aports/-/blob/3.23-stable/main/mailcap/APKBUILD))
maps `js mjs` → `text/javascript` and `webmanifest` → `application/manifest+json` (lines 1990 and
233 of the released `mime.types`, checked 2026-10-12). Go's `mime` package reads that file on
Linux, so `file_server` already sends the right types. The diff still sets them explicitly,
because the base image tag floats (`2-alpine`) and Caddy's `file_server` never sniffs: an
extension the MIME table does not know is served with **no `Content-Type` at all**, and it only
fills the header when nothing upstream set it, so a `header Content-Type …` in the same block wins
([`staticfiles.go` v2.11.7](https://github.com/caddyserver/caddy/blob/v2.11.7/modules/caddyhttp/fileserver/staticfiles.go),
lines 591–599: `if respHeader.Get("Content-Type") == "" { mtyp := mime.TypeByExtension(…); if mtyp == "" { … respHeader["Content-Type"] = nil } … }`).

```diff
 :{$PORT:80} {
 	root * /srv
 	encode gzip zstd
 
 	handle /healthz {
 		respond "ok" 200
 	}
 
 	handle_path /assets/* {
 		…unchanged…
 	}
 
+	# The service worker: a real file or a real 404, never the SPA shell
+	# (a 200 text/html here fails registration, and an installed worker
+	# whose script fails its update check stays installed). no-cache: the
+	# browser already skips its HTTP cache for this script; proxies must too.
+	handle /sw.js {
+		header Cache-Control "no-cache"
+		header Content-Type "text/javascript; charset=utf-8"
+		header X-Content-Type-Options "nosniff"
+		file_server
+	}
+
+	# The web app manifest and the home-screen icons: real files only.
+	handle /manifest.webmanifest {
+		header Cache-Control "no-cache"
+		header Content-Type "application/manifest+json"
+		file_server
+	}
+	@icons path /icons/* /apple-touch-icon.png /apple-touch-icon-precomposed.png /favicon.ico
+	handle @icons {
+		header Cache-Control "public, max-age=86400"
+		file_server
+	}
+
 	handle {
 		header Cache-Control "public, max-age=0, must-revalidate"
 		…unchanged…
 		try_files {path} /index.html
 		file_server
 	}
 
 	handle_errors {
 		header Cache-Control "no-store"
 		respond "{err.status_code} {err.status_text}" {err.status_code}
 	}
 }
```

- `handle` blocks are mutually exclusive, the single-path ones are sorted by specificity, the
  named `@icons` keeps its place and the matcher-less catch-all sorts last [R26][R27], so
  `/sw.js`, `/manifest.webmanifest` and `@icons` win over the catch-all and never reach its
  `try_files`. A missing file in those blocks is a 404 from `file_server`, which `handle_errors`
  stamps `no-store`.
- `/assets/*` keeps its immutable header, still only on files that exist.
- No `Service-Worker-Allowed`: "the default scope … is the directory where the service worker
  script is located", and the header exists only to allow "a scope broader than its own location"
  [R29]. `/sw.js` with scope `/` is already at its maximum.
- A worker script must be a JavaScript MIME type or registration rejects with a `SecurityError`
  [R1]; `text/javascript` qualifies [R30].
- The icon names Safari probes on its own (`/apple-touch-icon.png`,
  `/apple-touch-icon-precomposed.png`) are listed so a probe for one W1 does not ship gets a 404,
  not the SPA shell; which files exist is #1763's call.

## Dockerfile and `public/`

**No Dockerfile change.** Vite copies `public/` "to the root of the dist directory as-is" (Vite
5.4 docs, [The `public` directory](https://github.com/vitejs/vite/blob/v5.4.10/docs/guide/assets.md#the-public-directory);
the repo builds with Vite 5.4.21), and the Dockerfile already copies all of `dist/` to `/srv`.
New files under `frontend/public/`: `sw.js`, `manifest.webmanifest`, the icons (`icons/…`,
`apple-touch-icon.png`; sizes and fields per #1763). The manifest uses relative URLs only
(`"id": "/"`, `"start_url": "/"`, `"scope": "/"`), which keeps it free of hostnames
(`docs/design/brand-domain-and-store/PROGRAM.md`: "Hostnames live in DNS and dashboards, never in
code or manifests") and means the brand-domain move needs only the reinstall already planned.

The self-hosted fonts go under `src/assets/fonts/`, not `public/`: referenced from CSS, Vite
emits them hashed into `/assets/` (all six are above the 4 KB inline limit), so they get the
immutable header and the worker's cache-first rule for free and rotate only when a file changes.

## Registration

`frontend/src/lib/serviceWorker.ts`, called once from `main.tsx` (outside React, beside
`applyTheme`). Shape:

```ts
const UPDATE_CHECK_INTERVAL_MS = 60 * 60_000;

export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  const start = () => {
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .then((registration) => {
        let lastCheck = Date.now();
        document.addEventListener('visibilitychange', () => {
          if (document.visibilityState !== 'visible') return;
          if (Date.now() - lastCheck < UPDATE_CHECK_INTERVAL_MS) return;
          lastCheck = Date.now();
          registration.update().catch(() => {});
        });
      })
      .catch(() => {});
  };
  if (document.readyState === 'complete') start();
  else window.addEventListener('load', start, { once: true });
}
```

- Production builds only: Vite's dev server serves `public/sw.js` too, but a worker caching
  `/assets/*` under the dev server would only confuse.
- Registered for every viewport, not only the phone shell: one SPA, one behaviour; the desktop
  gets cached assets too, and a worker bug shows up on the operator's desktop first. WebKit counts
  an installed web app apart from Safari: "Web applications added to the home screen are not part
  of Safari and thus have their own counter of days of use" for the 7-day storage cap [R31], and
  under the 2023 storage policy a home-screen app gets the same origin quota as the browser (up
  to 60 % of disk) [R32]. Whether its storage is fully separate from Safari's is #1763's question;
  either way the installed app registers its worker on its own first launch.
- Every failure is swallowed: no worker means today's behaviour.

`useBuildSkew` gains the deferred mount probe described above (a `setTimeout(check, 10_000)` when
`navigator.serviceWorker?.controller` is set).

## The worker file (sketch for W1)

```js
/* Version-agnostic on purpose: caches by URL, never by build, so it does not
 * change when the app deploys and never decides which build runs. buildSkew
 * notices new builds; lazyChunk recovers from missing chunks. */
const SHELL_CACHE = 'shell-v1';
const ASSET_CACHE = 'assets-v1';
const SHELL_KEY = '/index.html';
const NAV_TIMEOUT_MS = 3000;
const ASSET_MAX_ENTRIES = 200;
const ASSET_MAX_AGE_MS = 14 * 24 * 3600 * 1000;
let pruned = false;

self.addEventListener('install', (event) => event.waitUntil(self.skipWaiting()));

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    if (self.registration.navigationPreload) await self.registration.navigationPreload.enable();
    for (const name of await caches.keys()) {
      if (name !== SHELL_CACHE && name !== ASSET_CACHE) await caches.delete(name);
    }
    await pruneAssets();
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === 'navigate') return handleNavigation(event);
  if (url.pathname.startsWith('/assets/')) return event.respondWith(cacheFirst(event));
});

function handleNavigation(event) {
  const network = (async () => (await event.preloadResponse) || fetch(event.request))();
  event.waitUntil(network.then(storeShell, () => {}));
  event.respondWith((async () => {
    const timeout = new Promise((resolve) => setTimeout(resolve, NAV_TIMEOUT_MS, null));
    const first = await Promise.race([network.catch(() => null), timeout]);
    if (first && first.status < 500) return first;
    const cached = await caches.match(SHELL_KEY, { cacheName: SHELL_CACHE });
    return cached || first || network;
  })());
}

async function storeShell(response) {
  const copy = response.clone();
  const type = copy.headers.get('content-type') || '';
  if (copy.status !== 200 || copy.redirected || !type.startsWith('text/html')) return;
  await (await caches.open(SHELL_CACHE)).put(SHELL_KEY, copy);
  if (!pruned) { pruned = true; await pruneAssets(); }
}

async function cacheFirst(event) {
  const cache = await caches.open(ASSET_CACHE);
  const hit = await cache.match(event.request, { ignoreVary: true });
  if (hit) return hit;
  const response = await fetch(event.request);
  if (response.status === 200 && response.type === 'basic') {
    event.waitUntil(cache.put(event.request, response.clone()));
  }
  return response;
}

async function pruneAssets() {
  const cache = await caches.open(ASSET_CACHE);
  const now = Date.now();
  const entries = [];
  for (const request of await cache.keys()) {
    const response = await cache.match(request, { ignoreVary: true });
    entries.push({ request, at: Date.parse(response?.headers.get('date') ?? '') || 0 });
  }
  entries.sort((a, b) => b.at - a.at);
  await Promise.all(entries
    .filter((entry, i) => i >= ASSET_MAX_ENTRIES || now - entry.at > ASSET_MAX_AGE_MS)
    .map((entry) => cache.delete(entry.request, { ignoreVary: true })));
}
```

Notes on the sketch: `storeShell` clones synchronously, before the page consumes the body (its
`then` is registered before the `respondWith` race, so it runs first); the cache write rides
`waitUntil` and never delays the response; a 5xx navigation (Railway mid-deploy) falls back to
the cached shell when there is one; `Date` is stamped by Go's `net/http` on every Caddy response
and is readable on a same-origin (`basic`) response, which is what the prune keys on.

Plain JavaScript, not TypeScript: `public/` is not compiled, and a separate build step for one
file costs more than it saves. ESLint's `src` scope does not cover it; W1 adds a `// @ts-check` header and a
`/// <reference lib="webworker" />` so editors still type-check it.

### Kill switch

If the worker ever misbehaves in production, the fix is a deploy, not a deletion: replace
`public/sw.js` with

```js
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil((async () => {
  for (const name of await caches.keys()) await caches.delete(name);
  await self.registration.unregister();
})()));
```

and, in the same PR, make `registerServiceWorker` unregister instead of register (otherwise every
load re-installs the kill switch). Pages keep running; the next load is worker-free. Keep that
file served for months. Write this down in the W1 PR body and `docs/architecture.md`.

## Verification W1 should run

- CI (`frontend-build.yml`): after `npm run build`, assert `dist/sw.js` and
  `dist/manifest.webmanifest` exist (two `test -f` lines).
- After the deploy, read-only against production: `curl -sI` on `/sw.js` (200,
  `text/javascript`, `no-cache`), `/manifest.webmanifest` (200, `application/manifest+json`),
  `/apple-touch-icon.png` (200 `image/png`, or 404 if not shipped, never `text/html`),
  `/assets/<entry>.js` (immutable), `/assets/nope.js` (404 `no-store`), `/browse` (200
  `text/html`, `max-age=0`).
- The Caddyfile diff above was NOT executed in this research (running a Caddy binary locally was
  not permitted in this session); the post-deploy `curl` list is the check.
- On the iPhone: install, open, airplane mode, cold-start (the shell opens; data shows its
  error state), back online; deploy a trivial change, resume the app (toast), tap Reload (new
  build); open a lazy section after a deploy without reloading (one silent reload).

## The file list W1 writes

| File | Change |
| --- | --- |
| `frontend/public/sw.js` | new: the worker above |
| `frontend/public/manifest.webmanifest` | new: fields per #1763; relative URLs only |
| `frontend/public/icons/*`, `frontend/public/apple-touch-icon.png` | new: per #1763 |
| `frontend/src/lib/serviceWorker.ts` | new: `registerServiceWorker()` + hourly `registration.update()` on resume |
| `frontend/src/main.tsx` | call `registerServiceWorker()` |
| `frontend/src/lib/useBuildSkew.ts` | deferred mount probe when a worker controls the page; header comment updated ("current by construction" no longer holds under a worker) |
| `frontend/src/lib/buildSkew.ts` | header comment: the probe must stay a script fetch the worker passes through |
| `frontend/index.html` | `<link rel="manifest">`, the `apple-touch-icon` link and the meta tags from #1763; remove the two Google Fonts `preconnect`s and the stylesheet |
| `frontend/src/assets/fonts/*.woff2` (6 files, 303 KB) | new: the latin + latin-ext variable files |
| `frontend/src/styles/fonts.css` | new: the 14 `@font-face` blocks copied from Google's CSS (`font-display: swap`, same `unicode-range`s and weights), imported from `main.tsx`; `@theme` tokens untouched |
| `frontend/Caddyfile` | the diff above |
| `frontend/Dockerfile` | none |
| `.github/workflows/frontend-build.yml` | `test -f dist/sw.js && test -f dist/manifest.webmanifest` |
| `frontend/README.md` § Deploy | the worker, the new Caddy blocks, the kill switch |
| `docs/architecture.md` § Frontend | one bullet: the worker is version-agnostic; its allowlist; who owns which staleness; the kill switch (CLAUDE.md: a behaviour change updates the doc in the same PR) |

Fonts and the worker can be two slices of W1 (fonts first: it is visually a no-op and makes the
worker's "same origin only" rule true from day one).

## Open decisions for the map

- **Silent reload on resume** (standalone mode, stale build, hidden > 30 min): changes the
  "offered, never imposed" rule; operator's call. Default: not built; the toast stays.
- **Self-host the fonts in W1** (recommended) or keep Google Fonts with the one cross-origin
  worker rule.

## Sources

Repository (read on `main` `16bb77a4`): `frontend/src/lib/buildSkew.ts`, `lib/useBuildSkew.ts`,
`lib/lazyChunk.ts`, `lib/imageUrl.ts`, `lib/api.ts`, `lib/supabase.ts`, `lib/basemap.ts`,
`lib/workflowDocs.ts`, `src/main.tsx`, `components/Shell.tsx`, `frontend/Caddyfile`,
`frontend/Dockerfile`, `frontend/.dockerignore`, `frontend/vite.config.ts`, `frontend/index.html`,
`frontend/package.json` (+ lockfile: Vite 5.4.21), `frontend/railway.json`,
`frontend/scripts/bundle-budget.mjs`, `.github/workflows/frontend-build.yml`,
`api/routes/images.py`, `docs/architecture.md` § Frontend,
`docs/design/brand-domain-and-store/PROGRAM.md`.

Fetched directly 2026-10-12: the Google Fonts CSS from `index.html` (iPhone user agent) and its
six latin/latin-ext woff2 files (sizes and headers); Caddy 2.11.7 source
([`fileserver/staticfiles.go`](https://github.com/caddyserver/caddy/blob/v2.11.7/modules/caddyhttp/fileserver/staticfiles.go),
[`httpcaddyfile/httptype.go`](https://github.com/caddyserver/caddy/blob/v2.11.7/caddyconfig/httpcaddyfile/httptype.go));
the official [`caddy-docker` 2.11 alpine Dockerfile](https://github.com/caddyserver/caddy-docker/blob/master/2.11/alpine/Dockerfile);
Alpine's [mailcap APKBUILD](https://gitlab.alpinelinux.org/alpine/aports/-/blob/3.23-stable/main/mailcap/APKBUILD)
and the released [mailcap-2.1.54 `mime.types`](https://releases.pagure.org/mailcap/mailcap-2.1.54.tar.xz);
Go [`net/http/server.go`](https://github.com/golang/go/blob/release-branch.go1.25/src/net/http/server.go)
(automatic `Date` header, line 1490); Vite 5.4 [assets guide](https://github.com/vitejs/vite/blob/v5.4.10/docs/guide/assets.md).

Gathered by a research subagent through WebFetch (each page passed through a summarising model;
quotes are close to verbatim, re-check before quoting one as load-bearing):

- [R1] W3C Service Workers, https://w3c.github.io/ServiceWorker/ (Update algorithm: byte-for-byte
  check, JavaScript MIME type check, `updateViaCache` default `"imports"`, stale = last update
  check > 86400 s; a failed update removes the registration only "If newestWorker is null";
  `skipWaiting`). The last two were also checked directly against the spec text on 2026-10-12.
- [R2] web.dev, "The service worker lifecycle", https://web.dev/articles/service-worker-lifecycle
- [R3] web.dev, "Speed up service worker with navigation preloads", https://web.dev/blog/navigation-preload
- [R4] MDN, `FetchEvent.preloadResponse`, https://developer.mozilla.org/en-US/docs/Web/API/FetchEvent/preloadResponse
- [R5] WebKit, "New WebKit Features in Safari 15.4", https://webkit.org/blog/12445/new-webkit-features-in-safari-15-4/
- [R6] MDN browser-compat-data, https://github.com/mdn/browser-compat-data (`ServiceWorkerGlobalScope.skipWaiting`, `Clients.claim`)
- [R7] WHATWG Fetch, HTTP fetch, https://fetch.spec.whatwg.org/
- [R8] WebKit bug 190041, https://bugs.webkit.org/show_bug.cgi?id=190041
- [R9] MDN, `fetch` event, https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerGlobalScope/fetch_event
  ("implicit network requests to load pages and subresources"; that cross-origin requests reach
  the handler is the reading of this plus Workbox's cross-origin routing rules, not one sentence)
- [R10] npm registry, vite-plugin-pwa, https://registry.npmjs.org/vite-plugin-pwa/latest
- [R11] npm registry, workbox-build, https://registry.npmjs.org/workbox-build/latest
- [R12] bundlephobia, workbox-window 7.4.1, https://bundlephobia.com/package/workbox-window@7.4.1
- [R13] vite-plugin-pwa, "Service worker strategies and behaviors", https://vite-pwa-org.netlify.app/guide/service-worker-strategies-and-behaviors.html
- [R14] vite-plugin-pwa, "Prompt for update", https://vite-pwa-org.netlify.app/guide/prompt-for-update.html
- [R15] vite-plugin-pwa, "Automatic reload", https://vite-pwa-org.netlify.app/guide/auto-update.html
- [R16] vite-plugin-pwa, "Static assets handling", https://vite-pwa-org.netlify.app/guide/static-assets.html
- [R17] Workbox, workbox-build / workbox-precaching, https://developer.chrome.com/docs/workbox/modules/workbox-build
- [R18] Workbox, "Handling service worker updates", https://developer.chrome.com/docs/workbox/handling-service-worker-updates
- [R19] web.dev, "Update" (Learn PWA), https://web.dev/learn/pwa/update
- [R20] Apple Developer Forums thread 123606, https://developer.apple.com/forums/thread/123606 (a user report, not Apple documentation)
- [R21] vite-plugin-pwa, "Periodic service worker updates", https://vite-pwa-org.netlify.app/guide/periodic-sw-updates.html
- [R22] MDN, `ServiceWorkerRegistration.updateViaCache`, https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/updateViaCache
- [R23] WebKit bugs 180523, https://bugs.webkit.org/show_bug.cgi?id=180523, and 201737, https://bugs.webkit.org/show_bug.cgi?id=201737
- [R24] WebKit bug 199110, https://bugs.webkit.org/show_bug.cgi?id=199110
- [R25] Caddy, directive order, https://caddyserver.com/docs/caddyfile/directives#directive-order
- [R26] Caddy, sorting algorithm, https://caddyserver.com/docs/caddyfile/directives#sorting-algorithm
  (checked directly in the docs source, `caddyserver/website` `src/docs/markdown/caddyfile/directives.md`)
- [R27] Caddy 2.11.7, `normalizeDirectiveName` in `caddyconfig/httpcaddyfile/httptype.go` (read directly)
- [R28] Caddy, `handle`, https://caddyserver.com/docs/caddyfile/directives/handle, and `file_server`, https://caddyserver.com/docs/caddyfile/directives/file_server
- [R29] MDN, `ServiceWorkerContainer.register()`, https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/register, and `Service-Worker-Allowed`, https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Service-Worker-Allowed
- [R30] WHATWG MIME Sniffing, JavaScript MIME type, https://mimesniff.spec.whatwg.org/#javascript-mime-type
- [R31] WebKit, "Full Third-Party Cookie Blocking and More" (2020), https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/
- [R32] WebKit, "Updates to Storage Policy" (2023), https://webkit.org/blog/14403/updates-to-storage-policy/
- [R33] Workbox, workbox-recipes (Google Fonts cache), https://developer.chrome.com/docs/workbox/modules/workbox-recipes
- [R34] web.dev, "The offline cookbook", https://web.dev/articles/offline-cookbook
- [R35] Workbox, `NetworkFirst` (`networkTimeoutSeconds`) and the page-cache recipe, https://developer.chrome.com/docs/workbox/modules/workbox-strategies
