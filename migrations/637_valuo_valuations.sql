-- 637_valuo_valuations.sql -- One row per paid Valuo call (valuo.cz/docs/api), kept as Valuo said it.
--
-- WHY. Operator ruling 2026-10-19 (the Valuo sprint): a valuation bought from Valuo is an outside opinion,
-- not our estimate, so it gets its own table and CLAUDE.md rule 12 one sentence. Not `estimation_runs`:
-- browser roles hold INSERT/UPDATE there (291), a row with no account falls back to SYSTEM, which every
-- account reads (431), and `property_estimates_public` (412) counts every successful run, so sandbox calls
-- would surface in Browse's "s odhadem" filter.
--
-- THE ROW. `request` is the exact JSON body sent, `provenance` where each value came from (the canonical ad,
-- the property record, a named rule, an assumption, the user), `response` Valuo's answer verbatim (min/max/std
-- keep Valuo's own names; nothing here calls them quartiles), `error` {code, http_status, message}. Keyed by
-- AD: `listing_id` is the property's canonical ad at call time, so a row follows its ad through merges and
-- splits and needs no carrier (rule 18). `environment` + `token_sha` (first 12 hex of the token's sha256)
-- say which Valuo credential answered; analysis filters `environment = 'production'`.
--
-- THE WRITE ORDER (api/valuo.py, the only writer). The row is INSERTed `pending` BEFORE the paid call and
-- UPDATEd once to its terminal state; the partial unique index makes an identical request (same account,
-- environment and body, `as_of` included) either reuse a success or meet its own pending twin, so a double
-- click or a proxy retry cannot pay twice. A pending row older than two minutes is abandoned (`failed`).
--
-- Posture: service-role only. RLS on with zero policies and everything revoked from the browser roles; the
-- SPA reads its own account's rows through GET /properties/{id}/valuo.
--
-- Additive. APPLY BEFORE MERGING (apply_migration.yml): the API routes read and write this table. Idempotent:
-- a re-apply is a no-op. The foreign key onto `listings` takes a brief lock there, hence the lock_timeout.
-- Verify:
--   select relrowsecurity from pg_class where oid = 'public.valuo_valuations'::regclass;              -- t
--   select has_table_privilege('authenticated', 'public.valuo_valuations', 'select');                -- f
--   select count(*) from pg_indexes where tablename = 'valuo_valuations';                            -- 3

set lock_timeout = '5s';

create table if not exists public.valuo_valuations (
  id          bigint      generated always as identity primary key,
  created_at  timestamptz not null default now(),
  account_id  uuid        not null references public.accounts(id),
  listing_id  bigint      not null references public.listings(id),
  environment text        not null check (environment in ('sandbox', 'production')),
  token_sha   text        not null,
  request     jsonb       not null,
  provenance  jsonb       not null,
  response    jsonb,
  error       jsonb,
  status      text        not null default 'pending' check (status in ('pending', 'success', 'failed')),
  constraint valuo_valuations_failed_has_error check ((status = 'failed') = (error is not null)),
  constraint valuo_valuations_success_has_price
    check (status <> 'success' or response -> 'result' ->> 'avg_price' is not null)
);
comment on table public.valuo_valuations is
  'One row per paid Valuo call: the request sent, where each value came from, Valuo''s verbatim answer. '
  'Written only by api/valuo.py (pending before the call, one terminal UPDATE). Not an estimation_runs row.';

create index if not exists valuo_valuations_listing_created_idx
  on public.valuo_valuations (listing_id, created_at desc);

create unique index if not exists valuo_valuations_live_request_uidx
  on public.valuo_valuations (account_id, environment, md5(request::text))
  where status in ('pending', 'success');

alter table public.valuo_valuations enable row level security;
revoke all on public.valuo_valuations from public, anon, authenticated;
revoke all on sequence public.valuo_valuations_id_seq from public, anon, authenticated;
