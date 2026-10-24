from pkp_scraper.config import ApiSettings, DbSettings
from pkp_scraper.db import DBService
from pkp_scraper.runner import run_paginated
from pkp_scraper.stations import StationsScraper

DELAY_BETWEEN_REQUESTS_S = 5

if __name__ == "__main__":
    scraper = StationsScraper(ApiSettings.from_env())
    with DBService.connect(DbSettings.from_env()) as db:
        total = run_paginated(scraper, db, db.store_stations, delay_s=DELAY_BETWEEN_REQUESTS_S)
    print(f"stored {total} stations")
