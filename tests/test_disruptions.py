import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import requests

from pkp_scraper.config import ApiSettings
from pkp_scraper.disruptions import DisruptionsScraper, free_text_code

EXAMPLES = Path(__file__).resolve().parent.parent / "example_response"
SETTINGS = ApiSettings(api_key="secret", base_url="https://api.example.test/api/v1")


def make_scraper(payload: dict, **kwargs) -> tuple[DisruptionsScraper, Mock]:
    session = requests.Session()
    response = Mock()
    response.json.return_value = payload
    session.get = Mock(return_value=response)
    return DisruptionsScraper(SETTINGS, session=session, **kwargs), session


def route(order_id: int, seq: int, **overrides) -> dict:
    r = {"scheduleId": 2026, "orderId": order_id, "trainOrderId": order_id + 1,
         "operatingDate": "2026-10-19", "stationId": 82404, "sequenceNumber": seq}
    r.update(overrides)
    return {k: v for k, v in r.items() if v is not None}


def test_params_default_to_today_with_stations():
    scraper, session = make_scraper({"disruptions": []}, stations=[82404])
    scraper.fetch()
    today = date.today().isoformat()
    assert session.get.call_args.args[0] == "https://api.example.test/api/v1/disruptions"
    assert session.get.call_args.kwargs["params"] == {"dateFrom": today, "dateTo": today, "stations": "82404"}


def test_parse_dictionary_codes_from_example():
    payload = json.loads((EXAMPLES / "distruptions.json").read_text(encoding="utf-8"))
    scraper, _ = make_scraper(payload)
    result = scraper.fetch()
    assert len(result.types) == len(payload["disruptionTypes"])  # all messages are codes here
    assert {t.code for t in result.types} >= {d.type_code for d in result.items}
    first = result.items[0]
    assert (first.order_id, first.train_order_id, first.seq, first.type_code) == (576474864, 841782846, 83, "utr_62")
    assert first.operating_date == date(2026, 10, 18) and first.station_id == 33506


def test_free_text_message_gets_hash_code_and_own_type():
    text = "Na odcinku od stacji A do stacji B obowiązuje komunikacja zastępcza."
    payload = {
        "disruptionTypes": {"utr_01": "Wypadek"},
        "disruptions": [
            {"disruptionId": 1, "message": "utr_01", "affectedRoutes": [route(10, 1)]},
            {"disruptionId": 2, "message": f" {text} ", "affectedRoutes": [route(20, 2)]},
        ],
    }
    scraper, _ = make_scraper(payload)
    result = scraper.fetch()
    code = free_text_code(text)
    assert code.startswith("txt_") and len(code) == 20
    assert {(t.code, t.message) for t in result.types} == {("utr_01", "Wypadek"), (code, text)}
    assert [d.type_code for d in result.items] == ["utr_01", code]


def test_routes_without_train_order_id_are_skipped_and_duplicates_removed():
    payload = {
        "disruptionTypes": {"utr_01": "Wypadek"},
        "disruptions": [
            {"disruptionId": 1, "message": "utr_01",
             "affectedRoutes": [route(10, 1), route(10, 1), route(30, 3, trainOrderId=None)]},
            {"disruptionId": 2, "message": "utr_01", "affectedRoutes": [route(10, 1)]},
        ],
    }
    scraper, _ = make_scraper(payload)
    result = scraper.fetch()
    assert len(result.items) == 1 and result.skipped_routes == 1
