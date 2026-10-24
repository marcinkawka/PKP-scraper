import json
from pathlib import Path
from unittest.mock import Mock

import requests

from pkp_scraper.config import ApiSettings
from pkp_scraper.stations import StationsScraper

EXAMPLE = Path(__file__).resolve().parent.parent / "example_response" / "stations.json"
SETTINGS = ApiSettings(api_key="secret", base_url="https://api.example.test/api/v1")


def make_scraper(payload: dict) -> tuple[StationsScraper, Mock]:
    session = requests.Session()
    response = Mock()
    response.json.return_value = payload
    session.get = Mock(return_value=response)
    return StationsScraper(SETTINGS, session=session), session


def test_scrape_page_sends_pagination_params():
    scraper, session = make_scraper(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    scraper.scrape_page(3)
    assert session.get.call_args.args[0] == "https://api.example.test/api/v1/dictionaries/stations"
    assert session.get.call_args.kwargs["params"] == {"page": 3, "pageSize": 1000}


def test_scrape_page_top_level_pagination():
    scraper, _ = make_scraper(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    page = scraper.scrape_page(1)
    assert len(page.items) == 58
    assert page.total_pages == 1 and not page.has_next
    assert page.generated_at is not None and page.generated_at.tzinfo is not None
    assert page.items[0].id == 1000001


def test_scrape_page_has_next_from_total_pages():
    payload = {"stations": [{"id": 1, "name": "A "}], "page": 1, "totalPages": 2}
    scraper, _ = make_scraper(payload)
    page = scraper.scrape_page(1)
    assert page.has_next and page.items[0].name == "A"


def test_scrape_page_nested_pagination():
    payload = {"stations": [], "pagination": {"page": 2, "totalPages": 5, "hasNextPage": True}}
    scraper, _ = make_scraper(payload)
    page = scraper.scrape_page(2)
    assert page.total_pages == 5 and page.has_next
