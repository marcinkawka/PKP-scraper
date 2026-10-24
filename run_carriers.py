from pkp_scraper.carriers import CarriersScraper
from pkp_scraper.config import ApiSettings, DbSettings
from pkp_scraper.db import DBService

if __name__ == "__main__":
    carriers = CarriersScraper(ApiSettings.from_env()).scrape()
    print(f"fetched {len(carriers)} carriers")
    with DBService.connect(DbSettings.from_env()) as db:
        db.store_carriers(carriers)
    print(f"stored {len(carriers)} carriers")
