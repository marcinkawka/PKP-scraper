from datetime import datetime

from pkp_scraper.models import StationDisruptions, TrainDisruption
from utils.renderer import MESSAGE_WIDTH, Renderer

SEEN = datetime(2026, 11, 17, 14, 30)


def disruption(station: str, message: str) -> TrainDisruption:
    return TrainDisruption("IC", "IC", "6148", "Oleńka", station, message, SEEN)


def test_label_names_the_train():
    assert Renderer._train_label(disruption("X", "m")) == "IC 6148 OLEŃKA"


def test_long_messages_wrap_onto_lines_aligned_under_the_message_column():
    rows = [disruption("Opole Główne", "Awaria taboru"), disruption("Skierniewice", "słowo " * 40)]
    lines = Renderer._unlucky_lines(rows, "IC 6148 OLEŃKA")

    assert lines[0] == "NAJBARDZIEJ PECHOWY POCIĄG DNIA = IC 6148 OLEŃKA ="
    assert lines[3].startswith("01. 14.30  OPOLE GŁÓWNE  AWARIA TABORU =")
    column = lines[3].index("AWARIA")
    continuation = lines[5:-1]
    assert continuation and all(line[:column].isspace() and line[column] != " " for line in continuation)
    assert all(len(line) <= column + MESSAGE_WIDTH for line in lines[3:-1])
    assert continuation[-1].endswith("=")


def test_template_messages_get_the_train_number_and_no_extra_spaces():
    lines = Renderer._unlucky_lines([disruption("X", "Pociąg nr {numer}  kursuje")], "L")
    assert lines[3].endswith("POCIĄG NR 6148 KURSUJE =")


def test_no_disruptions_gives_a_one_line_telegram():
    assert Renderer._unlucky_lines([], "PECHOWY POCIĄG DNIA") == [
        "PECHOWY POCIĄG DNIA = BRAK UTRUDNIEŃ W RUCHU POCIĄGÓW = STOP ="
    ]


def at_station(category: str, number: str, message: str) -> TrainDisruption:
    return TrainDisruption(None, category, number, None, "Katowice", message, SEEN)


def station(*rows: TrainDisruption, trains: int = 40) -> StationDisruptions:
    return StationDisruptions("Katowice", trains, len({r.number for r in rows}), rows)


def test_station_telegram_counts_per_message_then_lists_trains_and_cuts_at_limit():
    rows = [
        at_station("Os", "94400", "Rzadkie"),
        *(at_station("K22/K22 BUS/R", str(42900 + i), "Częste  {stacja}") for i in range(3)),
        at_station("IC", "8314", "Inne"),
    ]
    lines = Renderer._unlucky_station_lines(station(*rows), "KATOWICE", limit=2)

    assert lines == [
        "NAJBARDZIEJ PECHOWA STACJA DNIA = KATOWICE =",
        "UTRUDNIENIA DOTKNĘŁY 5 Z 40 POCIĄGÓW (12%) =",
        "ZGŁOSZONO 5 UTRUDNIEŃ =",
        "LP  UTRUDNIENIE  ILOŚĆ",
        "01. CZĘSTE ...       3 =",
        "02. RZADKIE          1 =",     # equal counts: first seen first
        "ORAZ 1 INNYCH UTRUDNIEŃ (1 RODZAJÓW) =",
        "WYKAZ POCIĄGÓW =",
        "01. K22 42900, K22 42901, K22 42902 =",
        "02. OS 94400 =",
        "POWYŻSZE PODAJE SIĘ DO WIADOMOŚCI SŁUŻBOWEJ = STOP =",
    ]


def test_station_summary_wraps_long_messages_and_keeps_counts_aligned():
    lines = Renderer._unlucky_station_lines(station(at_station("Os", "1", "słowo " * 20)), "X", 8)
    summary = lines[4:lines.index("WYKAZ POCIĄGÓW =")]
    assert len(summary) > 1 and summary[0].startswith("01. ") and summary[1].startswith("    ")
    assert summary[-1].endswith("    1 =") and len(summary[-1]) == len(lines[3]) + 2


def test_station_train_list_never_splits_a_train_over_two_lines():
    rows = [at_station("Os", str(94000 + i), "M") for i in range(30)]
    lines = Renderer._unlucky_station_lines(station(*rows), "X", 8)
    trains = [l.strip() for l in lines[lines.index("WYKAZ POCIĄGÓW =") + 1:-1]]
    assert len(trains) > 1
    for line in trains:
        assert not line.rstrip(" =,").endswith("OS")


def test_station_telegram_without_disruptions():
    assert Renderer._unlucky_station_lines(None, "PECHOWA STACJA DNIA", 8) == [
        "PECHOWA STACJA DNIA = BRAK UTRUDNIEŃ W RUCHU POCIĄGÓW = STOP ="
    ]


def test_social_fit_keeps_closing_equals_sign_with_its_word_and_shortens_closing():
    from utils.renderer import CLOSING, SHORT_CLOSING, fit

    assert fit(["NAJBARDZIEJ PECHOWA STACJA DNIA = SKIERNIEWICE =", CLOSING], 46) == [
        "NAJBARDZIEJ PECHOWA STACJA DNIA =",
        "    SKIERNIEWICE =",
        SHORT_CLOSING,
    ]
    assert fit([CLOSING], None) == [CLOSING]


def test_social_layouts_stay_within_the_line_width():
    from utils.renderer import SOCIAL_CHARS, fit
    from pkp_scraper.models import StationTrains

    long = "Bardzo długi komunikat o utrudnieniu " * 4
    bodies = [
        Renderer._unlucky_lines([disruption("Warszawa Wschodnia", long)] * 3, "IC 6148 OLEŃKA", 2, width=SOCIAL_CHARS),
        Renderer._unlucky_station_lines(station(*[at_station("Os", str(i), long) for i in range(30)]),
                                        "WARSZAWA WSCHODNIA", 4, width=SOCIAL_CHARS),
        Renderer._by_station_lines([StationTrains("Warszawa Wschodnia", 20, tuple(f"R2 {i}" for i in range(20)))] * 9,
                                   "INTERWENCJE SŁUŻB PORZĄDKOWYCH", 6, width=SOCIAL_CHARS),
    ]
    for lines in bodies:
        assert all(len(line) <= SOCIAL_CHARS for line in fit(lines, SOCIAL_CHARS))
    assert "ORAZ 60 ZGŁOSZEŃ NA 3 INNYCH STACJACH =" in bodies[2]
    assert "ORAZ 1 INNYCH UTRUDNIEŃ =" in bodies[0]
