-- 606: tenant ids are system-generated (GENERATED ALWAYS AS IDENTITY).
--
-- WHY. Twelve tenant tables kept the bigserial ids they were born with, and `authenticated` -- every
-- browser JWT through PostgREST, and the API's tenant_conn after SET LOCAL ROLE -- may INSERT (on
-- most also UPDATE) the whole row, `id` included. RLS's WITH CHECK tests only account_id, so a
-- signed-in caller could write rows with ids just above the sequence, and every account's next
-- inserts would draw a claimed id and fail on the primary key (HTTP 500) until the range was burned.
-- Not exploited: no table holds an id above its sequence today (max(id) <= last_value on all twelve).
--
-- WHAT. Each id becomes GENERATED ALWAYS AS IDENTITY, continuing past both max(id) and the old
-- sequence. ALWAYS refuses a caller's id: an INSERT needs OVERRIDING SYSTEM VALUE, which PostgREST
-- cannot send, and an UPDATE may only set it to DEFAULT. The executor draws an identity value without
-- a privilege check on its sequence, so the `grant usage on sequence ... to authenticated` of
-- migrations 290-294, 536 and 592 is moot: the serial sequences are dropped with their grants, and
-- the identity sequences (same names) are revoked from the browser roles whatever the applier's
-- default ACL.
--
-- Additive in effect: no row changes and no rewrite, ids continue where they were, and no code writes
-- an explicit id into these tables. Each table is held ACCESS EXCLUSIVE for a catalog change only,
-- hence the lock_timeout; the DEFAULT is dropped first so no insert can draw from the old sequence
-- after max(id) is read. Idempotent: a table whose id is already an identity is skipped.
-- Verify: the $post$ block below raises unless no browser role can write a non-identity integer id.

begin;

set local lock_timeout = '5s';

do $$
declare
  t   text;
  rel text;
  seq text;
  nxt bigint;
begin
  foreach t in array array[
    'pipeline_stages', 'property_pipeline_events', 'collections', 'tags', 'property_notes',
    'property_dismissals', 'property_note_attachments', 'estimation_runs', 'estimation_feedback',
    'building_runs', 'building_run_attachments', 'manual_rental_estimates'
  ] loop
    rel := format('public.%I', t);
    continue when (select attidentity from pg_attribute
                    where attrelid = rel::regclass and attname = 'id') = 'a';
    seq := pg_get_serial_sequence(rel, 'id');
    if seq is null then
      raise exception '606: %.id is neither an identity nor a serial column', rel;
    end if;
    execute format('alter table %s alter column id drop default', rel);
    execute format('select coalesce(max(id), 0) from %s', rel) into nxt;
    nxt := greatest(nxt, coalesce(pg_sequence_last_value(seq::regclass), 0)) + 1;
    execute format('drop sequence %s', seq);
    execute format('alter table %s alter column id add generated always as identity (start with %s)',
                   rel, nxt);
    execute format('revoke all on sequence %s from anon, authenticated',
                   pg_get_serial_sequence(rel, 'id'));
  end loop;
end
$$;

do $post$
declare
  bad text;
begin
  select string_agg(distinct c.oid::regclass::text, ', ') into bad
    from pg_class c
    join pg_attribute a on a.attrelid = c.oid and a.attname = 'id' and not a.attisdropped
    cross join (values ('anon'::name), ('authenticated'::name)) r(role)
   where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p')
     and a.atttypid in ('int2'::regtype, 'int4'::regtype, 'int8'::regtype)
     and a.attidentity <> 'a'
     and (has_column_privilege(r.role, c.oid, a.attnum, 'INSERT')
          or has_column_privilege(r.role, c.oid, a.attnum, 'UPDATE'));
  if bad is not null then
    raise exception '606 did not land: a browser role can write the id of %', bad;
  end if;
end
$post$;

notify pgrst, 'reload schema';

commit;
