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



def test_most_delayed_trains_last_confirmed_stop_and_carrier_filter(trains_cleanup):
    from datetime import datetime, timedelta, timezone

    from pkp_scraper.models import DelayedTrain, Stop, Train, TrainRun

    svc = trains_cleanup
    day = date(2030, 1, 16)
    tz = timezone(timedelta(hours=1))
    planned = datetime(2030, 1, 16, 10, 0, tzinfo=tz)

    def stop(seq: int, hours: int, late_min: int, confirmed: bool = True) -> Stop:
        arr = planned + timedelta(hours=hours)
        return Stop(seq=seq, station_id=82404, seq_shift=0, planned_arr=arr, planned_dep=None,
                    actual_arr=arr + timedelta(minutes=late_min), actual_dep=None, confirmed=confirmed)

    try:
        # no carrier: avoids depending on pkp.carrier content
        svc.store_trains([
            Train(TEST_SCHEDULE_ID, 1, 2, None, "TST", "99001", "Late", None),
            Train(TEST_SCHEDULE_ID, 1, 3, None, "TST", "99002", None, None),
        ])
        svc.store_train_runs([
            # last confirmed stop (+45) wins over the earlier one and over the forecast
            TrainRun(TEST_SCHEDULE_ID, 1, 2, day, "P",
                     (stop(1, 0, 20), stop(2, 1, 45), stop(3, 2, 90, confirmed=False))),
            TrainRun(TEST_SCHEDULE_ID, 1, 3, day, "P", (stop(1, 0, 5),)),  # under the threshold
        ])

        rows = svc.most_delayed_trains(day, ["PR"], exclude=True)
        assert rows == [DelayedTrain(None, "TST", "99001", "Late", rows[0].station_name,
                                     planned + timedelta(hours=1), 45)]
        assert svc.most_delayed_trains(day, ["PR"]) == []
    finally:
        with svc._conn.transaction():
            svc._conn.execute("DROP TABLE IF EXISTS pkp.stop_20300116")


def test_store_train_runs_skips_stops_at_unknown_stations(trains_cleanup):
    from datetime import datetime, timezone

    from pkp_scraper.models import Stop, TrainRun

    svc = trains_cleanup
    day = date(2030, 1, 16)
    when = datetime(2030, 1, 16, 10, 0, tzinfo=timezone.utc)
    known = svc._conn.execute("SELECT id FROM pkp.station ORDER BY id DESC LIMIT 1").fetchone()
    if known is None:
        pytest.skip("pkp.station is empty")
    unknown_id = -999_999  # never a real station

    def stop(seq: int, station_id: int) -> Stop:
        return Stop(seq=seq, station_id=station_id, seq_shift=0, planned_arr=when, planned_dep=None,
                    actual_arr=None, actual_dep=None, confirmed=False)

    run = TrainRun(TEST_SCHEDULE_ID, 1, 2, day, "P",
                   (stop(1, known[0]), stop(2, unknown_id), stop(3, unknown_id)))
    try:
        assert svc.store_train_runs([run]) == {unknown_id: 2}  # reported, no FK error
        rows = svc._conn.execute(
            """SELECT s.seq, s.station_id
                 FROM pkp.stop s JOIN pkp.train t ON t.id = s.train_id
                WHERE s.operating_date = %s AND t.schedule_id = %s""",
            (day, TEST_SCHEDULE_ID),
        ).fetchall()
        assert rows == [(1, known[0])]  # the rest of the run is stored
    finally:
        with svc._conn.transaction():
            svc._conn.execute("DROP TABLE IF EXISTS pkp.stop_20300116")


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


def test_unlucky_train_returns_all_disruptions_of_the_most_disrupted_train(trains_cleanup):
    from pkp_scraper.models import Disruption, DisruptionType, Train

    svc = trains_cleanup
    day = date(2030, 1, 17)  # no real disruptions that day
    try:
        assert svc.unlucky_train(day) == []
        svc.store_trains([
            Train(TEST_SCHEDULE_ID, 1, 2, None, "TST", "99001", "Pechowy", None),
            Train(TEST_SCHEDULE_ID, 1, 3, None, "TST", "99002", None, None),
        ])
        svc.store_disruptions([DisruptionType(TEST_TYPE_CODE, "Awaria")], [
            Disruption(TEST_SCHEDULE_ID, 1, 2, day, 1, 82404, TEST_TYPE_CODE),
            Disruption(TEST_SCHEDULE_ID, 1, 2, day, 2, 82404, TEST_TYPE_CODE),
            Disruption(TEST_SCHEDULE_ID, 1, 3, day, 1, 82404, TEST_TYPE_CODE),
        ])
        rows = svc.unlucky_train(day)
        assert [(r.number, r.train_name, r.message) for r in rows] == [("99001", "Pechowy", "Awaria")] * 2
    finally:
        with svc._conn.transaction():
            svc._conn.execute("DELETE FROM pkp.disruption WHERE type_code = %s", (TEST_TYPE_CODE,))
            svc._conn.execute("DELETE FROM pkp.disruption_type WHERE code = %s", (TEST_TYPE_CODE,))


def test_unlucky_station_ranks_by_share_of_disrupted_trains(trains_cleanup):
    from datetime import datetime, timezone

    from pkp_scraper.models import Disruption, DisruptionType, Stop, Train, TrainRun

    svc = trains_cleanup
    day = date(2030, 1, 18)  # no real data that day
    quiet = svc._conn.execute("SELECT id FROM pkp.station WHERE id <> 82404 ORDER BY id LIMIT 1").fetchone()[0]
    at = datetime(2030, 1, 18, 10, 0, tzinfo=timezone.utc)

    def stop(seq: int, station_id: int) -> Stop:
        return Stop(seq=seq, station_id=station_id, seq_shift=0, planned_arr=at, planned_dep=None,
                    actual_arr=None, actual_dep=None, confirmed=False)

    try:
        assert svc.unlucky_station(day, min_trains=1) is None
        svc.store_trains([Train(TEST_SCHEDULE_ID, 1, 2, None, "TST", "99001", None, None),
                          Train(TEST_SCHEDULE_ID, 1, 3, None, "TST", "99002", None, None)])
        # 82404: two trains, one disrupted (50%); quiet: one train, disrupted (100%)
        svc.store_train_runs([TrainRun(TEST_SCHEDULE_ID, 1, 2, day, "P", (stop(1, 82404), stop(2, quiet))),
                              TrainRun(TEST_SCHEDULE_ID, 1, 3, day, "P", (stop(1, 82404),))])
        svc.store_disruptions([DisruptionType(TEST_TYPE_CODE, "Awaria")], [
            Disruption(TEST_SCHEDULE_ID, 1, 2, day, 1, 82404, TEST_TYPE_CODE),
            Disruption(TEST_SCHEDULE_ID, 1, 2, day, 2, quiet, TEST_TYPE_CODE),
        ])
        best = svc.unlucky_station(day, min_trains=1)
        assert (best.trains, best.disrupted_trains, len(best.disruptions)) == (1, 1, 1)
        assert best.disruptions[0].number == "99001"

        busy = svc.unlucky_station(day, min_trains=2)   # quiet station has too little traffic
        assert (busy.trains, busy.disrupted_trains) == (2, 1)
        assert busy.station_name != best.station_name

        # excluded messages neither pick the station nor show up
        assert svc.unlucky_station(day, ["Awar%"], min_trains=1) is None
    finally:
        with svc._conn.transaction():
            svc._conn.execute("DELETE FROM pkp.disruption WHERE type_code = %s", (TEST_TYPE_CODE,))
            svc._conn.execute("DELETE FROM pkp.disruption_type WHERE code = %s", (TEST_TYPE_CODE,))
            svc._conn.execute("DROP TABLE IF EXISTS pkp.stop_20300118")


def test_disruption_by_station_counts_one_message_per_station(trains_cleanup):
    from pkp_scraper.models import Disruption, DisruptionType, Train

    svc = trains_cleanup
    day = date(2030, 1, 19)  # no real disruptions that day
    other = TEST_TYPE_CODE + "_other"
    try:
        svc.store_trains([Train(TEST_SCHEDULE_ID, 1, 2, None, "TST", "99001", "Pechowy", None),
                          Train(TEST_SCHEDULE_ID, 1, 3, None, "TST", "99002", None, None)])
        svc.store_disruptions(
            [DisruptionType(TEST_TYPE_CODE, "Interwencja testowa"), DisruptionType(other, "Inna")],
            [Disruption(TEST_SCHEDULE_ID, 1, 2, day, 1, 82404, TEST_TYPE_CODE),
             Disruption(TEST_SCHEDULE_ID, 1, 3, day, 1, 82404, TEST_TYPE_CODE),
             Disruption(TEST_SCHEDULE_ID, 1, 3, day, 2, 82404, other)],
        )
        [row] = svc.disruption_by_station(day, "Interwencja testowa")
        assert (row.count, sorted(row.trains)) == (2, ["Pechowy", "TST 99002"])  # name, else category number
    finally:
        with svc._conn.transaction():
            svc._conn.execute("DELETE FROM pkp.disruption WHERE type_code IN (%s, %s)", (TEST_TYPE_CODE, other))
            svc._conn.execute("DELETE FROM pkp.disruption_type WHERE code IN (%s, %s)", (TEST_TYPE_CODE, other))
