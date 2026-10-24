from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from .base import BaseScraper, FetchResult, parse_timestamp
from .config import ApiSettings
from .models import Train
from .timetable import Timetable


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class SchedulesResult(FetchResult[Train]):
    timetable: Timetable = field(default_factory=Timetable)


class SchedulesScraper(BaseScraper[Train]):
    """`schedules` endpoint: one route per train, for train metadata and planned stop times.

    `date_from` / `date_to` default to today; `stations` limits routes to those
    calling at the given station ids (None = all stations).
    """

    endpoint = "schedules"

    def __init__(
        self,
        settings: ApiSettings,
        date_from: date | None = None,
        date_to: date | None = None,
        stations: Iterable[int] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings, **kwargs)
        today = date.today()
        self.date_from = date_from or today
        self.date_to = date_to or today
        self.stations = list(stations) if stations is not None else None

    def params(self) -> Mapping[str, Any]:
        params: dict[str, Any] = {
            "dateFrom": self.date_from.isoformat(),
            "dateTo": self.date_to.isoformat(),
        }
        if self.stations:
            params["stations"] = ",".join(str(s) for s in self.stations)
        return params

    def fetch(self) -> SchedulesResult:
        payload = self._get()
        return SchedulesResult(
            items=self.parse(payload),
            generated_at=parse_timestamp(payload.get("generatedAt")),
            timetable=Timetable.from_routes(payload.get("routes", [])),
        )

    def parse(self, payload: dict[str, Any]) -> list[Train]:
        return [
            Train(
                schedule_id=int(route["scheduleId"]),
                order_id=int(route["orderId"]),
                train_order_id=int(route["trainOrderId"]),
                carrier_code=_clean(route.get("carrierCode")),
                category=_clean(route.get("commercialCategorySymbol")),
                number=_clean(route.get("nationalNumber")),
                name=_clean(route.get("name")),
                intl_number=_clean(
                    route.get("internationalDepartureNumber") or route.get("internationalArrivalNumber")
                ),
            )
            for route in payload.get("routes", [])
        ]
