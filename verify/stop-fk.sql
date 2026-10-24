-- Verify pkp_scraper:stop-fk on pg

BEGIN;

-- division by zero when a constraint is missing or has other actions
SELECT 1 / count(*)
  FROM pg_constraint
 WHERE conname = 'stop_train_id_fkey'
   AND conrelid = 'pkp.stop'::regclass
   AND confrelid = 'pkp.train'::regclass
   AND contype = 'f'
   AND confupdtype = 'c'   -- ON UPDATE CASCADE
   AND confdeltype = 'r';  -- ON DELETE RESTRICT

SELECT 1 / count(*)
  FROM pg_constraint
 WHERE conname = 'stop_station_id_fkey'
   AND conrelid = 'pkp.stop'::regclass
   AND confrelid = 'pkp.station'::regclass
   AND contype = 'f'
   AND confupdtype = 'c'
   AND confdeltype = 'r';

SELECT 'pkp.stop_train_id_idx'::regclass;
SELECT 'pkp.stop_station_id_idx'::regclass;

ROLLBACK;
