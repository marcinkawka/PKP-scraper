from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Mapping, TypeVar
from zoneinfo import ZoneInfo

import requests

from .config import ApiSettings

T = TypeVar("T")

# operations / schedules give local times without offset, in Polish local time
API_TZ = ZoneInfo("Europe/Warsaw")


def parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class FetchResult(Generic[T]):
    items: list[T]
    generated_at: datetime | None


class BaseScraper(ABC, Generic[T]):
    """Template for one API endpoint.

    The base class owns the HTTP request (X-Api-Key header, timeout, error handling).
    Subclasses only say *where* to call (`endpoint`, `params`) and *how* to turn the
    JSON payload into domain objects (`parse`).
    """

    API_KEY_HEADER = "X-Api-Key"

    def __init__(self, settings: ApiSettings, session: requests.Session | None = None) -> None:
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers[self.API_KEY_HEADER] = settings.api_key
        self._session.headers.setdefault("Accept", "application/json")

    @property
    @abstractmethod
    def endpoint(self) -> str:
        """Path relative to the base URL, e.g. 'dictionaries/carriers'."""

    def params(self) -> Mapping[str, Any]:
        return {}

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> list[T]:
        """Convert the decoded JSON response into domain objects."""

    def fetch(self) -> FetchResult[T]:
        payload = self._get()
        return FetchResult(items=self.parse(payload), generated_at=parse_timestamp(payload.get("generatedAt")))

    def scrape(self) -> list[T]:
        return self.fetch().items

    def _get(self, extra_params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._settings.base_url}/{self.endpoint.lstrip('/')}"
        params = {**self.params(), **(extra_params or {})}
        response = self._session.get(url, params=params, timeout=self._settings.timeout_s)
        response.raise_for_status()
        return response.json()


@dataclass(frozen=True)
class Page(FetchResult[T]):
    number: int
    total_pages: int
    has_next: bool


class PaginatedScraper(BaseScraper[T]):
    """Endpoint that takes `page` (1-based) and `pageSize` query parameters.

    The caller drives the iteration (and any delay between requests) via `scrape_page`.
    """

    page_size: int = 1000

    def __init__(self, settings: ApiSettings, page_size: int | None = None, **kwargs: Any) -> None:
        super().__init__(settings, **kwargs)
        if page_size is not None:
            self.page_size = page_size

    def scrape_page(self, number: int) -> Page[T]:
        payload = self._get({"page": number, "pageSize": self.page_size})
        # operations nest metadata under "pagination"; dictionaries keep it at the top level
        meta = payload.get("pagination", payload)
        total_pages = int(meta.get("totalPages") or number)
        return Page(
            items=self.parse(payload),
            number=number,
            total_pages=total_pages,
            has_next=bool(meta.get("hasNextPage", number < total_pages)),
            generated_at=parse_timestamp(payload.get("generatedAt")),
        )
