-- Revert pkp_scraper:disruption-train-fk from pg

BEGIN;

DROP INDEX pkp.disruption_train_id_idx;
ALTER TABLE pkp.disruption DROP CONSTRAINT disruption_train_id_fkey;

COMMIT;
