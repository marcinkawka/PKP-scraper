import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from pkp_scraper.base import API_TZ
from pkp_scraper.models import Stop, TrainRun
from pkp_scraper.operations import OperationsScraper
from pkp_scraper.timetable import ScheduleTimetableProvider, Timetable

EXAMPLES = Path(__file__).resolve().parent.parent / "example_response"
DAY = date(2026, 10, 19)


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=API_TZ)


def route(stations: list[dict], dates=("2026-10-19",)) -> dict:
    return {"scheduleId": 2026, "orderId": 1, "trainOrderId": 2, "operatingDates": list(dates), "stations": stations}


def stop(seq: int, actual_arr=None, planned_arr=None) -> Stop:
    return Stop(seq=seq, station_id=1, seq_shift=0, planned_arr=planned_arr, planned_dep=None,
                actual_arr=actual_arr, actual_dep=None, confirmed=actual_arr is not None)


def run(*stops: Stop, day: date = DAY) -> TrainRun:
    return TrainRun(2026, 1, 2, day, "P", tuple(stops))


def test_from_routes_rolls_over_midnight_within_route():
    tt = Timetable.from_routes([route([
        {"stationId": 1, "orderNumber": 1, "departureTime": "23:40:00"},
        {"stationId": 2, "orderNumber": 2, "arrivalTime": "23:58:00", "departureTime": "00:03:00"},
        {"stationId": 3, "orderNumber": 3, "arrivalTime": "00:30:00"},
    ])])
    assert tt.get((2026, 1, 2, DAY, 1)).dep == at(DAY, 23, 40)
    second = tt.get((2026, 1, 2, DAY, 2))
    assert second.arr == at(DAY, 23, 58) and second.dep == at(DAY + timedelta(days=1), 0, 3)
    assert tt.get((2026, 1, 2, DAY, 3)).arr == at(DAY + timedelta(days=1), 0, 30)


def test_from_routes_one_entry_per_operating_date_and_hours_over_24():
    tt = Timetable.from_routes([route([{"stationId": 1, "orderNumber": 5, "arrivalTime": "24:10:00"}],
                                      dates=("2026-10-19", "2026-10-20"))])
    assert tt.get((2026, 1, 2, DAY, 5)).arr == at(DAY + timedelta(days=1), 0, 10)
    assert tt.get((2026, 1, 2, DAY + timedelta(days=1), 5)).arr == at(DAY + timedelta(days=2), 0, 10)


def test_fill_only_missing_and_uses_actual_to_pick_day():
    # single listed stop after midnight: schedules cannot tell the day, the actual time can
    tt = Timetable.from_routes([route([{"stationId": 1, "orderNumber": 7, "arrivalTime": "00:20:00"}])])
    actual = at(DAY + timedelta(days=1), 0, 25)
    filled = tt.fill(run(stop(7, actual_arr=actual))).stops[0]
    assert filled.planned_arr == at(DAY + timedelta(days=1), 0, 20)

    from_operations = at(DAY, 9, 0)
    kept = tt.fill(run(stop(7, planned_arr=from_operations))).stops[0]
    assert kept.planned_arr == from_operations  # operations wins

    unknown = tt.fill(run(stop(8))).stops[0]
    assert unknown.planned_arr is None


def test_examples_fill_planned_times_for_matching_runs():
    schedules = json.loads((EXAMPLES / "schedules_82404.json").read_text(encoding="utf-8"))
    operations = json.loads((EXAMPLES / "operations_82404.json").read_text(encoding="utf-8"))
    tt = Timetable.from_routes(schedules["routes"])
    runs = OperationsScraper.parse(Mock(), operations)
    filled = [tt.fill(r) for r in runs]
    same_day = [s for r in filled if r.operating_date == DAY for s in r.stops]
    assert same_day and all(s.planned_arr or s.planned_dep for s in same_day)
    # all delays plausible (< 3 h) -> day picked correctly
    for s in same_day:
        for planned, actual in ((s.planned_arr, s.actual_arr), (s.planned_dep, s.actual_dep)):
            if planned and actual:
                assert abs(actual - planned) < timedelta(hours=3)
    other_days = [s for r in filled if r.operating_date != DAY for s in r.stops]
    assert all(s.planned_arr is None and s.planned_dep is None for s in other_days)


def test_provider_fetches_each_date_range_once_with_delay():
    tt = Timetable.from_routes([route([{"stationId": 1, "orderNumber": 7, "arrivalTime": "10:00:00"}])])
    fetch, sleep = Mock(return_value=tt), Mock()
    provider = ScheduleTimetableProvider(fetch, delay_s=5, sleep=sleep)
    runs = [run(stop(7)), run(stop(7), day=DAY - timedelta(days=2))]

    first = provider.fill(runs)
    provider.fill(runs)  # same dates again: cached
    assert fetch.call_args_list == [((DAY - timedelta(days=2), DAY),)]
    assert sleep.call_args_list == [((5,),)]
    assert first[0].stops[0].planned_arr == at(DAY, 10, 0)


def test_provider_failure_keeps_runs_without_planned_times():
    provider = ScheduleTimetableProvider(Mock(side_effect=RuntimeError("503")), sleep=Mock())
    result = provider.fill([run(stop(7))])
    assert result[0].stops[0].planned_arr is None
