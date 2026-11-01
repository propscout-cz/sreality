-- Location data quality programme, W7: the RÚIAN mirror loads only the levels the platform binds,
-- serves or uses as match keys (docs/design/location-quality/w7-registry.md), and keeps no column
-- nothing reads.
--   1. Retire (SCD-2: valid_to + retired_at, never DELETE) every live `spravni_obvod` unit (22: an
--      OP/SO hybrid keyed on codes OP and SO share, one Praha unit named "Kutná Hora"), `zsj` unit
--      (23,604, all named by their code, 0 gazetteer rows, never bindable), `orp` unit (206) and
--      `pou` unit (393), neither ever bound nor served (operator ruling 2026-11-01). No live unit
--      hangs under any of them. The loader stopped staging all four in this PR and never closes a
--      unit it no longer sees, so this is the only close they get. `last_version_id` stays the
--      last version that loaded them; their geometry rows stay, as registry history.
--   2. Delete the `momc_in_so` (282, OP and SO mixed), `orp_of` (18,774) and `pou_of` (18,774)
--      relations, read by nothing and no longer written.
--   3. Drop six unread columns: ruian_admin_units.nuts_lau (0 non-null), definition_point (0
--      non-null; its partial GiST index goes with it), has_polygon (no writer, no reader),
--      display_path (written, never read); ruian_address_points.praha_obvod_unit_id and
--      momc_unit_id (no reader; the change log keeps both values in its before/after JSON).
--
-- Pre-approved destructive steps (ruling Q13). Backup to R2 FIRST, read the receipt back:
--   gh workflow run backup_before_drop.yml -f label=w7-registry -f targets="ruian_admin_units:id,display_path,has_polygon ruian_address_points:kod_adm,praha_obvod_unit_id,momc_unit_id ruian_admin_unit_relations"
-- APPLY AFTER THE W7 DEPLOY IS CONFIRMED (Railway worker + api) and with NO registry load running:
-- the pre-W7 resolver reads definition_point; the pre-W7 loader writes display_path, momc_unit_id
-- and praha_obvod_unit_id and stages all four levels (it would re-open them). The W7 loader's
-- INSERT omits display_path, which is NOT NULL until this applies: do not dispatch a load before.
-- A 5 s lock timeout may trip on the resolver's reads of ruian_address_points: run it again.
-- Receipt: 24,225 units retired, 37,830 relation rows deleted.

begin;
set local lock_timeout = '5s';

update ruian_admin_units
   set valid_to = current_date, retired_at = now()
 where level in ('spravni_obvod', 'zsj', 'orp', 'pou') and valid_to is null;

delete from ruian_admin_unit_relations where relation_type in ('momc_in_so', 'orp_of', 'pou_of');

alter table ruian_admin_units
  drop column if exists nuts_lau,
  drop column if exists definition_point,
  drop column if exists has_polygon,
  drop column if exists display_path;

alter table ruian_address_points
  drop column if exists praha_obvod_unit_id,
  drop column if exists momc_unit_id;

commit;
