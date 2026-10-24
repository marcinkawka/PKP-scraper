from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .base import API_TZ, PaginatedScraper
from .config import ApiSettings
from .models import Stop, TrainRun

def _local_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).replace(tzinfo=API_TZ) if value else None


def _parse_stop(item: dict[str, Any]) -> Stop:
    planned_seq = int(item["plannedSequenceNumber"])
    actual_seq = item.get("actualSequenceNumber")
    return Stop(
        seq=planned_seq,
        station_id=int(item["stationId"]),
        seq_shift=planned_seq - int(actual_seq) if actual_seq is not None else 0,
        planned_arr=_local_ts(item.get("plannedArrival")),
        planned_dep=_local_ts(item.get("plannedDeparture")),
        actual_arr=_local_ts(item.get("actualArrival")),
        actual_dep=_local_ts(item.get("actualDeparture")),
        confirmed=bool(item.get("isConfirmed", False)),
    )


def _unique_stops(items: list[dict[str, Any]]) -> tuple[Stop, ...]:
    # The API sometimes repeats a stop with the same plannedSequenceNumber but another
    # actualSequenceNumber (same times). Keep one per seq, preferring seq_shift == 0.
    by_seq: dict[int, Stop] = {}
    for stop in map(_parse_stop, items):
        kept = by_seq.get(stop.seq)
        if kept is None or abs(stop.seq_shift) < abs(kept.seq_shift):
            by_seq[stop.seq] = stop
    return tuple(sorted(by_seq.values(), key=lambda s: s.seq))


class OperationsScraper(PaginatedScraper[TrainRun]):
    """`operations` endpoint: current state of train runs (one item per train and operating day)."""

    endpoint = "operations"
    page_size = 10_000

    def __init__(
        self,
        settings: ApiSettings,
        stations: Iterable[int] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings, **kwargs)
        self.stations = list(stations) if stations is not None else None

    def params(self) -> Mapping[str, Any]:
        if self.stations:
            return {"stations": ",".join(str(s) for s in self.stations)}
        return {}

    def parse(self, payload: dict[str, Any]) -> list[TrainRun]:
        return [
            TrainRun(
                schedule_id=int(train["scheduleId"]),
                order_id=int(train["orderId"]),
                train_order_id=int(train["trainOrderId"]),
                operating_date=date.fromisoformat(train["operatingDate"]),
                status=train["trainStatus"].strip(),
                stops=_unique_stops(train.get("stations", [])),
            )
            for train in payload.get("trains", [])
        ]
