from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

from .base import PaginatedScraper
from .db import DBService

T = TypeVar("T")

log = logging.getLogger(__name__)


def run_paginated(
    scraper: PaginatedScraper[T],
    db: DBService,
    store: Callable[[list[T]], None],
    delay_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Fetch every page, store it and log each request in pkp.api_fetch.

    Waits `delay_s` between requests (not after the last one) to go easy on the API.
    Returns the number of stored items. A failed request is logged and re-raised.
    """
    params = dict(scraper.params())
    total = 0
    number = 1
    while True:
        log.info("%s: requesting page %d (pageSize %s, %s)", scraper.endpoint, number, scraper.page_size, params)
        started = time.monotonic()
        try:
            page = scraper.scrape_page(number)
        except Exception as exc:
            log.error("%s: page %d failed: %s", scraper.endpoint, number, exc)
            db.log_fetch(scraper.endpoint, ok=False, note=f"page {number} {params}: {exc}"[:1000])
            raise
        fetched = time.monotonic()
        store(page.items)
        db.log_fetch(
            scraper.endpoint,
            ok=True,
            generated_at=page.generated_at,
            n_items=len(page.items),
            note=f"page {page.number}/{page.total_pages}, pageSize {scraper.page_size} {params}",
        )
        total += len(page.items)
        log.info(
            "%s: page %s/%s done: %d items (fetch %.1fs, store %.1fs), %d items so far",
            scraper.endpoint,
            page.number,
            page.total_pages,
            len(page.items),
            fetched - started,
            time.monotonic() - fetched,
            total,
        )
        if not page.has_next:
            return total
        number += 1
        sleep(delay_s)
