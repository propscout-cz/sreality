-- 688: the Settings "Filter availability" matrix goes (operator ruling 2026-10-29: "remove the
-- filters if they truly do not do anything").
--
-- filter_visibility (migration 059) held one agenda x filter switch per pair the registry
-- declares. No switch did anything: no filter form was handed the matrix, the agent tool
-- schema was built without a connection (so it read the all-on default), and no SQL
-- function, view or RPC reads the table. Live on 2026-10-29: 220 rows, every one enabled,
-- none edited since the seed. Where a filter applies is the registry's own `agendas`
-- declaration (toolkit/filter_registry.py), which is what every form, compiler and tool reads.
--
-- DESTRUCTIVE: applied only after the operator's OK and a backup of the table
-- (backup_before_drop.yml, label filter-visibility, to R2), and only once the PR that removes
-- its last readers (the /admin/filter-schema and /admin/filter-visibility routes) is live.

set lock_timeout = '5s';

drop table if exists public.filter_visibility;

reset lock_timeout;
