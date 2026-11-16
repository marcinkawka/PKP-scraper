import logging

import pytest

from utils.stations import CARRIER_STATIONS, OTHERS_POOL, normalize_code, station_for


@pytest.mark.parametrize("code, station", list(CARRIER_STATIONS.items()))
def test_each_mapped_carrier_gets_its_station_whatever_the_seed(code, station):
    assert station_for(code, seed=0) == station
    assert station_for(code, seed=12345) == station


@pytest.mark.parametrize("code", ["ŁKA", "LKA", "łka", "lka", " Łka "])
def test_carrier_code_matching_ignores_case_and_diacritics(code):
    assert normalize_code(code) == "lka"
    assert station_for(code, seed=7) == CARRIER_STATIONS["ŁKA"]


@pytest.mark.parametrize("carrier", [None, "others", "OTHERS"])
def test_others_telegram_is_deterministic_and_varies_with_the_seed(carrier, caplog):
    with caplog.at_level(logging.WARNING):
        assert station_for(carrier, seed=42) == station_for(carrier, seed=42)
        assert {station_for(carrier, seed=s) for s in range(len(OTHERS_POOL))} == set(OTHERS_POOL)
    assert not caplog.records  # a mixed telegram is expected, not a problem


def test_unknown_carrier_falls_back_to_the_pool_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        station = station_for("XYZ", seed=42)
    assert station == station_for(None, seed=42)
    assert station in OTHERS_POOL
    assert "XYZ" in caplog.text
