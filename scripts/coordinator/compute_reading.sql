-- The compute-size reading: one statement, one row, read only. Paste it into the production SQL tool.
-- verdict LOAD = stay on / go to 2XL; STRAINED = the current size is not coping, go up; QUIET = nothing
-- running needs 2XL. How and when to switch: .claude/skills/database/references/compute-size.md.
with engine as (
  select count(*) as unread
    from autodedup.rt_scope_ids s
    left join autodedup.rt_fp f on f.generation = s.generation and f.listing_id = s.listing_id
   where s.generation = 'rt' and f.listing_id is null
), queue as (
  select count(*) as pending, extract(epoch from now() - min(q.enqueued_at)) / 3600 as oldest_h
    from public.listing_detail_queue q
   where not q.given_up and q.claimed_at is null
), jobs as (
  select string_agg(r.job_name, ', ' order by r.job_name) as running
    from public.job_runs r
   where r.ended_at is null and r.started_at > now() - interval '1 day'
), browse as (
  select avg(extract(epoch from d.end_time - d.start_time)) as avg_s
    from cron.job j
    join cron.job_run_details d on d.jobid = j.jobid
   where j.jobname = 'browse-list-rebuild'
     and d.start_time > now() - interval '1 hour' and d.end_time is not null
), lanes as (
  -- A lane whose interval is 0 is off (or braked); the two hourly lanes pass once an hour.
  select string_agg(e.k, ', ' order by e.k) as stale
    from public.worker_heartbeats w, jsonb_each(w.details) e(k, v)
   where w.worker = 'realtime-worker' and jsonb_typeof(e.v) = 'object'
     and coalesce((e.v ->> 'interval_s')::numeric, 1) > 0
     and e.k not in ('location_refetch', 'sold_comps')
     and (e.v ->> 'last_pass_at')::timestamptz < now() - interval '15 minutes'
), judged as (
  select engine.unread, queue.pending, queue.oldest_h, jobs.running, browse.avg_s, lanes.stale,
         concat_ws('; ',
           case when engine.unread > 2000 then 'engine backlog ' || engine.unread || ' unread' end,
           case when queue.pending > 20000 then 'detail queue ' || queue.pending || ' pending' end,
           case when queue.oldest_h > 6 then 'detail queue oldest ' || round(queue.oldest_h::numeric, 1) || ' h' end
         ) as load,
         concat_ws('; ',
           case when browse.avg_s > 300 then 'browse rebuild ' || round(browse.avg_s::numeric) || ' s' end,
           case when lanes.stale is not null then 'stale lanes: ' || lanes.stale end
         ) as strain
    from engine, queue, jobs, browse, lanes
)
select now() as read_at,
       case current_setting('shared_buffers')
         when '1GB' then 'Medium' when '2GB' then 'Large' when '4GB' then 'XL' when '8GB' then '2XL'
         else current_setting('shared_buffers') end as size,
       pg_postmaster_start_time() as pg_started,
       case when j.load <> '' then 'LOAD' when j.strain <> '' then 'STRAINED' else 'QUIET' end as verdict,
       nullif(j.load, '') as load_reasons,
       nullif(j.strain, '') as strain_reasons,
       j.unread as engine_unread,
       j.pending as queue_pending,
       round(j.oldest_h::numeric, 1) as queue_oldest_h,
       round(j.avg_s::numeric) as browse_rebuild_s,
       j.running as jobs_running,
       (select (a.value #>> '{}')::bigint from public.app_settings a
         where a.key = 'realtime_autodedup_interval_seconds') as engine_brake_s,
       (select h.details #>> '{autodedup,in_flight_s}' from public.worker_heartbeats h
         where h.worker = 'realtime-worker') as engine_in_flight_s
  from judged j;
