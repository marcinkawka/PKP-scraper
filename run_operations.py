import logging
from collections import Counter
from datetime import date

from pkp_scraper.config import ApiSettings, DbSettings
from pkp_scraper.db import DBService
from pkp_scraper.models import TrainRun
from pkp_scraper.operations import OperationsScraper
from pkp_scraper.runner import run_paginated
from pkp_scraper.schedules import SchedulesScraper
from pkp_scraper.timetable import ScheduleTimetableProvider, Timetable

# None = all stations; a list of station ids limits the run (handy while testing)
# STATIONS = [82404,80630,81901,80416]
STATIONS = None
PAGE_SIZE = 10_000
DELAY_BETWEEN_REQUESTS_S = 5

log = logging.getLogger("run_operations")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = ApiSettings.from_env()
    scraper = OperationsScraper(settings, stations=STATIONS, page_size=PAGE_SIZE)
    log.info(
        "operations run: %s, page size %d, %ss between requests",
        f"stations {STATIONS}" if STATIONS else "all stations",
        PAGE_SIZE,
        DELAY_BETWEEN_REQUESTS_S,
    )
    with DBService.connect(DbSettings.from_env()) as db:

        def fetch_timetable(date_from: date, date_to: date) -> Timetable:
            """Schedules for the operations dates: stores trains, logs the fetch, returns planned times."""
            schedules = SchedulesScraper(settings, date_from=date_from, date_to=date_to, stations=STATIONS)
            params = dict(schedules.params())
            log.info("schedules: fetching planned times for %s..%s", date_from, date_to)
            try:
                result = schedules.fetch()
                db.store_trains(result.items)
            except Exception as exc:
                db.log_fetch(schedules.endpoint, ok=False, note=f"{params}: {exc}"[:1000])
                raise
            db.log_fetch(
                schedules.endpoint,
                ok=True,
                generated_at=result.generated_at,
                n_items=len(result.items),
                note=f"{params}, planned stops {len(result.timetable)} (for operations)",
            )
            log.info(
                "schedules: stored %d trains, %d planned stops for %s..%s",
                len(result.items),
                len(result.timetable),
                date_from,
                date_to,
            )
            return result.timetable

        planned = ScheduleTimetableProvider(fetch_timetable, delay_s=DELAY_BETWEEN_REQUESTS_S)
        totals: Counter[str] = Counter()
        unknown_stations: Counter[int] = Counter()  # station_id -> skipped stops, whole run

        def store(runs: list[TrainRun]) -> None:
            """Store one page; stops at stations missing from pkp.station are skipped and reported."""
            filled = planned.fill(runs)
            n_stops = sum(len(r.stops) for r in filled)
            skipped = db.store_train_runs(filled)
            n_skipped = sum(skipped.values())
            totals["stops"] += n_stops - n_skipped
            totals["skipped"] += n_skipped
            unknown_stations.update(skipped)
            log.info(
                "stored %d train runs with %d stops (operating dates %s)",
                len(filled),
                n_stops - n_skipped,
                ", ".join(f"{d}: {n}" for d, n in sorted(Counter(r.operating_date for r in filled).items()))
                or "none",
            )
            if skipped:
                log.warning(
                    "skipped %d stops at %d stations missing from pkp.station: %s",
                    n_skipped,
                    len(skipped),
                    dict(sorted(skipped.items())),
                )

        total = run_paginated(scraper, db, store, delay_s=DELAY_BETWEEN_REQUESTS_S)
    log.info("done: stored %d train runs, %d stops", total, totals["stops"])
    if unknown_stations:
        log.warning(
            "skipped %d stops at %d unknown stations (run run_stations.py to add them): %s",
            totals["skipped"],
            len(unknown_stations),
            dict(sorted(unknown_stations.items())),
        )
