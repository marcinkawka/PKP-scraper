import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import requests

from pkp_scraper.config import ApiSettings
from pkp_scraper.operations import OperationsScraper

EXAMPLE = Path(__file__).resolve().parent.parent / "example_response" / "operations_82404.json"
SETTINGS = ApiSettings(api_key="secret", base_url="https://api.example.test/api/v1")


def make_scraper(payload: dict, **kwargs) -> tuple[OperationsScraper, Mock]:
    session = requests.Session()
    response = Mock()
    response.json.return_value = payload
    session.get = Mock(return_value=response)
    return OperationsScraper(SETTINGS, session=session, **kwargs), session


def test_params_include_stations_and_default_page_size():
    scraper, session = make_scraper({"trains": [], "pagination": {"totalPages": 1}}, stations=[82404])
    scraper.scrape_page(2)
    assert session.get.call_args.args[0] == "https://api.example.test/api/v1/operations"
    assert session.get.call_args.kwargs["params"] == {"stations": "82404", "page": 2, "pageSize": 10_000}


def test_page_size_override():
    scraper, session = make_scraper({"trains": [], "pagination": {"totalPages": 1}}, page_size=50)
    scraper.scrape_page(1)
    assert session.get.call_args.kwargs["params"] == {"page": 1, "pageSize": 50}


def test_parse_real_response():
    scraper, _ = make_scraper(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    page = scraper.scrape_page(1)
    assert len(page.items) == 49 and not page.has_next
    run = page.items[0]
    assert (run.schedule_id, run.order_id, run.train_order_id) == (2026, 472826001, 554885819)
    assert run.operating_date == date(2026, 10, 19) and run.status == "C"


def test_parse_original_example_has_next_page():
    example = EXAMPLE.with_name("operations.json")
    scraper, _ = make_scraper(json.loads(example.read_text(encoding="utf-8")))
    page = scraper.scrape_page(1)
    assert page.total_pages == 71 and page.has_next


def test_parse_stops_local_time_and_dedup():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    scraper, _ = make_scraper(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    runs = scraper.scrape_page(1).items
    first = runs[0].stops[0]
    assert first.station_id == 82404 and first.seq == 4 and first.confirmed
    assert first.actual_arr == datetime(2026, 10, 19, 10, 35, tzinfo=ZoneInfo("Europe/Warsaw"))
    assert first.planned_arr is None  # stations-filtered response has no planned times
    # order 665128879 repeats seq 51 (actual 51 and 52): one stop left, the unshifted one
    dup = next(r for r in runs if r.order_id == 665128879)
    seq51 = [s for s in dup.stops if s.seq == 51]
    assert len(seq51) == 1 and seq51[0].seq_shift == 0
    for run in runs:
        seqs = [s.seq for s in run.stops]
        assert len(seqs) == len(set(seqs))


def test_parse_stops_planned_times_and_unconfirmed():
    example = EXAMPLE.with_name("operations.json")
    scraper, _ = make_scraper(json.loads(example.read_text(encoding="utf-8")))
    stops = [s for r in scraper.scrape_page(1).items for s in r.stops]
    assert any(s.planned_arr is not None for s in stops)
    assert any(not s.confirmed for s in stops)  # isConfirmed missing -> forecast
