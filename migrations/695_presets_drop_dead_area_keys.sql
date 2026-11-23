-- 695_presets_drop_dead_area_keys.sql -- saved presets lose the two keys the map area no longer has.
--
-- WHY. Operator ruling 2026-10-30 (migration 694, the SPA in the same PR): Browse's map area is ONE value,
-- `bounds` or `circle`. `ListingFilters.locationMode` and `.centerRadius` are deleted, and every saved preset
-- still carries them (16 of 16 on production, 2026-10-30, all `viewport` / null: no preset ever saved a centre +
-- radius). The SPA's reader already drops a key it does not know (filters.ts coerceStoredFilters), so this is
-- data hygiene: the stored blob says what the model says. stats_dashboards and notification_subscriptions hold
-- neither key (0 rows, 2026-10-30).
--
-- WHAT CHANGES. Each preset's filter_spec (the `{filters, sort}` blob, or the legacy bare filters) without the
-- two keys; nothing else in the blob, no other row, no schema. The touch trigger is paused for the update, so
-- updated_at keeps saying when the operator last saved the preset.
--
-- BACKUP FIRST: backup_before_drop.yml, label presets-area-keys, target filter_presets (R2).
-- APPLY ORDER: before the PR merges (apply_migration.yml --ref <branch>); the deployed SPA reads a preset
-- without the keys as their defaults. IDEMPOTENT: a re-run finds no row with either key.

set lock_timeout = '5s';

begin;

alter table public.filter_presets disable trigger filter_presets_touch_updated_at_trg;

update public.filter_presets
   set filter_spec = case
         when jsonb_typeof(filter_spec->'filters') = 'object'
           then jsonb_set(filter_spec, '{filters}', (filter_spec->'filters') - 'locationMode' - 'centerRadius')
         else filter_spec - 'locationMode' - 'centerRadius'
       end
 where coalesce(filter_spec->'filters', filter_spec) ?| array['locationMode', 'centerRadius'];

alter table public.filter_presets enable trigger filter_presets_touch_updated_at_trg;

do $post$
begin
  if exists (select 1 from public.filter_presets
              where coalesce(filter_spec->'filters', filter_spec) ?| array['locationMode', 'centerRadius']) then
    raise exception '695 did not land: a preset still carries locationMode or centerRadius';
  end if;
end
$post$;

commit;

reset lock_timeout;
