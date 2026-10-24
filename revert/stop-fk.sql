-- Revert pkp_scraper:stop-fk from pg

BEGIN;

DROP INDEX pkp.stop_station_id_idx;
ALTER TABLE pkp.stop DROP CONSTRAINT stop_station_id_fkey;

DROP INDEX pkp.stop_train_id_idx;
ALTER TABLE pkp.stop DROP CONSTRAINT stop_train_id_fkey;

COMMIT;
