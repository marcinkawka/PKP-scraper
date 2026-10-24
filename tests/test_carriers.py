import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import requests

from pkp_scraper.carriers import CarriersScraper
from pkp_scraper.config import ApiSettings

EXAMPLE = Path(__file__).resolve().parent.parent / "example_response" / "carriers.json"
SETTINGS = ApiSettings(api_key="secret", base_url="https://api.example.test/api/v1")


def make_scraper() -> tuple[CarriersScraper, Mock]:
    session = requests.Session()
    response = Mock()
    response.json.return_value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    session.get = Mock(return_value=response)
    return CarriersScraper(SETTINGS, session=session), session


def test_request_has_api_key_header_and_url():
    scraper, session = make_scraper()
    scraper.scrape()
    url = session.get.call_args.args[0]
    assert url == "https://api.example.test/api/v1/dictionaries/carriers"
    assert session.headers["X-Api-Key"] == "secret"


def test_parse_example_response():
    scraper, _ = make_scraper()
    carriers = scraper.scrape()
    assert len(carriers) > 10
    ic = next(c for c in carriers if c.code == "IC")
    assert ic.valid_from == date(1999, 1, 1)
    assert ic.valid_to == date(2999, 12, 31)
    assert all(c.name == c.name.strip() for c in carriers)
