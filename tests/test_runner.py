from datetime import datetime, timezone
from unittest.mock import Mock

from pkp_scraper.base import Page
from pkp_scraper.runner import run_paginated

GENERATED = datetime(2026, 10, 19, tzinfo=timezone.utc)


def page(number: int, total: int, items: list) -> Page:
    return Page(items=items, generated_at=GENERATED, number=number, total_pages=total, has_next=number < total)


def make_scraper(pages: list) -> Mock:
    scraper = Mock(endpoint="operations", page_size=10)
    scraper.params.return_value = {"stations": "1"}
    scraper.scrape_page.side_effect = pages
    return scraper


def test_fetches_all_pages_with_delay_between_requests():
    scraper = make_scraper([page(1, 3, [1, 2]), page(2, 3, [3]), page(3, 3, [4])])
    db, store, sleep = Mock(), Mock(), Mock()
    total = run_paginated(scraper, db, store, delay_s=5, sleep=sleep)
    assert total == 4
    assert [c.args[0] for c in scraper.scrape_page.call_args_list] == [1, 2, 3]
    assert [c.args[0] for c in store.call_args_list] == [[1, 2], [3], [4]]
    assert [c.args[0] for c in sleep.call_args_list] == [5, 5]  # between requests only
    assert db.log_fetch.call_count == 3
    assert all(c.kwargs["ok"] for c in db.log_fetch.call_args_list)


def test_failed_request_is_logged_and_reraised():
    scraper = make_scraper([page(1, 2, [1]), RuntimeError("503")])
    db, store, sleep = Mock(), Mock(), Mock()
    try:
        run_paginated(scraper, db, store, sleep=sleep)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError")
    last = db.log_fetch.call_args_list[-1]
    assert last.kwargs["ok"] is False and "page 2" in last.kwargs["note"]
