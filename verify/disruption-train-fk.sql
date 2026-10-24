-- Verify pkp_scraper:disruption-train-fk on pg

BEGIN;

-- division by zero when missing
SELECT 1 / count(*)
  FROM pg_constraint
 WHERE conname = 'disruption_train_id_fkey'
   AND conrelid = 'pkp.disruption'::regclass
   AND confrelid = 'pkp.train'::regclass
   AND contype = 'f'
   AND confupdtype = 'c'   -- ON UPDATE CASCADE
   AND confdeltype = 'r';  -- ON DELETE RESTRICT

SELECT 'pkp.disruption_train_id_idx'::regclass;

ROLLBACK;
