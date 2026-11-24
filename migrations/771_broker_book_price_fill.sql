-- 771_broker_book_price_fill.sql -- fill broker_book's price columns from 770's projection, once.
--
-- WHY: 770 adds broker_book.price_changes and changes which advert a book row's price pair is read from. The
-- property maintenance pass writes both when it recomputes a property, which for most properties is the next
-- daily sweep; until then the dossier's price figures would mix the two definitions and count every unread row
-- as not repriced. This writes what the pass would write, for every row, in property_id batches of 1,000.
--
-- APPLY AFTER the code of #2639 has rolled out (the deployed pass then keeps the columns current), through
-- apply_migration.yml (psql, autocommit): each batch commits on its own, so a lock wait is one batch, never the
-- whole book. Re-runnable: a row already equal to the projection is not written, so a re-run after a lock
-- timeout (5 s) only redoes what is left. An empty book does nothing (the CI replay).
-- Verify:
--   select count(*) from broker_book_projection v join broker_book b using (property_id, broker_id)
--   where property_id between 600000 and 602000
--     and (b.first_price_czk, b.last_price_czk, b.price_changes)
--         is distinct from (v.first_price_czk, v.last_price_czk, v.price_changes);               -- 0

set lock_timeout = '5s';
set statement_timeout = 0;

do $fill$
declare
  v_from    bigint := 0;
  v_max     bigint;
  v_written bigint := 0;
  v_n       bigint;
begin
  select max(property_id) into v_max from public.broker_book;
  while v_from <= v_max loop
    update public.broker_book b
       set first_price_czk = v.first_price_czk, last_price_czk = v.last_price_czk, price_changes = v.price_changes
      from public.broker_book_projection v
     where v.property_id >= v_from and v.property_id < v_from + 1000
       and b.property_id = v.property_id and b.broker_id = v.broker_id
       and (b.first_price_czk, b.last_price_czk, b.price_changes)
           is distinct from (v.first_price_czk, v.last_price_czk, v.price_changes);
    get diagnostics v_n = row_count;
    v_written := v_written + v_n;
    commit;
    v_from := v_from + 1000;
  end loop;
  raise notice '771: % broker_book rows written', v_written;
end
$fill$;

reset statement_timeout;
reset lock_timeout;
