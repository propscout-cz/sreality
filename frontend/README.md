# sreality · database browser

Web UI over the Supabase database produced by the scraper: it reads the
public views directly and routes any write through the FastAPI service.
Vite + React + TypeScript + Tailwind v4.  Static SPA — no SSR, no backend.

The estimation flow (`/estimate` → `/estimation/:id` → `/estimations`) is
end-to-end: paste a URL, edit the spec, submit, and the run is persisted by
the FastAPI service and rendered with a full trace timeline + comparables
table.  The Timeline component dispatches on `step.kind` so it renders
today's deterministic 4-step traces and the future U4 agent's longer
traces without rework.

> Audience for this README: anyone setting the project up locally or
> debugging the deploy.  The operator now works locally (VS Code on WSL2) and
> can run these commands; the live site is still built by Railway on push to
> `main`.

## Stack

| Concern        | Choice                                              |
| -------------- | --------------------------------------------------- |
| Build          | Vite 5                                              |
| Framework      | React 18 (StrictMode)                               |
| Routing        | `react-router-dom` v6                               |
| Server state   | `@tanstack/react-query` v5                          |
| Styling        | Tailwind CSS v4 (CSS-only config via `@theme`)      |
| DB client      | `@supabase/supabase-js` reading `*_public` views    |
| Map            | `maplibre-gl` + OpenFreeMap (Positron tiles)        |
| Charts         | `recharts` (lazy-loaded; SVG sparklines elsewhere)  |

## Local development

```sh
cd frontend
cp .env.example .env
# fill in VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY
npm install
npm run dev      # http://localhost:5173
npm run build    # type-check + production bundle to dist/
```

Node 20+ required.

### Where to find each env var

- `VITE_SUPABASE_URL` — Supabase dashboard → Project Settings → API → Project URL.
- `VITE_SUPABASE_ANON_KEY` — same page → "anon public" / "publishable" key.
  Safe to ship to the browser; reads are fenced by the `*_public` views in
  migration 008.
- Listing photos need **no** R2 env var. They are served through the API's
  `GET /images/{storage_path}` redirect (which mints a short-lived presigned
  R2 URL), so the durable R2 copy reaches the browser without baking a bucket
  base into the build or exposing the bucket publicly. The single
  `imageSrc()` helper (`src/lib/imageUrl.ts`) builds `${VITE_API_BASE_URL}/images/${storage_path}`,
  falling back to the original CDN URL only for a just-scraped listing whose
  bytes the async image job hasn't downloaded yet. On that fallback a sreality
  (`sdn.cz`) URL is normalised onto their `res,1800,1800,1|shr,,20|jpg,80`
  template — the same whole-frame, ≤1800px template the scraper downloads
  through (their CDN is an exact-template allowlist; a bare URL 401s and a
  stored legacy `res,749,…` chain would serve a 4:3 crop). The SPA copy and the
  scraper's are kept in step by `tests/test_image_transform_parity.py` (template
  string, host, kept-op set) plus the shared probe vectors in
  `tests/fixtures/sreality_transform_probes.json`, which `imageUrl.test.ts` runs
  through this module and the Python suite runs through `with_transform` — so a
  drift in either copy's LOGIC, not just its constants, reds a suite.
- The R2 bucket sends **no CORS header**: an `<img>` displays a photo, but
  `fetch()` cannot read its bytes. So the property page's "Stáhnout" button
  asks the API for `GET /properties/{id}/download.zip` (JWT), which reads the
  stored photos from R2 and zips them server-side with a PDF of the ad.

## Project layout

```
src/
  main.tsx           react root + react-query provider + router
  App.tsx            mounts the routed shell
  routes.tsx         route table
  lib/
    supabase.ts      single shared client
    pgRead.ts        THE PostgREST read: every supabase-js read is awaited
                     here (deadline, abort, ApiError, one retry rule)
    types.ts         shapes mirroring listings_public et al.
    queries.ts       supabase query helpers (grows with parts B–E)
    listingUrl.ts    propertyPath(propertyId) — the ONE detail-page builder
                     (/property/:id); listingPath / advertPath build the advert
                     aliases that land there. Don't hand-roll the path.
  components/
    Shell.tsx        top bar + nav + footer
  pages/
    Browse.tsx       part B
    PropertyDetail.tsx part C (the one property page)
    Region.tsx       part D
    Health.tsx       part E
  styles/
    globals.css      design tokens (@theme), typography, base reset
```

## Design system in one place

All visual tokens live in `src/styles/globals.css` under a single `@theme`
block (light) plus a dark-mode mirror.  Colour names are domain words —
`--color-paper`, `--color-ink`, `--color-copper`, `--color-brick`, etc. —
not abstract greys.  Depth strategy is **borders only** (no shadows),
with one carve-out: map-anchored popovers may use a small shadow because
borders fail the squint test against arbitrary tile imagery.

Numbers always use tabular figures (`font-variant-numeric: tabular-nums`)
and Czech locale (`Intl.NumberFormat('cs-CZ')`) so a column of prices
aligns to the rightmost digit.

## Deploy

Railway, as a second service in the same project as the API.  The
service builds from this `frontend/` directory using the local
`Dockerfile` (Node build stage → Caddy serve stage) and exposes a
public domain that Railway terminates TLS at.  See the root `README.md`
for the click-by-click setup.

`Caddyfile` handles SPA routing (`try_files {path} /index.html`),
gzip + zstd compression, immutable caching for hashed `assets/*`
bundles, the installable app's own files (below), and a `/healthz`
endpoint that Railway uses for liveness.

CI lives at `.github/workflows/frontend-build.yml` and runs
`npm install && npm run build` on every pull request (and on a push to `main`) to catch broken builds
before Railway sees them; it also fails when `dist/sw.js` or
`dist/manifest.webmanifest` is missing.

### The installable app: manifest, icons, service worker

The SPA installs to a phone's home screen as the Phone app (map #1759).
Three things in `public/` make that work; Vite copies them into `dist/`
as-is, and Caddy serves each from its own `handle` block, real file or
real 404, never the SPA fallback:

- **`manifest.webmanifest`** (`no-cache`, `application/manifest+json`):
  root-relative URLs only (`id` `/`, `scope` `/`, `start_url` `/feed`),
  so the brand-domain move needs nothing but a reinstall. Its `name` and
  `short_name` are literal copies of `src/lib/brand.ts` (JSON can't import
  it); `src/lib/manifest.test.ts` fails when they drift. iOS reads the
  manifest once, at install: a change reaches an installed app only when
  it is reinstalled.
- **Icons** (`icons/*`, `apple-touch-icon.png`, `favicon.ico`;
  `max-age=86400`): drawn by `python3 scripts/make-icons.py` (Pillow, the
  self-hosted Fraunces); rerun it and commit the PNGs to change them.
- **`sw.js`** (`no-cache`, `text/javascript`): a hand-written service
  worker, registered by `src/lib/serviceWorker.ts` in production builds
  only. It caches by URL, never by build: page navigations go to the
  network first (the last good `index.html` only after 3 s, offline or on
  a 5xx) and hashed `/assets/*` files are served from its cache; it
  touches nothing else (API, images, Supabase, tiles). It never reloads a
  page: `lib/buildSkew` stays the one "newer version" signal and
  `lib/lazyChunk` the one recovery from a missing chunk. It also handles
  push (notifications push W4): `push` always shows exactly one
  notification and sets the icon badge (below iOS 18.4, three pushes that
  show nothing remove every subscription), and `notificationclick` opens
  the tapped page in the app's window. `src/lib/sw.test.ts` runs the file
  in a fake worker scope; `npm run typecheck` checks it against the
  worker's globals (`tsconfig.sw.json`). Design and reasons: #1764, the
  header of `public/sw.js`, `docs/design/notifications-push/PROGRAM.md`.

**Retiring the worker.** Never delete `public/sw.js`: an installed worker
whose script 404s simply stays installed. Replace the file's contents
with the self-unregistering version in its header comment (clears its
caches, unregisters itself), make `registerServiceWorker()` unregister
instead of register in the same PR, deploy, and keep that file served
for months. Unregistering also ends push on iOS below 18.4.

The three typefaces are self-hosted (`src/assets/fonts/`, SIL OFL 1.1,
licence files beside them; `src/styles/fonts.css`), so the page makes no
cross-origin request before first paint and the worker needs no
cross-origin rule.

### Phone shell

Under 768 px wide, `Shell.tsx` renders the routes inside the phone frame
(`src/components/phone/PhoneShell.tsx`) instead of the desktop top bar:
an `h-14` header (never taller — the Browse and Pipeline panes pin to
3.5 rem) with a bell (the unread count, opening `/notifications`), a
five-tab bar (Nové `/feed`, Pipeline, Sbírky, Makléři, Odkaz
`/link`) and an account sheet (the theme, this iPhone's push switch
"Oznámení na tomto iPhonu" — `src/components/phone/PushSwitch.tsx` over
`src/lib/usePush.ts`, its subscription bound to the signed-in user by
`src/lib/auth.tsx` and `src/lib/push.ts` — the desktop view, sign-out).
A tapped push lands on `/n/:dispatchId` (`src/pages/NotificationOpen.tsx`),
which marks it read and replaces itself with what it is about. One unread observer
(`src/lib/unreadCount.tsx`, mounted by `Shell`) feeds the bell, the
desktop nav badge, the Notifications page and the installed app's icon
badge. `usePhoneShell()` (`src/lib/phoneShell.ts`) decides; the
account sheet's "Desktopová verze" sets a per-device override
(`localStorage` `sreality.phone.desktopView`) and the desktop account menu's
"Mobilní verze" clears it. Pages are not forked: a route without a phone
design renders its desktop page inside the frame. The feed (`/feed`,
`src/pages/Feed.tsx`) reuses Browse's read layer and keeps its scope in the
URL as Browse does; its cards are `src/components/phone/FeedCard.tsx`, and
its Filtry sheet (`src/components/phone/FilterSheet.tsx`) draws Browse's
filter set from the sidebar's own layout table (`src/lib/filterLayout.ts`). The property page draws
`src/components/phone/PhoneProperty.tsx` from `PropertyDetail`'s own reads;
Odkaz (`src/pages/Link.tsx`) takes a pasted portal link to
`/listing/:source/:nativeId`. To see the frame on a desktop browser, narrow
the window below 768 px or use the device toolbar.

### Build-time vs runtime env vars

Vite inlines `import.meta.env.VITE_*` into the JS bundle at **build
time** as string constants — the deployed bundle never reads env at
runtime.  Practical consequence: changing any `VITE_*` value (e.g.
`VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, or `VITE_R2_PUBLIC_BASE`)
requires a redeploy (push or click "Redeploy" in Railway), not just a
variable update. (Listing-image serving deliberately has **no** build-time
R2 env — it routes through the API at runtime — so it can never silently
regress on a missing variable the way a baked-in bucket base could.)
