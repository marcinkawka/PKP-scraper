-- Revert pkp_scraper:additional-views from pg

BEGIN;

DROP VIEW IF EXISTS pkp.v_train_disruptions;
DROP VIEW IF EXISTS pkp.v_train_run;

COMMIT;
