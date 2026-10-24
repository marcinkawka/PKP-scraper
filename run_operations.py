from datetime import date

from pkp_scraper.config import ApiSettings, DbSettings
from pkp_scraper.db import DBService
from pkp_scraper.operations import OperationsScraper
from pkp_scraper.runner import run_paginated
from pkp_scraper.schedules import SchedulesScraper
from pkp_scraper.timetable import ScheduleTimetableProvider, Timetable

# limited to one station while testing; None = all stations
STATIONS = [82404,80630,81901,80416]
PAGE_SIZE = 10_000
DELAY_BETWEEN_REQUESTS_S = 5

if __name__ == "__main__":
    settings = ApiSettings.from_env()
    scraper = OperationsScraper(settings, stations=STATIONS, page_size=PAGE_SIZE)
    with DBService.connect(DbSettings.from_env()) as db:

        def fetch_timetable(date_from: date, date_to: date) -> Timetable:
            """Schedules for the operations dates: stores trains, logs the fetch, returns planned times."""
            schedules = SchedulesScraper(settings, date_from=date_from, date_to=date_to, stations=STATIONS)
            params = dict(schedules.params())
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
            return result.timetable

        planned = ScheduleTimetableProvider(fetch_timetable, delay_s=DELAY_BETWEEN_REQUESTS_S)
        total = run_paginated(
            scraper,
            db,
            lambda runs: db.store_train_runs(planned.fill(runs)),
            delay_s=DELAY_BETWEEN_REQUESTS_S,
        )
    print(f"stored {total} train runs")
