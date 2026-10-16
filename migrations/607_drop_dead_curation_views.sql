-- 607: drop `collections_public` and `tags_public`, two tenant views nothing reads.
--
-- WHY. Both are `security_invoker` views over `collections` / `tags` (created 025, re-cut 202,
-- `collections_public` again 211, made invoker by 316, granted to `authenticated` by 319).
-- Read 2026-10-16: no reader in api/, toolkit/, scraper/, scripts/, frontend/src or
-- chrome-extension/src; the only names left were two entries in
-- tests/test_tenant_isolation_live.py, removed in the same PR. The SPA reads the collection
-- LIST through `GET /collections` (tenant_conn) and the member map through
-- `collection_properties_public`; tags likewise through `GET /tags` and `property_tags_public`.
-- Those two views stay. Collection sharing (docs/design/collection-sharing.md §5) pays for its
-- new table with this deletion.
--
-- Destructive by class (DROP VIEW); the operator ruled yes 2026-10-17 (ruling 7). A view holds
-- no data, and its definition lives in 025/202/211/316/319, so there is nothing to back up. No
-- view or function in the migrations reads either one; a dependent created outside them would
-- make the DROP fail (no CASCADE), which is the wanted outcome.
--
-- ROLLBACK: forward only (rule 1); re-create from 211's (collections) and 202's (tags)
-- definitions plus 316 and 319. A re-run is a no-op (IF EXISTS).

set lock_timeout = '5s';

drop view if exists collections_public;
drop view if exists tags_public;
