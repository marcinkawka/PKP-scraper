-- Deploy pkp_scraper:additional-views to pg

BEGIN;

-- XXX Add DDLs here.
CREATE OR REPLACE VIEW pkp.v_train_disruptions
AS 
SELECT t.carrier_code,
    t.category,
    t.number,
    t.name as train_name,
    dt.message,
    s.name as station_name,
    t.intl_number,
    d.train_id,
    d.station_id,
    t.order_id,
    t.schedule_id,
    t.train_order_id,
    d.first_seen_at,
    d.last_seen_at,
    d.operating_date,
    dt.code
   FROM disruption d
     JOIN train t ON d.train_id = t.id
     JOIN disruption_type dt ON d.type_code = dt.code
	join pkp.station s on d.station_id = s.id; 



CREATE OR REPLACE VIEW pkp.v_train_run
AS SELECT tr.operating_date,
    t.category,
    t.name AS train_name,
    t.number,
    s2.name AS station_name,
    s.planned_arr,
    s.planned_dep,
    s.actual_arr,
    s.actual_dep,
    coalesce(EXTRACT(epoch from (s.actual_arr-s.planned_arr))::integer,0) as arr_delay,
    coalesce(EXTRACT(epoch from (s.actual_dep-s.planned_dep))::integer,0) as dep_delay,
    s.confirmed,
    s.seq_shift
   FROM train_run tr
     JOIN train t ON tr.train_id = t.id
     JOIN stop s ON tr.train_id = s.train_id AND tr.operating_date = s.operating_date
     JOIN station s2 ON s2.id = s.station_id;

COMMIT;
