from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

import psycopg
from psycopg import sql as pgsql

from .config import DbSettings
from .models import (
    Carrier,
    DelayedTrain,
    Disruption,
    DisruptionType,
    Station,
    StationDisruptions,
    StationTrains,
    Train,
    TrainDisruption,
    TrainRun,
)


class DBRepository:
    """Data access only: SQL for the pkp schema. Transactions are the caller's job."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._conn = connection

    def upsert_carriers(self, carriers: Iterable[Carrier]) -> None:
        # Update only when something changed: fewer dead tuples on repeated runs.
        sql = """
            INSERT INTO pkp.carrier AS c (code, name, valid_from, valid_to)
            VALUES (%(code)s, %(name)s, %(valid_from)s, %(valid_to)s)
            ON CONFLICT (code) DO UPDATE
               SET name = EXCLUDED.name,
                   valid_from = EXCLUDED.valid_from,
                   valid_to = EXCLUDED.valid_to
             WHERE (c.name, c.valid_from, c.valid_to) IS DISTINCT FROM
                   (EXCLUDED.name, EXCLUDED.valid_from, EXCLUDED.valid_to)
        """
        rows = [
            {"code": c.code, "name": c.name, "valid_from": c.valid_from, "valid_to": c.valid_to}
            for c in carriers
        ]
        if not rows:
            return
        with self._conn.cursor() as cur:
            cur.executemany(sql, rows)

    def upsert_stations(self, stations: Iterable[Station]) -> None:
        sql = """
            INSERT INTO pkp.station AS s (id, name)
            VALUES (%(id)s, %(name)s)
            ON CONFLICT (id) DO UPDATE
               SET name = EXCLUDED.name
             WHERE s.name IS DISTINCT FROM EXCLUDED.name
        """
        rows = [{"id": s.id, "name": s.name} for s in stations]
        if not rows:
            return
        with self._conn.cursor() as cur:
            cur.executemany(sql, rows)

    def upsert_trains(self, trains: Iterable[Train]) -> None:
        sql = """
            INSERT INTO pkp.train AS t (schedule_id, order_id, train_order_id,
                                        carrier_code, category, number, name, intl_number)
            VALUES (%(schedule_id)s, %(order_id)s, %(train_order_id)s,
                    %(carrier_code)s, %(category)s, %(number)s, %(name)s, %(intl_number)s)
            ON CONFLICT (schedule_id, order_id, train_order_id) DO UPDATE
               SET carrier_code = EXCLUDED.carrier_code,
                   category = EXCLUDED.category,
                   number = EXCLUDED.number,
                   name = EXCLUDED.name,
                   intl_number = EXCLUDED.intl_number
             WHERE (t.carrier_code, t.category, t.number, t.name, t.intl_number) IS DISTINCT FROM
                   (EXCLUDED.carrier_code, EXCLUDED.category, EXCLUDED.number, EXCLUDED.name,
                    EXCLUDED.intl_number)
        """
        rows = [
            {
                "schedule_id": t.schedule_id,
                "order_id": t.order_id,
                "train_order_id": t.train_order_id,
                "carrier_code": t.carrier_code,
                "category": t.category,
                "number": t.number,
                "name": t.name,
                "intl_number": t.intl_number,
            }
            for t in trains
        ]
        if not rows:
            return
        with self._conn.cursor() as cur:
            cur.executemany(sql, rows)

    def upsert_train_runs(self, runs: Iterable[TrainRun]) -> None:
        rows = [
            {
                "schedule_id": r.schedule_id,
                "order_id": r.order_id,
                "train_order_id": r.train_order_id,
                "operating_date": r.operating_date,
                "status": r.status,
            }
            for r in runs
        ]
        if not rows:
            return
        upsert_run = """
            INSERT INTO pkp.train_run AS r (operating_date, train_id, status)
            SELECT %(operating_date)s, t.id, %(status)s
              FROM pkp.train t
             WHERE t.schedule_id = %(schedule_id)s
               AND t.order_id = %(order_id)s
               AND t.train_order_id = %(train_order_id)s
            ON CONFLICT (operating_date, train_id) DO UPDATE
               SET status = EXCLUDED.status,
                   updated_at = now()
             WHERE r.status IS DISTINCT FROM EXCLUDED.status
        """
        self._ensure_trains(rows)
        with self._conn.cursor() as cur:
            cur.executemany(upsert_run, rows)

    def _ensure_trains(self, rows: list[dict]) -> None:
        """Key-only pkp.train rows for trains not seen in schedules yet (metadata comes later
        from upsert_trains). NOT EXISTS, not just ON CONFLICT, so the identity sequence is not
        consumed for trains that already exist."""
        keys = {(r["schedule_id"], r["order_id"], r["train_order_id"]) for r in rows}
        sql = """
            INSERT INTO pkp.train (schedule_id, order_id, train_order_id)
            SELECT %(schedule_id)s, %(order_id)s, %(train_order_id)s
             WHERE NOT EXISTS (SELECT 1 FROM pkp.train
                                WHERE schedule_id = %(schedule_id)s
                                  AND order_id = %(order_id)s
                                  AND train_order_id = %(train_order_id)s)
            ON CONFLICT (schedule_id, order_id, train_order_id) DO NOTHING
        """
        with self._conn.cursor() as cur:
            cur.executemany(
                sql,
                [{"schedule_id": k[0], "order_id": k[1], "train_order_id": k[2]} for k in sorted(keys)],
            )

    def ensure_stop_partitions(self, days: Iterable[date]) -> None:
        """Daily partitions of pkp.stop (pkp.stop_YYYYMMDD), created when missing."""
        with self._conn.cursor() as cur:
            for day in sorted(set(days)):
                cur.execute(
                    pgsql.SQL(
                        "CREATE TABLE IF NOT EXISTS pkp.{} PARTITION OF pkp.stop"
                        " FOR VALUES FROM ({}) TO ({}) WITH (fillfactor = 85)"
                    ).format(
                        pgsql.Identifier(f"stop_{day:%Y%m%d}"),
                        pgsql.Literal(day),
                        pgsql.Literal(day + timedelta(days=1)),
                    )
                )

    def upsert_stops(self, runs: Iterable[TrainRun]) -> dict[int, int]:
        """Stops of the given runs; their trains must already exist in pkp.train.

        Stops at stations missing from pkp.station are skipped (they would break the FK).
        Returns {unknown station_id: number of skipped stops}.
        """
        rows = [
            {
                "schedule_id": r.schedule_id,
                "order_id": r.order_id,
                "train_order_id": r.train_order_id,
                "operating_date": r.operating_date,
                "seq": s.seq,
                "station_id": s.station_id,
                "seq_shift": s.seq_shift,
                "planned_arr": s.planned_arr,
                "planned_dep": s.planned_dep,
                "actual_arr": s.actual_arr,
                "actual_dep": s.actual_dep,
                "confirmed": s.confirmed,
            }
            for r in runs
            for s in r.stops
        ]
        if not rows:
            return {}
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM pkp.station WHERE id = ANY(%s)", [list({r["station_id"] for r in rows})]
            )
            known_ids = {row[0] for row in cur.fetchall()}
        skipped: dict[int, int] = {}
        for r in rows:
            if r["station_id"] not in known_ids:
                skipped[r["station_id"]] = skipped.get(r["station_id"], 0) + 1
        if skipped:
            rows = [r for r in rows if r["station_id"] in known_ids]
            if not rows:
                return skipped
        # Missing timestamps never erase stored ones: e.g. a stations-filtered fetch comes
        # without planned times. Write only when something changed (fewer dead tuples).
        sql = """
            INSERT INTO pkp.stop AS s (operating_date, train_id, seq, station_id, seq_shift,
                                       planned_arr, planned_dep, actual_arr, actual_dep, confirmed)
            SELECT %(operating_date)s::date, t.id, %(seq)s::smallint, %(station_id)s::integer,
                   %(seq_shift)s::smallint,
                   %(planned_arr)s::timestamptz, %(planned_dep)s::timestamptz,
                   %(actual_arr)s::timestamptz, %(actual_dep)s::timestamptz, %(confirmed)s::boolean
              FROM pkp.train t
             WHERE t.schedule_id = %(schedule_id)s
               AND t.order_id = %(order_id)s
               AND t.train_order_id = %(train_order_id)s
            ON CONFLICT (operating_date, train_id, seq) DO UPDATE
               SET station_id = EXCLUDED.station_id,
                   seq_shift = EXCLUDED.seq_shift,
                   planned_arr = COALESCE(EXCLUDED.planned_arr, s.planned_arr),
                   planned_dep = COALESCE(EXCLUDED.planned_dep, s.planned_dep),
                   actual_arr = COALESCE(EXCLUDED.actual_arr, s.actual_arr),
                   actual_dep = COALESCE(EXCLUDED.actual_dep, s.actual_dep),
                   confirmed = EXCLUDED.confirmed
             WHERE (s.station_id, s.seq_shift, s.planned_arr, s.planned_dep,
                    s.actual_arr, s.actual_dep, s.confirmed)
                   IS DISTINCT FROM
                   (EXCLUDED.station_id, EXCLUDED.seq_shift,
                    COALESCE(EXCLUDED.planned_arr, s.planned_arr),
                    COALESCE(EXCLUDED.planned_dep, s.planned_dep),
                    COALESCE(EXCLUDED.actual_arr, s.actual_arr),
                    COALESCE(EXCLUDED.actual_dep, s.actual_dep),
                    EXCLUDED.confirmed)
        """
        with self._conn.cursor() as cur:
            cur.executemany(sql, rows)
        return skipped

    def upsert_disruption_types(self, types: Iterable[DisruptionType]) -> None:
        sql = """
            INSERT INTO pkp.disruption_type AS d (code, message)
            VALUES (%(code)s, %(message)s)
            ON CONFLICT (code) DO UPDATE
               SET message = EXCLUDED.message
             WHERE d.message IS DISTINCT FROM EXCLUDED.message
        """
        rows = [{"code": t.code, "message": t.message} for t in types]
        if not rows:
            return
        with self._conn.cursor() as cur:
            cur.executemany(sql, rows)

    def upsert_disruptions(self, disruptions: Iterable[Disruption]) -> set[int]:
        """Disruptions per affected stop; their type codes must already be in pkp.disruption_type.

        Returns the set of station_ids that were skipped because they are not in pkp.station.
        """
        rows = [
            {
                "schedule_id": d.schedule_id,
                "order_id": d.order_id,
                "train_order_id": d.train_order_id,
                "operating_date": d.operating_date,
                "seq": d.seq,
                "station_id": d.station_id,
                "type_code": d.type_code,
            }
            for d in disruptions
        ]
        if not rows:
            return set()
        all_station_ids = list({r["station_id"] for r in rows})
        with self._conn.cursor() as cur:
            cur.execute("SELECT id FROM pkp.station WHERE id = ANY(%s)", [all_station_ids])
            known_ids = {row[0] for row in cur.fetchall()}
        unknown_ids = set(all_station_ids) - known_ids
        valid_rows = [r for r in rows if r["station_id"] not in unknown_ids] if unknown_ids else rows
        if not valid_rows:
            return unknown_ids
        self._ensure_trains(valid_rows)
        # Every poll that still reports the disruption moves last_seen_at forward.
        sql = """
            INSERT INTO pkp.disruption AS d (operating_date, train_id, seq, station_id, type_code)
            SELECT %(operating_date)s::date, t.id, %(seq)s::smallint, %(station_id)s::integer,
                   %(type_code)s::text
              FROM pkp.train t
             WHERE t.schedule_id = %(schedule_id)s
               AND t.order_id = %(order_id)s
               AND t.train_order_id = %(train_order_id)s
            ON CONFLICT (operating_date, train_id, seq, type_code) DO UPDATE
               SET station_id = EXCLUDED.station_id,
                   last_seen_at = now()
        """
        with self._conn.cursor() as cur:
            cur.executemany(sql, valid_rows)
        return unknown_ids

    def top_station_ids(self, limit: int, window_days: int) -> list[int]:
        """Station IDs ranked by number of planned stops in the last `window_days`.

        Reads from pkp.stop, which is partitioned by day and short-retention — so this
        tracks the current timetable without a manual refresh.
        """
        sql = """
            SELECT station_id
              FROM pkp.stop
             WHERE operating_date >= CURRENT_DATE - %s::integer
             GROUP BY station_id
             ORDER BY count(*) DESC
             LIMIT %s
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, (window_days, limit))
            return [row[0] for row in cur.fetchall()]

    def top_delayed_trains(
        self, day: date, carriers: list[str], exclude: bool, min_delay_min: int, limit: int
    ) -> list[DelayedTrain]:
        """Most delayed trains of `day`, by arrival delay at each train's last confirmed stop.

        `carriers` are carrier codes to keep, or to leave out when `exclude` is set
        (trains without a carrier then count as "not in the list"). Same joins as
        pkp.v_train_run, which does not expose carrier_code.
        """
        sql = """
            SELECT carrier_code, category, number, name, station_name, planned_arr, delay_min
              FROM (
                SELECT DISTINCT ON (t.carrier_code, t.number)
                       t.carrier_code, t.category, t.number, t.name,
                       st.name AS station_name,
                       s.planned_arr,
                       EXTRACT(epoch FROM (s.actual_arr - s.planned_arr))::integer / 60 AS delay_min
                  FROM pkp.train_run tr
                  JOIN pkp.train t ON tr.train_id = t.id
                  JOIN pkp.stop s ON tr.train_id = s.train_id AND tr.operating_date = s.operating_date
                  JOIN pkp.station st ON st.id = s.station_id
                 WHERE tr.operating_date = %(day)s
                   AND coalesce(t.carrier_code = ANY(%(carriers)s), false) = %(include)s
                   AND s.actual_arr - s.planned_arr > make_interval(mins => %(min_delay)s)
                   AND s.confirmed
                 ORDER BY t.carrier_code, t.number, s.planned_arr DESC NULLS LAST
              ) last_stop
             ORDER BY delay_min DESC
             LIMIT %(limit)s
        """
        params = {
            "day": day,
            "carriers": carriers,
            "include": not exclude,
            "min_delay": min_delay_min,
            "limit": limit,
        }
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return [DelayedTrain(*row) for row in cur.fetchall()]

    def unlucky_train_disruptions(self, day: date) -> list[TrainDisruption]:
        """All disruptions of `day`'s most disrupted train (most rows in pkp.v_train_disruptions).

        A train is (carrier_code, number); ties go to the lowest number. Empty when
        nothing was disrupted that day.
        """
        sql = """
            WITH unlucky AS (
                SELECT carrier_code, number
                  FROM pkp.v_train_disruptions
                 WHERE operating_date = %(day)s
                 GROUP BY carrier_code, number
                 ORDER BY count(*) DESC, number, carrier_code
                 LIMIT 1
            )
            SELECT v.carrier_code, v.category, v.number, v.train_name, v.station_name,
                   v.message, v.first_seen_at
              FROM pkp.v_train_disruptions v
              JOIN unlucky u ON u.carrier_code IS NOT DISTINCT FROM v.carrier_code
                            AND u.number IS NOT DISTINCT FROM v.number
             WHERE v.operating_date = %(day)s
             ORDER BY v.first_seen_at, v.station_name, v.message
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, {"day": day})
            return [TrainDisruption(*row) for row in cur.fetchall()]

    def unlucky_station_disruptions(
        self, day: date, excluded: list[str], min_trains: int
    ) -> StationDisruptions | None:
        """Disruptions of `day` at the station where the largest share of trains was disrupted.

        Share = trains with a disruption there / trains stopping there (pkp.stop), so
        busy junctions do not win just by traffic; stations with fewer than
        `min_trains` trains are skipped. Messages matching any LIKE pattern in
        `excluded` are left out, both when picking the station and from the result.
        Ties go to more disrupted trains, then station id. None when no station qualifies.
        """
        sql = """
            WITH disrupted AS (
                SELECT station_id, count(DISTINCT train_id) AS disrupted_trains
                  FROM pkp.v_train_disruptions
                 WHERE operating_date = %(day)s
                   AND NOT message LIKE ANY(%(excluded)s)
                 GROUP BY station_id
            ), unlucky AS (
                SELECT d.station_id, d.disrupted_trains, count(DISTINCT s.train_id) AS trains
                  FROM disrupted d
                  JOIN pkp.stop s ON s.station_id = d.station_id AND s.operating_date = %(day)s
                 GROUP BY d.station_id, d.disrupted_trains
                HAVING count(DISTINCT s.train_id) >= %(min_trains)s
                 ORDER BY d.disrupted_trains::numeric / count(DISTINCT s.train_id) DESC,
                          d.disrupted_trains DESC, d.station_id
                 LIMIT 1
            )
            SELECT u.trains, u.disrupted_trains,
                   v.carrier_code, v.category, v.number, v.train_name, v.station_name,
                   v.message, v.first_seen_at
              FROM pkp.v_train_disruptions v
              JOIN unlucky u ON u.station_id = v.station_id
             WHERE v.operating_date = %(day)s
               AND NOT v.message LIKE ANY(%(excluded)s)
             ORDER BY v.first_seen_at, v.number, v.message
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, {"day": day, "excluded": excluded, "min_trains": min_trains})
            rows = cur.fetchall()
        if not rows:
            return None
        disruptions = tuple(TrainDisruption(*row[2:]) for row in rows)
        return StationDisruptions(disruptions[0].station_name, rows[0][0], rows[0][1], disruptions)

    def disruption_by_station(self, day: date, message: str) -> list[StationTrains]:
        """Stations where `message` was reported on `day`, by station name, with the trains."""
        sql = """
            SELECT station_name, count(*),
                   array_agg(coalesce(train_name, concat_ws(' ', category, number)) ORDER BY first_seen_at, number)
              FROM pkp.v_train_disruptions
             WHERE operating_date = %(day)s
               AND message = %(message)s
             GROUP BY station_name
             ORDER BY station_name
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, {"day": day, "message": message})
            return [StationTrains(name, n, tuple(trains)) for name, n, trains in cur.fetchall()]

    def insert_api_fetch(
        self,
        endpoint: str,
        ok: bool,
        generated_at: datetime | None,
        n_items: int | None,
        note: str | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO pkp.api_fetch (endpoint, generated_at, n_items, ok, note)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (endpoint, generated_at, n_items, ok, note),
        )


class DBService:
    """Use cases for storing scraped data. Owns the connection and transaction boundaries."""

    def __init__(self, connection: psycopg.Connection, repository: DBRepository | None = None) -> None:
        self._conn = connection
        self._repo = repository or DBRepository(connection)

    @classmethod
    def connect(cls, settings: DbSettings) -> DBService:
        connection = psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.dbname,
            user=settings.user,
            password=settings.password,
            # autocommit: transaction() then always means a real BEGIN/COMMIT. Without it a stray
            # read opens an implicit transaction and later writes silently become savepoints.
            autocommit=True,
        )
        return cls(connection)

    def store_carriers(self, carriers: Iterable[Carrier]) -> None:
        with self._conn.transaction():
            self._repo.upsert_carriers(carriers)

    def store_stations(self, stations: Iterable[Station]) -> None:
        with self._conn.transaction():
            self._repo.upsert_stations(stations)

    def store_trains(self, trains: Iterable[Train]) -> None:
        with self._conn.transaction():
            self._repo.upsert_trains(trains)

    def store_train_runs(self, runs: Iterable[TrainRun]) -> dict[int, int]:
        """Runs and their stops in one transaction; creates missing daily stop partitions.

        Returns {station_id: number of stops} skipped because the station is not in pkp.station.
        """
        runs = list(runs)
        with self._conn.transaction():
            self._repo.upsert_train_runs(runs)
            self._repo.ensure_stop_partitions(r.operating_date for r in runs if r.stops)
            return self._repo.upsert_stops(runs)

    def busiest_stations(self, limit: int = 32, window_days: int = 7) -> list[int]:
        """Top-N station IDs by planned-stop count in the recent window.

        Read-only; no transaction needed. Defaults chosen to stay within the
        pkp.stop retention (14 days).
        """
        return self._repo.top_station_ids(limit=limit, window_days=window_days)

    def most_delayed_trains(
        self,
        day: date,
        carriers: Iterable[str],
        *,
        exclude: bool = False,
        min_delay_min: int = 10,
        limit: int = 20,
    ) -> list[DelayedTrain]:
        """Top-N delayed trains of `day` run by `carriers` (or by anyone else, with `exclude`).

        Read-only; no transaction needed.
        """
        return self._repo.top_delayed_trains(
            day=day, carriers=list(carriers), exclude=exclude, min_delay_min=min_delay_min, limit=limit
        )

    def unlucky_train(self, day: date) -> list[TrainDisruption]:
        """Disruptions of the most disrupted train of `day`, oldest first ([] if none).

        Read-only; no transaction needed.
        """
        return self._repo.unlucky_train_disruptions(day)

    def unlucky_station(
        self, day: date, excluded: Iterable[str] = (), *, min_trains: int = 50
    ) -> StationDisruptions | None:
        """The station of `day` with the largest share of disrupted trains (None if none).

        `excluded` are LIKE patterns of messages that do not count, e.g. 'Na odcinku od%';
        stations with fewer than `min_trains` trains that day are skipped.
        Read-only; no transaction needed.
        """
        return self._repo.unlucky_station_disruptions(day, list(excluded), min_trains)

    def disruption_by_station(self, day: date, message: str) -> list[StationTrains]:
        """Where the disruption `message` (exact text) was reported on `day`, by station name.

        Read-only; no transaction needed.
        """
        return self._repo.disruption_by_station(day, message)

    def store_disruptions(
        self, types: Iterable[DisruptionType], disruptions: Iterable[Disruption]
    ) -> set[int]:
        """Dictionary first (FK target), then disruptions, in one transaction.

        Returns the set of station_ids that were skipped (not in pkp.station).
        """
        with self._conn.transaction():
            self._repo.upsert_disruption_types(types)
            return self._repo.upsert_disruptions(disruptions)

    def log_fetch(
        self,
        endpoint: str,
        ok: bool,
        generated_at: datetime | None = None,
        n_items: int | None = None,
        note: str | None = None,
    ) -> None:
        """Record one API call in pkp.api_fetch (committed on its own, so failures are kept too)."""
        with self._conn.transaction():
            self._repo.insert_api_fetch(endpoint, ok, generated_at, n_items, note)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DBService:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
