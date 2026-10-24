from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .base import BaseScraper
from .models import Carrier


def _to_date(value: str | None) -> date | None:
    return datetime.fromisoformat(value).date() if value else None


class CarriersScraper(BaseScraper[Carrier]):
    endpoint = "dictionaries/carriers"

    def parse(self, payload: dict[str, Any]) -> list[Carrier]:
        return [
            Carrier(
                code=item["code"].strip(),
                # the API sometimes appends \r\n to names (e.g. ODEG)
                name=item["name"].strip(),
                valid_from=_to_date(item.get("validFrom")),
                valid_to=_to_date(item.get("validTo")),
            )
            for item in payload["carriers"]
        ]
