from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping

from .base import BaseScraper, FetchResult, parse_timestamp
from .config import ApiSettings
from .models import Disruption, DisruptionType

FREE_TEXT_PREFIX = "txt_"


def free_text_code(message: str) -> str:
    """Stable short code for a disruption whose `message` is free text, not a dictionary code."""
    return FREE_TEXT_PREFIX + hashlib.sha1(message.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class DisruptionsResult(FetchResult[Disruption]):
    types: list[DisruptionType] = field(default_factory=list)
    skipped_routes: int = 0         # affected routes without full train key (no trainOrderId)


class DisruptionsScraper(BaseScraper[Disruption]):
    """`disruptions` endpoint: disruption dictionary plus disruptions per affected stop.

    `message` is either a code from `disruptionTypes` ('utr_62') or free text; free text
    becomes its own dictionary entry under a hash-based code (see `free_text_code`).
    """

    endpoint = "disruptions"
    KEY_FIELDS = ("scheduleId", "orderId", "trainOrderId", "operatingDate", "sequenceNumber", "stationId")

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

    def fetch(self) -> DisruptionsResult:
        payload = self._get()
        types = self.parse_types(payload)
        items, skipped = self._parse(payload)
        return DisruptionsResult(
            items=items,
            generated_at=parse_timestamp(payload.get("generatedAt")),
            types=types,
            skipped_routes=skipped,
        )

    def parse(self, payload: dict[str, Any]) -> list[Disruption]:
        return self._parse(payload)[0]

    def parse_types(self, payload: dict[str, Any]) -> list[DisruptionType]:
        dictionary = payload.get("disruptionTypes") or {}
        types = {code: DisruptionType(code, text.strip()) for code, text in dictionary.items()}
        for item in payload.get("disruptions", []):
            message = item["message"].strip()
            if message not in dictionary:
                code = free_text_code(message)
                types[code] = DisruptionType(code, message)
        return sorted(types.values(), key=lambda t: t.code)

    def _parse(self, payload: dict[str, Any]) -> tuple[list[Disruption], int]:
        dictionary = payload.get("disruptionTypes") or {}
        seen: dict[Disruption, None] = {}  # ordered set: the API repeats some routes
        skipped = 0
        for item in payload.get("disruptions", []):
            message = item["message"].strip()
            type_code = message if message in dictionary else free_text_code(message)
            for route in item.get("affectedRoutes", []):
                if any(route.get(k) is None for k in self.KEY_FIELDS):
                    skipped += 1
                    continue
                seen[Disruption(
                    schedule_id=int(route["scheduleId"]),
                    order_id=int(route["orderId"]),
                    train_order_id=int(route["trainOrderId"]),
                    operating_date=date.fromisoformat(route["operatingDate"]),
                    seq=int(route["sequenceNumber"]),
                    station_id=int(route["stationId"]),
                    type_code=type_code,
                )] = None
        return list(seen), skipped
