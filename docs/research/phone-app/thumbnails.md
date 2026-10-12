# A phone-size photo: how the image route gets a thumbnail

Research for [#1765](https://github.com/waiff/sreality/issues/1765) (map [#1759](https://github.com/waiff/sreality/issues/1759), operator Q15).
Read 2026-10-12 against `origin/main` 16bb77a4. This is a throwaway research branch and is never merged.

## Recommendation

**Option B.** Make two WebP renditions, **480 px and 1080 px wide**, at the point where the master is written to R2. The
existing route `GET /images/{key}?v=2&w=480|1080` redirects to a rendition when one exists and **falls back to the master**
when it does not. The browser picks the size from `srcset` + `sizes`.

- No `images` column is needed and no migration.
- No new dependency is needed.
- No backfill is needed for W1: photos stored before the cut-over keep today's behaviour and are served the master.

The deciding fact is that **the phone feed is newest-first**. The photos it shows are the ones nobody has requested yet. A
renditions-on-first-request design (A) would serve those large or slow on exactly the screen it was built for. Only a producer
that runs at ingest serves them small the first time they are seen.

## What exists today (code facts)

**The route** (`api/routes/images.py`, 83 lines):
- It takes no database read and makes no R2 call. It validates the key against `^-?\d+/\d{4}\.jpg$`. That guard keeps the
  route from ever presigning the private `custom-attachments/` keys in the same bucket.
- It then presigns a GET signed as of the start of the UTC day (`IMAGE_PRESIGN_ANCHOR_SECONDS`, default 86400) and answers
  `302` with `Cache-Control: public, max-age=3600`.
- `?v=2` is ignored on the server. It is the SPA's cache-bust token (`IMG_CACHE_BUST` in `frontend/src/lib/imageUrl.ts`).

**The R2 client** (`scraper/image_storage.py`, `R2Client`):
- It is boto3 against `https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`. The env vars are `R2_ACCOUNT_ID`,
  `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_BUCKET_NAME`, and both the scraper and the API need them (toolkit-api
  skill, § Image storage).
- `upload_bytes` stamps every object `Cache-Control: public, max-age=2592000` (30 days).
- `object_size(key)` already does a `HeadObject` and returns `None` only when the key is missing. Rendition lookup can use it
  as is.

**Who writes masters.** Two writers:
1. `scraper/main.py::_fetch_one_image`. This one runs in the GitHub Actions image workflows (`images.yml`,
   `images_fresh.yml`, …) and in the realtime worker's images-first lane (`realtime_worker.py` → `_run_image_downloads(...,
   active_only=True)`, 8 workers).
2. `scripts/remaster_sreality_images.py:412`. It **overwrites** an existing `storage_path` with new bytes.

The download step already has the bytes in hand and already decodes them with Pillow, for `_phash_or_none` and
`image_dimensions`.

**Keys.** A key is `{listing_id}/{seq:04d}.jpg`, and older ones are keyed by `sreality_id`. The upsert is position-keyed, so
one key can hold different bytes over time. A rendition must therefore be rewritten every time its master is.

**Pillow is already a core dependency.** `pyproject.toml` has `"Pillow>=10"`, pre-approved for dHash. Code that uses it:
`scraper/image_phash.py`, `scraper/image_storage.py::image_dimensions`, and `toolkit/vision_images.py::downscale_jpeg`, which
already does `thumbnail()` + JPEG re-encode for the vision tiers. So rule 7 needs nothing new. The local check ran Pillow
12.3.0 with WebP and AVIF compiled in.

**How photos render.** `imageSrc()` returns one URL per image, the master, and there is no `srcset` anywhere in `frontend/src`.
That one size goes everywhere:
- the Browse card carousel (`ImageCarousel`, one `<img>` at a time, `loading=lazy`, frame `aspect-[5/4]`, card min width
  11.5rem small or 23rem large in `ListingCards.tsx`);
- the property page's photo grid (`listing-detail/Gallery.tsx`, 4:3 tiles, all lazy, `decoding=async`);
- the pipeline board (`pipeline/BoardCard.tsx` `CardThumb`, **48 px or 96 px squares** or a full-width 16:10, per
  `pipelineCardSize.ts`).

**The extension shows none of our photos.** `chrome-extension/src/index_stage.ts` draws the stage *over the portal's own card
photo*, which it finds by geometry (`findPhoto`). A search of `chrome-extension/src` finds no `storage_path`, no `/images/`
and no `<img>` of ours. **The extension gains nothing from thumbnails.**

## Live facts (one sampled read, 2026-10-12)

A full aggregate over `images` timed out on the MCP, so the read used `TABLESAMPLE` instead: 0.5 % of `images` and 1 % of
`listings`, with each sampled listing's first photo found through `images_listing_id_sequence_key`.

| Fact | Value |
| --- | --- |
| `images` rows (planner estimate) | **≈ 14.36 M** |
| share with `storage_path` set (sample n = 75,881) | **98.8 %** → ≈ 14.2 M masters in R2 |
| listings whose first photo is stored (n = 9,549) | **98.8 %**; active listings (n = 3,985) **99.7 %** |
| stored masters with dimensions: average long edge | **1,504 px**; 2.9 % over 1,800 px; 0.2 % at 600 px or less |
| stored rows with no dimensions (downloaded before migration 496) | 66 % |
| growth | 11.30 M stored on 2026-09-05 (`docs/design/new-dedup/ENCODER-DECISION.md`) → ≈ 14.2 M on 2026-10-12, so **≈ 75–80 k new photos a day** |

Master byte sizes are not recorded anywhere I could read: there is no column, and no bucket total in the docs.

## Benchmark (one real master, local CPU)

The sample photo was `2836292428/0001.jpg` (1536×1024, 88 KB), fetched through the public route. I also tested a synthetic
1800-wide upscale of it as a worst case. The pipeline was Pillow 12.3.0 `draft()` + LANCZOS resize with `reducing_gap=2.0`,
then encode. Timings are single-threaded, averaged over 20 runs.

| Rendition | JPEG q75 | WebP q75 (method 4) |
| --- | --- | --- |
| 480 w (480×320) | 21–28 ms, 12.5 KB | 30–43 ms, **5.9 KB** |
| 1080 w (1080×720) | 29–70 ms, 46.5 KB | 64–84 ms, **19.6 KB** |

- In pixels, the 480 version is about 10 % of the master and the 1080 version about 50 %.
- WebP is less than half the bytes of JPEG at the same quality setting.
- This photo compresses unusually well, so expect typical renditions at 2–3× these bytes: **about 25–75 KB per photo for
  both renditions together.**
- Both renditions together cost **about 0.1–0.13 CPU-seconds per photo.**

## Vendor facts (primary sources)

**Cloudflare R2** ([pricing](https://developers.cloudflare.com/r2/pricing/), updated 2026-10-01):
- Standard storage is $0.015 per GB-month.
- Class A operations (PutObject …) cost **$4.50 per million**. Class B operations (GetObject, HeadObject) cost **$0.36 per
  million**.
- Egress is free.
- The free tier is 10 GB-month, 1 M Class A and 10 M Class B per month. Our masters (about 2.3 M PUTs a month) already use up
  the Class A allowance.

**Presigned URLs and custom domains:**
- A presigned GET is a GetObject, so Class B. That is my inference; the pricing page does not say "presigned".
- Presigned URLs "cannot be used with custom domains"
  ([presigned-urls](https://developers.cloudflare.com/r2/api/s3/presigned-urls/)).
- A custom domain must be a zone in the same account
  ([public-buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/)).
- `r2.dev` is rate-limited and uncached.

**Cloudflare Images transformations** ([pricing](https://developers.cloudflare.com/images/pricing/), 2026-07-08):
- The first 5,000 unique transformations a month are free; after that it is **$0.50 per 1,000 unique transformations**.
- "Unique" means one image plus one parameter set, counted once per **calendar month**. A photo viewed every month is billed
  every month.
- With the URL form `/cdn-cgi/image/...`, transformations are switched on **per zone**, and the source must be on that zone
  or be publicly reachable ([transform-images](https://developers.cloudflare.com/images/transform-images/),
  [sources](https://developers.cloudflare.com/images/transform-images/sources/)).
- A Worker's `cf.image` works on `*.workers.dev`.
- The Images binding takes R2 bytes directly
  ([bindings](https://developers.cloudflare.com/images/transform-images/bindings/)).
- Not settled: one Cloudflare tutorial says the binding needs an "Images Paid" subscription, and the newer binding page says
  only hosted images need it.
- No page says whether a presigned URL can be a transform source.

**Pillow** ([Image reference](https://pillow.readthedocs.io/en/stable/reference/Image.html), 12.3.0):
- `draft()` is "implemented only for JPEG and MPO images". It decodes at 1/2, 1/4 or 1/8 scale.
- `thumbnail(..., reducing_gap=2.0)` uses `draft()` for JPEG on its own.
- WebP read and write is in the Linux wheels, which "include support for all optional libraries except libimagequant". Save
  options are `quality` (default 80) and `method` (0–6, default 4).
- AVIF has been in the wheels since 11.3.0, but it encodes slower and gains little here.

**Railway** ([pricing plans](https://docs.railway.com/reference/pricing/plans)):
- CPU is $20 per vCPU-month ($0.000463 per vCPU-minute) and RAM $10 per GB-month. The Hobby plan is $5 a month including $5
  of usage.
- 8 vCPU / 8 GB per Hobby replica is arithmetic (48 GB across 6 replicas), not a stated figure. It matches the worker's
  7,629 MiB cgroup.
- The API is **one uvicorn process** (`Dockerfile` CMD, no `--workers`), so every resize done in the API shares that
  process's thread pool and CPU with every other request.

**Cloudflare plan.** The Cloudflare account exists for R2, but **no domain is on Cloudflare today**. The brand-domain program
(`docs/design/brand-domain-and-store/PROGRAM.md` W0–W1) plans one, with `api` permanently DNS-only. I could not confirm the
account's plan tier.

**Browser** ([MDN `<img>`](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/img)):
- With `srcset` `w` descriptors + `sizes`, the browser picks a candidate "at its discretion" for the slot width × device
  pixel ratio.
- `loading=lazy` and `decoding=async` defer the work but do not shrink it.
- A `302` is not heuristically cacheable ([MDN Cacheable](https://developer.mozilla.org/en-US/docs/Glossary/Cacheable)). It
  is cached only through an explicit `max-age`, which the route already sends.
- An iPhone draws 3 device pixels per CSS pixel. So a full-width card on a 390–430 pt screen wants about 1,100–1,300 px, and
  a 160 pt side photo wants about 480 px.

## The four options

| | A: resize on first request (API) | **B: resize at ingest + route fallback** | C: Cloudflare transformations | D: `<img>` tricks only |
| --- | --- | --- | --- | --- |
| How | `?w=` miss → API GETs the master, resizes, PUTs `thumb/…`, then redirects | download step writes master + 2 renditions; `?w=` HEADs the rendition and redirects to it or to the master | Worker (+ Images binding) or a zone's `/cdn-cgi/image/` in front of R2 | `sizes`, `loading`, `decoding`, `fetchpriority` on the master |
| Feed (newest-first) on first view | **+≈250–600 ms per photo** (GET + 30–85 ms resize + PUT, estimated); nearly every feed photo is a first view | small at once; + one R2 HEAD inside the 302 | edge transform on first view (unmeasured) | full master |
| Cost per 1,000 photos | ≈ $0.006 per 1,000 *viewed* (GET + PUT + HEAD); nothing for unviewed | ≈ **$0.01** per 1,000 *ingested* (2,000 PUTs = $0.009, CPU ≈ free on Actions) + 25–75 MB stored (≈ $0.001/month) | **$1.00 per 1,000 viewed per month** (2 widths × $0.50), re-billed monthly; 5,000 free | $0 |
| At our volume | ≈ $0 | ≈ 75 k photos/day → ≈ 4.5 M PUTs/month ≈ **$20/month**; storage grows ≈ 2–5 GB/day (≈ +$1–2.5/month each month) | operator-scale ≈ 30–60 k unique transformations/month ≈ $12–28/month | $0 |
| Staleness when the master changes | rendition outlives a re-mastered or re-sequenced master unless invalidated | rewritten in the same step as the master | none (transform of current bytes) | none |
| New runtime / prerequisites | none; API CPU contention (one uvicorn process) | none | a Worker (JS, a 4th deploy surface) or a domain zone that does not exist yet; paid-plan question open | none |
| Bytes saved on the phone | yes, after the first view | **yes, from the first view** | yes | **no**: still ≈ 1.5 MP and a ≈ 6 MB decoded bitmap per photo |

**A** is cheap and needs no download-phase change, but it fails the screen it exists for. A variant that answers the master
on a miss and renders in the background has no first-view penalty, but then the feed sees almost no thumbnails, since feed
photos are mostly seen once.

**C** is the most capable option. It is priced per *viewed* rendition per month, needs either a Worker or the brand domain,
and adds a runtime the project does not have. Revisit it after the brand-domain move if the photo set ever needs many sizes.

**D** is a complement, not an answer. `Gallery.tsx` already uses `lazy` + `async`.

## Details of B

**Widths: two, named by pixel need rather than by surface.**
- **480 w** covers a slot up to about 160 pt at 3× or 240 pt at 2×. That is list rows, pipeline thumbnails (48/96 px today,
  served from a 1.5 MP master), the property page's grid tiles and small Browse cards on a 1× screen.
- **1080 w** covers a full-width phone card and the phone's photo swiper and lightbox, plus large Browse cards at 1.5–2×.
- The **master** (≤ 1,800, average 1,504) stays for the desktop lightbox.
- Never upscale. A master narrower than *w* is written at its own width, so "both renditions exist after a successful
  ingest" holds without exceptions.

**Format and encoding:**
- **WebP q75, method 4.** Safari has supported WebP since iOS 14.
- Store with `ContentType: image/webp` under `thumb/w480/{id}/{seq}.webp` and `thumb/w1080/…`. The key is derived from the
  validated master key and is never accepted from the client.
- Apply `ImageOps.exif_transpose` before resizing. Browsers honour EXIF orientation on the master, and a re-encode that drops
  it would rotate non-sreality photos wrongly.
- Keep `icc_profile` so colours match.

**The producer** is one helper in `scraper/image_storage.py` (`store_image(r2, key, data, content_type)`). It writes the
master, then the renditions, and is called by **both** master writers: `_fetch_one_image` and
`scripts/remaster_sreality_images.py`. Renditions are **best-effort**. A failed rendition is counted (`thumbs_failed` on the
`IMAGES done` line) but never fails the master, because the route falls back. The extra CPU is about 0.1–0.13 s per photo,
spread over the Actions image shards and the worker's 8-thread images lane, which comes to roughly 2–3 CPU-hours a day across
both.

**URL shape:** `?w=` on the existing route, e.g. `/images/{key}?v=2&w=480`. That keeps one route, one key guard and one
cache-bust token.
- Accept `w` only from the allowlist `{480, 1080}`; any other value → 404, so nobody can mint arbitrary sizes.
- Rendition present (`object_size(thumb_key)` is not `None`) → 302 to its presigned URL with `public, max-age=3600`, the same
  as today.
- Rendition missing → 302 to the **master** with `public, max-age=300`. A short lifetime lets a later backfill upgrade the
  image within minutes.
- An optional in-process memo of renditions known to exist saves the HEAD on repeat mints. Renditions only appear and never
  vanish, so a positive-only memo cannot go stale.
- Objects carry the same `public, max-age=2592000` as masters. The day-anchored signature means the browser re-fetches bytes
  at most once a day.
- R2's missing CORS headers do not matter: plain `<img>` loads need none.

**Front end:** `imageSrcSet(img)` in `imageUrl.ts` returns `"…&w=480 480w, …&w=1080 1080w, … 1800w"`, or nothing when there
is no `storage_path`, in which case the CDN fallback is unchanged. Each surface states only its `sizes`.
- A `w` descriptor that does not match a narrow master is harmless here: every one of our `<img>`s has a CSS box
  (`w-full h-full object-cover`), so the intrinsic size never drives layout.

**What the desktop gains:**
- The pipeline board's 48/96 px thumbnails and the property page's grid tiles drop from the master to the 480 rendition.
  That is the biggest win: 10 % of the pixels, about 1/10–1/15 of the bytes.
- Browse cards get whatever `sizes` × device pixel ratio asks for, with no visual change.

**The extension gains nothing.** It draws over the portal's own photo.

**Backfill: not in W1.** Older photos are served the master, which is today's experience.
- A later, scoped job — a GitHub Actions script with a keyset cursor, not a worker lane, because the worker's 8 GB cgroup is
  already the autodedup bottleneck — can fill active ads newest-first, then pipeline and collection properties.
- Cost of the whole corpus: 14.2 M × 2 PUTs ≈ **$128** once, + 14.2 M GETs ≈ $5, + about 350–1,000 GB stored ≈ **$5–15 a
  month**. Active ads only is very roughly 40 % of that; 40 % is the active share of sampled listings, assumed to hold for
  photos too.

## W1 slice (one PR, no migration, no new dependency)

1. `scraper/image_storage.py`:
   - `THUMB_WIDTHS = (480, 1080)`
   - `thumb_key(key, w)`
   - `make_thumbnail(data, w) -> bytes` (`draft`, `exif_transpose`, LANCZOS, WebP q75, never upscale)
   - `store_image(...)`, which writes master + renditions, best-effort, and returns the count written.
2. `scraper/main.py::_fetch_one_image`: call `store_image` instead of `r2.upload_bytes`; add a `thumbs_failed` counter to
   `IMAGES done`.
3. `scripts/remaster_sreality_images.py`: switch to the same helper. Without that, a re-master leaves stale renditions.
4. `api/routes/images.py`: an optional `w` query parameter checked against the allowlist, a HEAD through `object_size`, and
   a 302 to the rendition or to the master (with the shorter max-age).
5. `frontend/src/lib/imageUrl.ts`: `imageSrcSet()`. The phone feed card uses `srcset` + `sizes`. Two pure desktop wins can
   ride along if the map agrees: `BoardCard` `CardThumb` (`sizes="96px"`) and the `Gallery.tsx` tiles.
6. Tests:
   - `tests/api/test_images.py`: `w` allowlist, hit and miss, and the key guard still refusing `custom-attachments/`.
   - A scraper test for `make_thumbnail`: never upscales, EXIF is transposed, output is WebP.
   - `frontend/src/lib/imageUrl.test.ts`.
7. Docs in the same PR:
   - the toolkit-api skill's R2 section (`?w=`, rendition keys);
   - `docs/architecture.md` § rule 6 (masters plus two renditions);
   - the scraper-ops `IMAGES done` line shape.
   - The rule-6 sentence in CLAUDE.md belongs to the operator.

Done when the operator's phone, on a cellular connection, opens the feed and Safari's network panel (or a `?w=` probe with
`curl -sI`) shows `thumb/w1080/…webp` or `thumb/w480/…webp` targets for photos ingested after the deploy, and a master target
for older ones.
