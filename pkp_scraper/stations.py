from __future__ import annotations

from typing import Any

from .base import PaginatedScraper
from .models import Station


class StationsScraper(PaginatedScraper[Station]):
    endpoint = "dictionaries/stations"

    def parse(self, payload: dict[str, Any]) -> list[Station]:
        return [Station(id=int(item["id"]), name=item["name"].strip()) for item in payload["stations"]]
