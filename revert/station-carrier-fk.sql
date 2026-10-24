-- Revert pkp_scraper:station-carrier-fk from pg

BEGIN;

DROP INDEX pkp.train_carrier_code_idx;
ALTER TABLE pkp.train DROP CONSTRAINT train_carrier_code_fkey;

DROP INDEX pkp.disruption_station_id_idx;
ALTER TABLE pkp.disruption DROP CONSTRAINT disruption_station_id_fkey;

COMMIT;
