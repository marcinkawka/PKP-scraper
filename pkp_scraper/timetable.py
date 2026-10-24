from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

from .base import API_TZ
from .models import Stop, TrainRun

log = logging.getLogger(__name__)

# (schedule_id, order_id, train_order_id, operating_date, seq)
StopKey = tuple[int, int, int, date, int]


@dataclass(frozen=True)
class PlannedStop:
    arr: datetime | None
    dep: datetime | None


def _seconds(value: str | None) -> int | None:
    """'HH:MM:SS' -> seconds since midnight; tolerates hours >= 24."""
    if not value:
        return None
    h, m, *rest = value.split(":")
    return int(h) * 3600 + int(m) * 60 + (int(rest[0]) if rest else 0)


def _at(day: date, seconds: int | None, day_offset: int) -> datetime | None:
    if seconds is None:
        return None
    midnight = datetime(day.year, day.month, day.day, tzinfo=API_TZ)
    # timedelta on an aware datetime moves wall-clock time, which is what a timetable means
    return midnight + timedelta(days=day_offset, seconds=seconds)


def _closest(planned: datetime | None, actual: datetime | None) -> datetime | None:
    """Overnight trains: pick the planned day (-1/0/+1) nearest to the actual time, if known."""
    if planned is None or actual is None:
        return planned
    return min((planned + timedelta(days=d) for d in (-1, 0, 1)), key=lambda c: abs(c - actual))


class Timetable:
    """Planned arrival/departure per stop, built from `schedules` routes.

    Schedules give times of day only. Within one route a time earlier than the previous
    stop's time means the train passed midnight; a single listed stop (stations filter)
    cannot show that, which `fill` corrects with the actual time when there is one.
    """

    def __init__(self, stops: dict[StopKey, PlannedStop] | None = None) -> None:
        self._stops: dict[StopKey, PlannedStop] = dict(stops or {})

    def __len__(self) -> int:
        return len(self._stops)

    def get(self, key: StopKey) -> PlannedStop | None:
        return self._stops.get(key)

    def merge(self, other: Timetable) -> None:
        self._stops.update(other._stops)

    @classmethod
    def from_routes(cls, routes: Iterable[dict[str, Any]]) -> Timetable:
        stops: dict[StopKey, PlannedStop] = {}
        for route in routes:
            # (seq, arr_s, arr_offset, dep_s, dep_offset) with day offsets from midnight rollovers
            entries = []
            offset, previous = 0, None
            for item in sorted(route.get("stations", []), key=lambda s: int(s["orderNumber"])):
                times = []
                for field in ("arrivalTime", "departureTime"):
                    seconds = _seconds(item.get(field))
                    if seconds is not None:
                        if previous is not None and seconds < previous:
                            offset += 1
                        previous = seconds
                    times.append((seconds, offset))
                (arr, arr_off), (dep, dep_off) = times
                entries.append((int(item["orderNumber"]), arr, arr_off, dep, dep_off))
            key = (int(route["scheduleId"]), int(route["orderId"]), int(route["trainOrderId"]))
            for raw_day in route.get("operatingDates", []):
                day = date.fromisoformat(raw_day[:10])
                for seq, arr, arr_off, dep, dep_off in entries:
                    stops[(*key, day, seq)] = PlannedStop(_at(day, arr, arr_off), _at(day, dep, dep_off))
        return cls(stops)

    def fill(self, run: TrainRun) -> TrainRun:
        """Copy of `run` with missing planned times taken from the timetable."""
        stops: list[Stop] = []
        for stop in run.stops:
            planned = None
            if stop.planned_arr is None or stop.planned_dep is None:
                planned = self.get(
                    (run.schedule_id, run.order_id, run.train_order_id, run.operating_date, stop.seq)
                )
            if planned is None:
                stops.append(stop)
                continue
            stops.append(replace(
                stop,
                planned_arr=stop.planned_arr or _closest(planned.arr, stop.actual_arr),
                planned_dep=stop.planned_dep or _closest(planned.dep, stop.actual_dep),
            ))
        return replace(run, stops=tuple(stops))


class ScheduleTimetableProvider:
    """Fills planned times of operations runs from schedules, fetched on demand.

    `fetch(date_from, date_to)` returns a Timetable for that date range; each date is fetched
    at most once. A failed fetch is logged and the runs are returned without planned times.
    """

    def __init__(
        self,
        fetch: Callable[[date, date], Timetable],
        delay_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetch = fetch
        self._delay_s = delay_s
        self._sleep = sleep
        self._timetable = Timetable()
        self._loaded: set[date] = set()

    def fill(self, runs: Iterable[TrainRun]) -> list[TrainRun]:
        runs = list(runs)
        missing = {
            r.operating_date
            for r in runs
            if any(s.planned_arr is None or s.planned_dep is None for s in r.stops)
        } - self._loaded
        if missing:
            date_from, date_to = min(missing), max(missing)
            self._loaded |= {date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)}
            self._sleep(self._delay_s)  # an operations request came right before
            try:
                self._timetable.merge(self._fetch(date_from, date_to))
            except Exception:
                log.exception("schedules %s..%s failed, storing runs without planned times", date_from, date_to)
        return [self._timetable.fill(r) for r in runs]
