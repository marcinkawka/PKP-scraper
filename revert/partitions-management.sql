-- Revert pkp_scraper:partitions-management from pg

BEGIN;

DROP FUNCTION IF EXISTS pkp.ensure_stop_partitions(date, int);
DROP FUNCTION IF EXISTS pkp.summarize_day(date);
DROP FUNCTION IF EXISTS pkp.drop_old_stop_partitions(int);

COMMIT;
