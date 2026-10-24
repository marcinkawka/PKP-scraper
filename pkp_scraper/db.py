from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

import psycopg
from psycopg import sql as pgsql

from .config import DbSettings
from .models import Carrier, Disruption, DisruptionType, Station, Train, TrainRun


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

    def upsert_stops(self, runs: Iterable[TrainRun]) -> None:
        """Stops of the given runs; their trains must already exist in pkp.train."""
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
            return
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

    def upsert_disruptions(self, disruptions: Iterable[Disruption]) -> None:
        """Disruptions per affected stop; their type codes must already be in pkp.disruption_type."""
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
            return
        self._ensure_trains(rows)
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
            cur.executemany(sql, rows)

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

    def store_train_runs(self, runs: Iterable[TrainRun]) -> None:
        """Runs and their stops in one transaction; creates missing daily stop partitions."""
        runs = list(runs)
        with self._conn.transaction():
            self._repo.upsert_train_runs(runs)
            self._repo.ensure_stop_partitions(r.operating_date for r in runs if r.stops)
            self._repo.upsert_stops(runs)

    def store_disruptions(
        self, types: Iterable[DisruptionType], disruptions: Iterable[Disruption]
    ) -> None:
        """Dictionary first (FK target), then disruptions, in one transaction."""
        with self._conn.transaction():
            self._repo.upsert_disruption_types(types)
            self._repo.upsert_disruptions(disruptions)

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
