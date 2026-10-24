from datetime import date

import psycopg
import pytest

from pkp_scraper.config import DbSettings
from pkp_scraper.db import DBService
from pkp_scraper.models import Carrier

TEST_CODE = "TEST_UPSERT"


@pytest.fixture
def service():
    settings = DbSettings.from_env()
    if settings.host not in {"localhost", "127.0.0.1"}:
        pytest.skip(f"refusing to write test data to non-local db {settings.host}")
    try:
        svc = DBService.connect(settings)
    except psycopg.OperationalError as exc:
        pytest.skip(f"local db not available: {exc}")
    yield svc
    with svc._conn.transaction():
        svc._conn.execute("DELETE FROM pkp.carrier WHERE code = %s", (TEST_CODE,))
    svc.close()


def fetch(svc: DBService) -> tuple:
    return svc._conn.execute(
        "SELECT name, valid_from, valid_to FROM pkp.carrier WHERE code = %s", (TEST_CODE,)
    ).fetchone()


def test_store_carriers_is_idempotent_and_updates(service):
    carrier = Carrier(TEST_CODE, "Old name", date(2020, 1, 1), None)
    service.store_carriers([carrier])
    service.store_carriers([carrier])  # same data again: no error
    assert fetch(service) == ("Old name", date(2020, 1, 1), None)

    service.store_carriers([Carrier(TEST_CODE, "New name", date(2020, 1, 1), date(2999, 12, 31))])
    assert fetch(service) == ("New name", date(2020, 1, 1), date(2999, 12, 31))


TEST_STATION_ID = -1
TEST_ENDPOINT = "test/endpoint"


@pytest.fixture
def cleanup(service):
    yield service
    with service._conn.transaction():
        service._conn.execute("DELETE FROM pkp.station WHERE id = %s", (TEST_STATION_ID,))
        service._conn.execute("DELETE FROM pkp.api_fetch WHERE endpoint = %s", (TEST_ENDPOINT,))


def test_store_stations_is_idempotent_and_updates(cleanup):
    svc = cleanup
    from pkp_scraper.models import Station

    svc.store_stations([Station(TEST_STATION_ID, "Old")])
    svc.store_stations([Station(TEST_STATION_ID, "Old")])
    svc.store_stations([Station(TEST_STATION_ID, "New")])
    name = svc._conn.execute("SELECT name FROM pkp.station WHERE id = %s", (TEST_STATION_ID,)).fetchone()
    assert name == ("New",)


def test_log_fetch(cleanup):
    svc = cleanup
    svc.log_fetch(TEST_ENDPOINT, ok=True, n_items=5, note="page 1/1")
    svc.log_fetch(TEST_ENDPOINT, ok=False, note="boom")
    rows = svc._conn.execute(
        "SELECT ok, n_items, note FROM pkp.api_fetch WHERE endpoint = %s ORDER BY id", (TEST_ENDPOINT,)
    ).fetchall()
    assert rows == [(True, 5, "page 1/1"), (False, None, "boom")]


TEST_SCHEDULE_ID = -1


@pytest.fixture
def trains_cleanup(service):
    yield service
    with service._conn.transaction():
        for table in ("pkp.stop", "pkp.train_run", "pkp.disruption"):
            service._conn.execute(
                f"DELETE FROM {table} WHERE train_id IN (SELECT id FROM pkp.train WHERE schedule_id = %s)",
                (TEST_SCHEDULE_ID,),
            )
        service._conn.execute("DELETE FROM pkp.train WHERE schedule_id = %s", (TEST_SCHEDULE_ID,))


def test_store_trains_is_idempotent_and_updates(trains_cleanup):
    from pkp_scraper.models import Train

    svc = trains_cleanup
    train = Train(TEST_SCHEDULE_ID, 1, 2, "PR", "K7", "30649", None, None)
    svc.store_trains([train])
    svc.store_trains([train])
    svc.store_trains([Train(TEST_SCHEDULE_ID, 1, 2, "PR", "K7/K7P", "30649", "JAWORZYNA", None)])
    rows = svc._conn.execute(
        "SELECT category, name FROM pkp.train WHERE schedule_id = %s", (TEST_SCHEDULE_ID,)
    ).fetchall()
    assert rows == [("K7/K7P", "JAWORZYNA")]


def test_store_train_runs_creates_stub_train_and_upserts_status(trains_cleanup):
    from pkp_scraper.models import Train, TrainRun

    svc = trains_cleanup
    day = date(2026, 10, 19)
    # train known from schedules: its metadata must survive
    svc.store_trains([Train(TEST_SCHEDULE_ID, 1, 2, "PR", "K7", "30649", None, None)])
    runs = [
        TrainRun(TEST_SCHEDULE_ID, 1, 2, day, "S"),
        TrainRun(TEST_SCHEDULE_ID, 3, 4, day, "S"),  # unknown train -> stub row
    ]
    svc.store_train_runs(runs)
    svc.store_train_runs(runs)
    svc.store_train_runs([TrainRun(TEST_SCHEDULE_ID, 1, 2, day, "C")])

    rows = svc._conn.execute(
        """SELECT t.order_id, t.carrier_code, r.status
             FROM pkp.train_run r JOIN pkp.train t ON t.id = r.train_id
            WHERE t.schedule_id = %s ORDER BY t.order_id""",
        (TEST_SCHEDULE_ID,),
    ).fetchall()
    assert rows == [(1, "PR", "C"), (3, None, "S")]


def test_store_train_runs_stores_stops_in_new_partition(trains_cleanup):
    from datetime import datetime, timedelta, timezone

    from pkp_scraper.models import Stop, TrainRun

    svc = trains_cleanup
    day = date(2030, 1, 15)  # far future: partition certainly does not exist yet
    tz = timezone(timedelta(hours=1))
    planned = datetime(2030, 1, 15, 10, 0, tzinfo=tz)
    stop = Stop(seq=4, station_id=82404, seq_shift=0, planned_arr=planned, planned_dep=None,
                actual_arr=planned + timedelta(minutes=3), actual_dep=None, confirmed=False)
    try:
        svc.store_train_runs([TrainRun(TEST_SCHEDULE_ID, 1, 2, day, "P", (stop,))])
        svc.store_train_runs([TrainRun(TEST_SCHEDULE_ID, 1, 2, day, "P", (stop,))])
        # later fetch without planned time (stations filter) must keep the stored one
        later = Stop(seq=4, station_id=82404, seq_shift=0, planned_arr=None, planned_dep=None,
                     actual_arr=planned + timedelta(minutes=5), actual_dep=None, confirmed=True)
        svc.store_train_runs([TrainRun(TEST_SCHEDULE_ID, 1, 2, day, "C", (later,))])

        row = svc._conn.execute(
            """SELECT s.planned_arr, s.actual_arr, s.confirmed, d.arr_delay_s
                 FROM pkp.stop s
                 JOIN pkp.v_stop_delay d USING (operating_date, train_id, seq)
                 JOIN pkp.train t ON t.id = s.train_id
                WHERE s.operating_date = %s AND t.schedule_id = %s""",
            (day, TEST_SCHEDULE_ID),
        ).fetchone()
        assert row == (planned, planned + timedelta(minutes=5), True, 300)
    finally:
        with svc._conn.transaction():
            svc._conn.execute("DROP TABLE IF EXISTS pkp.stop_20300115")


TEST_TYPE_CODE = "test_type"


def test_store_disruptions_dictionary_first_and_idempotent(trains_cleanup):
    import time

    from pkp_scraper.models import Disruption, DisruptionType

    svc = trains_cleanup
    day = date(2026, 10, 19)
    disruption = Disruption(TEST_SCHEDULE_ID, 1, 2, day, 5, 82404, TEST_TYPE_CODE)
    try:
        svc.store_disruptions([DisruptionType(TEST_TYPE_CODE, "Old")], [disruption])
        first, last = svc._conn.execute(
            "SELECT first_seen_at, last_seen_at FROM pkp.disruption WHERE type_code = %s", (TEST_TYPE_CODE,)
        ).fetchone()
        time.sleep(0.01)
        svc.store_disruptions([DisruptionType(TEST_TYPE_CODE, "New")], [disruption])
        rows = svc._conn.execute(
            """SELECT d.first_seen_at, d.last_seen_at, dt.message, t.carrier_code
                 FROM pkp.disruption d
                 JOIN pkp.disruption_type dt ON dt.code = d.type_code
                 JOIN pkp.train t ON t.id = d.train_id
                WHERE d.type_code = %s""",
            (TEST_TYPE_CODE,),
        ).fetchall()
        assert len(rows) == 1
        first2, last2, message, carrier = rows[0]
        assert first2 == first and last2 > last  # still reported -> last_seen moves on
        assert message == "New" and carrier is None  # train created as key-only stub
    finally:
        with svc._conn.transaction():
            svc._conn.execute("DELETE FROM pkp.disruption WHERE type_code = %s", (TEST_TYPE_CODE,))
            svc._conn.execute("DELETE FROM pkp.disruption_type WHERE code = %s", (TEST_TYPE_CODE,))
