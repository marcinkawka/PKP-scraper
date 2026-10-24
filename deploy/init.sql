-- Deploy pkp_scraper:init to pg

BEGIN;
-- 001_init.sql — schemat operacyjny dla PKP PLK API (mikrus, limit 200 MB)
-- PostgreSQL 13+. Zero rozszerzeń.
--
-- Zasady:
--   * planu NIE trzymamy osobno: plannedArrival/Departure przychodzą w operations,
--     a schedules służy tylko do uzupełnienia metadanych pociągu (numer, kategoria, nazwa, przewoźnik)
--   * surowe stopy: partycje dzienne, krótka retencja (domyślnie 14 dni), DROP zamiast DELETE
--   * przed wyrzuceniem partycji liczymy podsumowanie do train_run — to jest zbiór długożyjący
--   * stan jest UPSERT-owany (jeden wiersz na stop), nie dopisywany co poll

CREATE SCHEMA IF NOT EXISTS pkp;

-- ───────────────────────── słowniki ─────────────────────────

CREATE TABLE pkp.carrier (
    code        text PRIMARY KEY,           -- 'IC', 'KM', 'PR' ... (uwaga: ODEG ma w API \r\n w nazwie → trim w loaderze)
    name        text NOT NULL,
    valid_from  date,
    valid_to    date
);

-- Wypełniana „przy okazji" z każdego dictionaries.stations / stations, jakie przyjdzie z API.
-- Uwaga: id nie jest unikalne względem nazwy (np. 'Warszawa Praga' = 36129 i 201092),
-- a id >= 1000001 to węzły zbiorcze ('WARSZAWA -'), nie stacje.
CREATE TABLE pkp.station (
    id    integer PRIMARY KEY,
    name  text NOT NULL
);

-- Słownik przyczyn (disruptionTypes). Wiele kodów ma identyczny tekst, część to szablony z {placeholderami}.
CREATE TABLE pkp.disruption_type (
    code     text PRIMARY KEY,              -- 'utr_48'
    message  text NOT NULL
);

-- ───────────────────────── pociąg (metadane z schedules) ─────────────────────────

CREATE TABLE pkp.train (
    id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- klucz zastępczy: oszczędza miejsce w stop
    schedule_id     smallint NOT NULL,                                  -- rok rozkładowy (2026)
    order_id        bigint   NOT NULL,
    train_order_id  bigint   NOT NULL,
    carrier_code    text,                                               -- bez FK: loader może zobaczyć pociąg z operations przed schedules
    category        text,       -- commercialCategorySymbol; BEZ FK: bywa 'EC/IC', 'IC/MP', 'EN/IC', których nie ma w słowniku
    number          text,       -- nationalNumber jako tekst (numery zmieniają się po trasie, tu: numer trasy)
    name            text,       -- 'TATRY', 'NAREW' ...
    intl_number     text,       -- internationalDepartureNumber / internationalArrivalNumber
    UNIQUE (schedule_id, order_id, train_order_id)
);

-- ───────────────────────── kurs w danym dniu (długożyjący) ─────────────────────────

CREATE TABLE pkp.train_run (
    operating_date  date     NOT NULL,
    train_id        integer  NOT NULL REFERENCES pkp.train(id),
    status          char(1)  NOT NULL,      -- trainStatus; z próbki: C = zakończony, P = w drodze, S = jeszcze nie ruszył (do potwierdzenia)
    updated_at      timestamptz NOT NULL DEFAULT now(),
    -- podsumowanie, wypełniane przez summarize_day() zanim zniknie partycja stop:
    n_confirmed     smallint,               -- ile stopów z potwierdzonym czasem
    max_delay_s     integer,                -- największe opóźnienie (s) na potwierdzonym stopie
    last_delay_s    integer,                -- opóźnienie na ostatnim potwierdzonym stopie
    n_late_5min     smallint,               -- ile stopów z opóźnieniem >= 5 min
    summarized_at   timestamptz,
    PRIMARY KEY (operating_date, train_id)
);

-- ───────────────────────── surowe stopy (partycjonowane, krótka retencja) ─────────────────────────

CREATE TABLE pkp.stop (
    operating_date  date     NOT NULL,
    train_id        integer  NOT NULL,
    seq             smallint NOT NULL,      -- plannedSequenceNumber (to samo co sequenceNumber w disruptions)
    station_id      integer  NOT NULL,
    seq_shift       smallint NOT NULL DEFAULT 0,   -- plannedSeq - actualSeq; != 0 gdy wcześniej na trasie wypadły stacje
    planned_arr     timestamptz,
    planned_dep     timestamptz,
    actual_arr      timestamptz,
    actual_dep      timestamptz,
    confirmed       boolean  NOT NULL DEFAULT false,  -- isConfirmed; false = PROGNOZA, nie wolno jej liczyć do statystyk
    PRIMARY KEY (operating_date, train_id, seq)
) PARTITION BY RANGE (operating_date);

-- Opóźnienia liczymy z timestampów (API pomija pola *DelayMinutes gdy opóźnienie = 0
-- i zaokrągla do minut; z timestampów mamy sekundy).
CREATE VIEW pkp.v_stop_delay AS
SELECT s.*,
       extract(epoch FROM s.actual_arr - s.planned_arr)::integer AS arr_delay_s,
       extract(epoch FROM s.actual_dep - s.planned_dep)::integer AS dep_delay_s
FROM pkp.stop s;

-- ───────────────────────── zakłócenia (długożyjące, małe) ─────────────────────────

CREATE TABLE pkp.disruption (
    operating_date  date     NOT NULL,
    train_id        integer  NOT NULL,
    seq             smallint NOT NULL,                  -- → stop.seq
    station_id      integer  NOT NULL,                  -- duplikat z stop, bo partycja stop zniknie
    type_code       text     NOT NULL REFERENCES pkp.disruption_type(code),
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (operating_date, train_id, seq, type_code)
);

-- ───────────────────────── log odpytań (diagnostyka, retencja ~14 dni) ─────────────────────────

CREATE TABLE pkp.api_fetch (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    endpoint      text        NOT NULL,         -- 'operations' | 'disruptions' | 'schedules'
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    generated_at  timestamptz,                  -- generatedAt z odpowiedzi
    n_items       integer,
    ok            boolean     NOT NULL,
    note          text
);
CREATE INDEX api_fetch_time_idx ON pkp.api_fetch (fetched_at);


commit;