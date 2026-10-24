-- Verify pkp_scraper:station-carrier-fk on pg

BEGIN;

-- division by zero when a constraint is missing or has other actions
SELECT 1 / count(*)
  FROM pg_constraint
 WHERE conname = 'disruption_station_id_fkey'
   AND conrelid = 'pkp.disruption'::regclass
   AND confrelid = 'pkp.station'::regclass
   AND contype = 'f'
   AND confupdtype = 'c'   -- ON UPDATE CASCADE
   AND confdeltype = 'r';  -- ON DELETE RESTRICT

SELECT 1 / count(*)
  FROM pg_constraint
 WHERE conname = 'train_carrier_code_fkey'
   AND conrelid = 'pkp.train'::regclass
   AND confrelid = 'pkp.carrier'::regclass
   AND contype = 'f'
   AND confupdtype = 'c'
   AND confdeltype = 'r';

SELECT 'pkp.disruption_station_id_idx'::regclass;
SELECT 'pkp.train_carrier_code_idx'::regclass;

ROLLBACK;
