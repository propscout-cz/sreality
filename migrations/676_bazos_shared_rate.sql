-- 676_bazos_shared_rate.sql -- bazos's walk and drain share ONE 0.7 req/s budget, as every other portal's do.
--
-- Until #1852's fix every full 20-slot bazos lease overflowed smallint (20 x 2000 ms index, 20 x 1667 ms
-- detail), so the GitHub walk and the worker's drain each fell back to local pacing: 0.5 + 0.6, up to
-- ~1.1 req/s together, and a 403 seen by one never slowed the other. Shared at those rates, a walk
-- (~2,600 pages plus ~780 ad fetches beside it, 7-day mean) would need ~130 min against its 110-min
-- budget and stop before the portal's end, so it would nominate nothing (rule 3).
-- Operator ruling 2026-10-26: 0.7 req/s for both, about today's mean load during a walk (0.64 req/s)
-- without the 1.1 req/s peaks; a walk then takes ~80 min. At 1,429 ms a 20-slot lease (28,580) also
-- fits a smallint before the code fix deploys. Additive (two jsonb keys); idempotent.

update portals
   set operational_limits = coalesce(operational_limits, '{}'::jsonb)
         || '{"index_rate": 0.7, "detail_rate": 0.7}'::jsonb,
       operational_limits_updated_by = 'migration_676_bazos_shared_rate'
 where source = 'bazos'
   and (operational_limits ->> 'index_rate' is distinct from '0.7'
        or operational_limits ->> 'detail_rate' is distinct from '0.7');
