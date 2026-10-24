-- Deploy pkp_scraper:partitions-management to pg

BEGIN;

-- XXX Add DDLs here.

-- Tworzy partycje dzienne od p_from na p_days dni do przodu. Wołaj z joba co dobę (i raz na starcie).
CREATE OR REPLACE FUNCTION ensure_stop_partitions(p_from date DEFAULT current_date - 1, p_days int DEFAULT 4)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE d date;
BEGIN
    FOR d IN SELECT g::date FROM generate_series(p_from, p_from + p_days, interval '1 day') AS g LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS pkp.%I PARTITION OF pkp.stop FOR VALUES FROM (%L) TO (%L) WITH (fillfactor = 85)',
            'stop_' || to_char(d, 'YYYYMMDD'), d, d + 1);
    END LOOP;
END $$;
 
-- Podsumowanie doby do train_run. Wołaj dla wczoraj PRZED drop_old_stop_partitions().
CREATE OR REPLACE FUNCTION summarize_day(p_date date)
RETURNS integer LANGUAGE sql AS $$
    WITH per_stop AS (
        SELECT train_id, seq,
               GREATEST(extract(epoch FROM actual_arr - planned_arr),
                        extract(epoch FROM actual_dep - planned_dep))::integer AS delay_s
        FROM pkp.stop
        WHERE operating_date = p_date AND confirmed
    ), agg AS (
        SELECT train_id,
               count(*)::smallint                                                   AS n,
               max(delay_s)                                                         AS max_delay_s,
               (array_agg(delay_s ORDER BY seq DESC) FILTER (WHERE delay_s IS NOT NULL))[1] AS last_delay_s,
               (count(*) FILTER (WHERE delay_s >= 300))::smallint                   AS n_late
        FROM per_stop
        GROUP BY train_id
    ), upd AS (
        UPDATE pkp.train_run r
           SET n_confirmed = a.n, max_delay_s = a.max_delay_s, last_delay_s = a.last_delay_s,
               n_late_5min = a.n_late, summarized_at = now()
          FROM agg a
         WHERE r.operating_date = p_date AND r.train_id = a.train_id
        RETURNING 1
    )
    SELECT count(*)::integer FROM upd;
$$;
 
-- Wyrzuca całe partycje starsze niż keep_days. Zwraca liczbę usuniętych.
CREATE OR REPLACE FUNCTION drop_old_stop_partitions(keep_days int DEFAULT 14)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE r record; n integer := 0;
BEGIN
    FOR r IN
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        WHERE i.inhparent = 'pkp.stop'::regclass
          AND c.relname < 'stop_' || to_char(current_date - keep_days, 'YYYYMMDD')
    LOOP
        EXECUTE format('DROP TABLE pkp.%I', r.relname);
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;
 
SELECT ensure_stop_partitions();

COMMIT;
