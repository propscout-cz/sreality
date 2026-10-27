# Secrets inventory: every credential, where it lives, who reads it

One row per secret: where a copy is stored, which code reads it, how to rotate it, and how to prove the
new value works. Each rotation's date and check go in `docs/design/agent-platform/MVP.md` (§ 3, the M0 credential
rotation log; agent platform M0, #2061). Update this file in the same PR as any change
to where a secret lives or who reads it.

The Railway services are three separate projects, one service each: **API** (`api/`, uvicorn),
**worker** (`scraper/realtime_worker.py`, same image) and **SPA** (`frontend/`, Caddy; it holds only the
public `VITE_*` build values). "GH" is a GitHub Actions repository secret. Railway's variable names per
service come from the 2026-10-25 audit (API 20, worker 14, SPA 3); the operator confirms each on the
service's Variables page before editing it.
Railway variable NAMES live in `.railway/railway.ts` (each `preserve()`; values stay in Railway): add a name there in
the same PR as the secret, remove it there to delete one (an operator apply, `scripts/railway_iac_wizard.sh`).
`EXTRAS` (API and worker: `api`) is the root Dockerfile's build argument.

## Rotated in M0 (ruling Q13, MVP.md § 4)

| Secret | GH | Railway API | Railway worker | Elsewhere | Read by | Rotate at | Verified by |
|---|---|---|---|---|---|---|---|
| `SUPABASE_DB_URL` (the postgres password) | yes, 62 workflow refs: the **shared** pooler's transaction string (runners reach Supabase over IPv4 only) | yes, the kind it holds today | yes, likewise | RunPod pods get it from a workflow's env | `scraper/db.py` `connect`, `api/dependencies.py` `get_db_conn`, scripts | Supabase → Database settings → Reset database password (one password, so no overlap: stage Railway first, set GitHub just before the reset) | psycopg on the exact GitHub string; signed-in `GET /estimations` |
| `SUPABASE_DB_SESSION_URL` (same password, session pooler 5432) | yes, 7 refs | if present | yes | | `scraper/db.py` `connect_session` (falls back to `SUPABASE_DB_URL`) | with the password above | psycopg; `apply_migration.yml` dry run (its connectivity step prefers this one) |
| `TENANT_POOL_DB_URL` (`tenant_pool.<ref>` role on the shared pooler, migration 293; an impersonation credential, audit F7) | no | yes | no | | `api/tenant_pool.py` | `ALTER ROLE tenant_pool PASSWORD '<SCRAM verifier>'` (compute the verifier locally, so no plaintext password reaches a log) | psycopg as `tenant_pool`; signed-in `GET /pipeline/stages` |
| `SUPABASE_SERVICE_ROLE_KEY` (a `sb_secret_…` key, not a JWT) | yes | no | no | | nothing (kept for forward compatibility) | Supabase → Settings → API Keys → new secret key, then delete the old one | `GET /auth/v1/admin/users?per_page=1` → 200 |
| `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY` | yes, 27 refs | yes (serves `/images/{key}`, the zip) | yes (images lane) | RunPod pods via workflow env | `scraper/image_storage.py` (the API and the worker both import it), `autodedup/lane.py`, `location_data/payloads.py`, scripts | Cloudflare → R2 → Manage API tokens → create (Object Read & Write), then delete the old token | boto3 `head_bucket`; the API's presigned photo URL carries the new key id (`X-Amz-Credential`, public); the worker's variable by eye |
| `MAPY_CZ_API_KEY` | yes (`seed_curated_cities.yml`) | yes (`/maps/*`, static map) | yes (live 2026-10-27) | | `scraper/geocoding.py` | Mapy developer portal → the project → new key, then delete the old one | `GET api.mapy.com/v1/geocode?query=Praha` → 200; the API's variable by eye (`/maps/suggest` falls back to MAPY2, so it passes on either) |
| `MAPY2_CZ_API_KEY` (backup) | yes (`seed_curated_cities.yml`) | yes | yes (live 2026-10-27) | | `scraper/geocoding.with_mapy_key_failover` | as above | as above |
| `VALUO_API_TOKEN`, `VALUO_SANDBOX_API_TOKEN` | no | yes | no | never local | `api/valuo_client.py` | from Valuo (their portal or account contact); ask them to revoke the old ones | `POST /v1/advert/detail` with no ids (prices nothing): 401/403 refused, 000/5xx inconclusive; the API's variables by eye |
| `GH_DISPATCH_TOKEN` (fine-grained PAT, Actions read+write) | no | yes | no | | `api/price_stats.py` | github.com → Settings → Fine-grained tokens → resource owner **propscout-cz**, only `sreality`, Actions read+write (1 year; recreated 2026-10-27, #2008); approve it under the org's token requests if pending | `PUT …/workflows/scrape_price_stats.yml/enable` on the active workflow → 204 (a no-op write; the repo is public, so a read proves nothing); the API's variable by eye |
| `STRIPE_WEBHOOK_SECRET` | no | **absent** (audit 2026-10-25: the billing webhook fails closed) | no | | `api/routes/billing.py` | Stripe → Developers → Webhooks → endpoint → Roll secret, only if stored | Stripe's "Send test event" → 2xx |
| `STRIPE_SECRET_KEY` | no | absent | no | | nothing | Stripe → Developers → API keys → Roll, only if it was ever stored | n/a |
| `SMOKE_TEST_ADMIN_PASSWORD`, `SMOKE_TEST_NONADMIN_PASSWORD` | no | no | no | local `.env` only | the production smoke checks | sign in, then set a new password (`PUT /auth/v1/user`) | sign-in with the new password |

## Deleted in M0 (no reader)

`RUNPOD_API_KEY` has no reader on Railway: pods are rented only inside workflows (the judge lane runs in `autodedup.yml`;
the worker's autodedup lane calls no provider). Both Railway copies are deleted.


| Secret | Where | Why it is dead |
|---|---|---|
| `API_TOKEN` | Railway API | the static key was deleted 2026-10-13; the API accepts only a Supabase JWT |
| `SUPABASE_JWT_SECRET` | Railway API | read by nothing since 2026-10-13 (`verify_jwt` uses the project JWKS) |
| `EXT_API_TOKEN` | GH | the extension stopped sending a static key 2026-07-21 (Wave 1, #888); the build no longer reads it |
| `SCRAPE_CHAIN_TOKEN` | GH | no workflow reads it since #2043 (price stats chains on its job's own `GITHUB_TOKEN`) |
| `RUNPOD_API_KEY` | Railway worker | no worker code reads it (above) |

Also in M0: the Supabase **legacy JWT secret**. If it sits under JWT Keys → Previously used, anyone who
holds it can mint a `service_role` token that PostgREST accepts. Revoke it only once every client sends a
`sb_publishable_…` key: the SPA does (`VITE_SUPABASE_ANON_KEY`), and so does every extension build since v0.10.143
(`EXT_SUPABASE_ANON_KEY`). Supabase refuses the revoke until API Keys → the legacy tab → **Disable JWT-based API
keys** is done (that switch is reversible; the revoke is not). Never revoke the **Current** signing key (ECC P-256):
it signs every session and never leaves Supabase. Revoked 2026-10-27.

## How a rotation runs

Make each new credential while the old one still works, stage it on Railway without deploying, set the GitHub
secret (the operator runs `gh secret set NAME < file`; Claude's auto mode refuses it), deploy each service once with
sreality-e9's dedup brake (every worker deploy restarts the dedup lease), check, and only then revoke the old one.
Values never enter a chat: generate or hold them in a private scratch file and hand them over with `clip.exe`. The
database goes last (Supabase keeps one postgres password, so there is no overlap): shared pooler
`aws-0-eu-west-1.pooler.supabase.com`, 6543 for `SUPABASE_DB_URL` and `TENANT_POOL_DB_URL`, 5432 for
`SUPABASE_DB_SESSION_URL`; reset under Database settings → Reset password (wait for the green toast), deploy the API
and worker at once, then re-key `tenant_pool`. A burst of failed connections right after the reset is the pooler's
cache, not a bad password: do not reset twice.

## Not rotated here

- Rotated 2026-10-22, before the M0 rotation: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `QWEN_API_KEY`,
  `RUNPOD_API_KEY`, the Cloudflare API token. `VAPID_PRIVATE_KEY` was created after the incident and stays.
- `SCRAPER_PROXY_URL` (GH + worker; the DataImpulse residential proxy, `http://<login>:<password>@gw.dataimpulse.com:823`,
  read through `scraper/portal_base.py` `PROXY_ENV` by idnes, mmreality, ceskereality): rotated 2026-10-27 by operator
  ruling. The plan page's ↻ regenerates the password with no overlap, so take sreality-e9's brake first, then
  regenerate, test (`curl -K -` with the proxy on stdin → CONNECT 200), stage + deploy the worker, `gh secret set`.
  A rejected proxy login is a `ProxyError`, retried, never a gone signal.
- GitHub-only, so outside Railway's incident: `HF_TOKEN`, `SREALITY_LOGIN_EMAIL` / `SREALITY_LOGIN_PASSWORD` /
  `SREALITY_SESSION_COOKIE`. The `GEMINI_API_KEY` Actions secret has no consumer.
- Public values: `SUPABASE_URL`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`, `EXT_SUPABASE_URL`, `EXT_API_BASE_URL`,
  `EXT_APP_BASE_URL`, every `VITE_*`.
- `LOCATION_DB_DIRECT_URL` is read by one workflow but is not set as a secret (the job falls back to the pooler).
