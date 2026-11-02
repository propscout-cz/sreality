-- The engine reading: one statement, one row, read only. Paste it into the production SQL tool.
-- total_unread is the four kraje below; total_all_blocks counts every block, so a new block never hides.
with unread as (
  select s.block_key, count(*) as ads
    from autodedup.rt_scope_ids s
    left join autodedup.rt_fp f on f.generation = s.generation and f.listing_id = s.listing_id
   where s.generation = 'rt' and f.listing_id is null
   group by s.block_key
), merges as (
  select count(distinct e.merge_group_id) as merges, count(*) as merged_ads
    from public.property_merge_events e
   where e.source = 'autodedup' and e.created_at >= now() - interval '1 hour'
), lease as (
  select j.lease_holder, j.lease_expires_at
    from public.scheduled_jobs j
   where j.job_name = 'autodedup_realtime'
)
select now() as read_at,
       coalesce(sum(u.ads) filter (where u.block_key in ('okres:3501', 'okres:3504', 'okres:3505', 'okres:3608')), 0)::bigint as unread_lib,
       coalesce(sum(u.ads) filter (where u.block_key in ('okres:3603', 'okres:3606', 'okres:3609', 'okres:3611')), 0)::bigint as unread_pak,
       coalesce(sum(u.ads) filter (where u.block_key in ('okres:3602', 'okres:3604', 'okres:3605', 'okres:3607', 'okres:3610')), 0)::bigint as unread_khk,
       coalesce(sum(u.ads) filter (where u.block_key in ('okres:3304', 'okres:3601', 'okres:3707', 'okres:3710', 'okres:3714')), 0)::bigint as unread_vys,
       coalesce(sum(u.ads) filter (where u.block_key in ('okres:3501', 'okres:3504', 'okres:3505', 'okres:3608',
                                                         'okres:3603', 'okres:3606', 'okres:3609', 'okres:3611',
                                                         'okres:3602', 'okres:3604', 'okres:3605', 'okres:3607', 'okres:3610',
                                                         'okres:3304', 'okres:3601', 'okres:3707', 'okres:3710', 'okres:3714')), 0)::bigint as total_unread,
       coalesce(sum(u.ads), 0)::bigint as total_all_blocks,
       (select m.merges from merges m) as merges_last_hour,
       (select m.merged_ads from merges m) as merged_ads_last_hour,
       (select (c.value ->> 'cap')::bigint from autodedup.settings c where c.key = 'rt_claim_cap:rt') as claim_cap,
       (select (b.value ->> 'bytes')::bigint / 1048576 from autodedup.settings b where b.key = 'rt_storage_last') as schema_mb,
       (select (a.value #>> '{}')::bigint from public.app_settings a where a.key = 'realtime_autodedup_interval_seconds') as brake_interval_seconds,
       (select l.lease_holder from lease l) as lease_holder,
       (select l.lease_expires_at from lease l) as lease_expires_at
  from unread u;
