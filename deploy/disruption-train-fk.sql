-- Deploy pkp_scraper:disruption-train-fk to pg
-- requires: init

BEGIN;

ALTER TABLE pkp.disruption
    ADD CONSTRAINT disruption_train_id_fkey
    FOREIGN KEY (train_id) REFERENCES pkp.train(id)
    ON UPDATE CASCADE ON DELETE RESTRICT;

-- train_id is not the leading PK column, so the FK needs its own index
-- (joins to train, and checks when a train row is deleted).
CREATE INDEX disruption_train_id_idx ON pkp.disruption (train_id);

COMMIT;
