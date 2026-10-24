-- Deploy pkp_scraper:station-carrier-fk to pg
-- requires: init

BEGIN;

ALTER TABLE pkp.disruption
    ADD CONSTRAINT disruption_station_id_fkey
    FOREIGN KEY (station_id) REFERENCES pkp.station(id)
    ON UPDATE CASCADE ON DELETE RESTRICT;

CREATE INDEX disruption_station_id_idx ON pkp.disruption (station_id);

-- carrier_code stays nullable: trains first seen in operations/disruptions have no metadata yet.
ALTER TABLE pkp.train
    ADD CONSTRAINT train_carrier_code_fkey
    FOREIGN KEY (carrier_code) REFERENCES pkp.carrier(code)
    ON UPDATE CASCADE ON DELETE RESTRICT;

CREATE INDEX train_carrier_code_idx ON pkp.train (carrier_code);

COMMIT;
