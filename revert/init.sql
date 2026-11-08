-- Revert pkp_scraper:init from pg

BEGIN;

DROP SCHEMA pkp CASCADE;

COMMIT;
