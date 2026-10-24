-- Deploy pkp_scraper:stop-fk to pg
-- requires: init

BEGIN;

-- pkp.stop is partitioned: constraints and indexes defined on the parent are created
-- on every existing partition and on partitions added later.

ALTER TABLE pkp.stop
    ADD CONSTRAINT stop_train_id_fkey
    FOREIGN KEY (train_id) REFERENCES pkp.train(id)
    ON UPDATE CASCADE ON DELETE RESTRICT;

CREATE INDEX stop_train_id_idx ON pkp.stop (train_id);

ALTER TABLE pkp.stop
    ADD CONSTRAINT stop_station_id_fkey
    FOREIGN KEY (station_id) REFERENCES pkp.station(id)
    ON UPDATE CASCADE ON DELETE RESTRICT;

CREATE INDEX stop_station_id_idx ON pkp.stop (station_id);

COMMIT;
