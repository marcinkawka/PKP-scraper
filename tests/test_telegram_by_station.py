from pkp_scraper.models import StationTrains
from utils.renderer import MESSAGE_WIDTH, Renderer

TITLE = "INTERWENCJE SŁUŻB PORZĄDKOWYCH"


def test_lists_stations_with_counts_and_trains():
    rows = [StationTrains("Kutno", 2, ("Kociewie", "Ł 97858")), StationTrains("Łódź Widzew", 1, ("R 14207",))]
    assert Renderer._by_station_lines(rows, TITLE) == [
        f"{TITLE} = ZGŁOSZONO 3 RAZY NA 2 STACJACH =",
        "LP  STACJA       ILOŚĆ  POCIĄGI",
        "01. KUTNO            2  KOCIEWIE, Ł 97858 =",
        "02. ŁÓDŹ WIDZEW      1  R 14207 =",
        "POWYŻSZE PODAJE SIĘ DO WIADOMOŚCI SŁUŻBOWEJ = STOP =",
    ]


def test_long_train_lists_wrap_under_the_trains_column_without_splitting_a_train():
    trains = tuple(f"R2 {93800 + i}" for i in range(20))
    lines = Renderer._by_station_lines([StationTrains("Warszawa Wschodnia", 20, trains)], TITLE)
    column = lines[1].index("POCIĄGI")
    body = lines[2:-1]
    assert len(body) > 1
    for line in body[1:]:
        assert line[:column].isspace() and line[column] != " "
    for line in body:
        assert len(line) <= column + MESSAGE_WIDTH
        assert not line.rstrip(" =,").endswith("R2")
    assert ", ".join(l[column:] for l in body).replace(" =", "").replace(",,", ",").count("R2") == 20


def test_no_reports():
    assert Renderer._by_station_lines([], TITLE) == [f"{TITLE} = BRAK ZGŁOSZEŃ = STOP ="]


def test_social_delay_table_drops_columns_and_shortens_long_station_names():
    from datetime import datetime

    from pkp_scraper.models import DelayedTrain
    from utils.renderer import SOCIAL_CHARS

    rows = [DelayedTrain("IC", "EC/EIC", "17000", "Berlin-Warszawa-Express", "Warszawa Wschodnia Osobowa Peron 9",
                         datetime(2026, 11, 17, 9, 9), 148)]
    lines = Renderer._body_lines(rows, "PKP INTERCITY", 10, False, width=SOCIAL_CHARS)
    row = lines[2]
    assert row.startswith("01. EC 17000  WARSZAWA") and row.endswith("+148 MIN =")
    assert "." in row.split("  ")[1] and len(row) <= SOCIAL_CHARS
    assert "BERLIN" not in row and "09.09" not in row
