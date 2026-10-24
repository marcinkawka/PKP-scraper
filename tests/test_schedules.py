import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import requests

from pkp_scraper.config import ApiSettings
from pkp_scraper.schedules import SchedulesScraper

EXAMPLE = Path(__file__).resolve().parent.parent / "example_response" / "schedules_82404.json"
SETTINGS = ApiSettings(api_key="secret", base_url="https://api.example.test/api/v1")


def make_scraper(payload: dict, **kwargs) -> tuple[SchedulesScraper, Mock]:
    session = requests.Session()
    response = Mock()
    response.json.return_value = payload
    session.get = Mock(return_value=response)
    return SchedulesScraper(SETTINGS, session=session, **kwargs), session


def test_params_default_to_today_and_join_stations():
    scraper, session = make_scraper({"routes": []}, stations=[82404, 1])
    scraper.scrape()
    today = date.today().isoformat()
    assert session.get.call_args.args[0] == "https://api.example.test/api/v1/schedules"
    assert session.get.call_args.kwargs["params"] == {
        "dateFrom": today,
        "dateTo": today,
        "stations": "82404,1",
    }


def test_params_without_stations():
    scraper, _ = make_scraper({"routes": []}, date_from=date(2026, 1, 1), date_to=date(2026, 1, 2))
    assert dict(scraper.params()) == {"dateFrom": "2026-01-01", "dateTo": "2026-01-02"}


def test_parse_real_response():
    scraper, _ = make_scraper(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    result = scraper.fetch()
    assert len(result.items) == 20
    assert result.generated_at is not None
    first = result.items[0]
    assert (first.schedule_id, first.order_id, first.train_order_id) == (2026, 950428432, 467701821)
    assert (first.carrier_code, first.category, first.number, first.name) == ("PR", "K7", "30649", None)
    assert any(t.name == "KARPATY" for t in result.items)


def test_parse_international_number_and_blank_name():
    route = {
        "scheduleId": 2026, "orderId": 1, "trainOrderId": 2, "carrierCode": "IC",
        "nationalNumber": "1234", "commercialCategorySymbol": "EC", "name": "  ",
        "internationalArrivalNumber": "EC 41",
    }
    scraper, _ = make_scraper({"routes": [route]})
    train = scraper.scrape()[0]
    assert train.name is None and train.intl_number == "EC 41"
