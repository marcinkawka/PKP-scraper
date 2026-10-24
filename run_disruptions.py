from datetime import date, timedelta

from pkp_scraper.config import ApiSettings, DbSettings
from pkp_scraper.db import DBService
from pkp_scraper.disruptions import DisruptionsScraper

# limited to one station while testing; None = all stations
STATIONS = [82404,80630,81901,80416]

start_date = date.today()  - timedelta(days=31)  # date(2024, 1, 1)
end_date = date.today()  # date(2024, 1, 31)

if __name__ == "__main__":
    scraper = DisruptionsScraper(ApiSettings.from_env(), stations=STATIONS, date_from=start_date, date_to=end_date)
    params = dict(scraper.params())
    with DBService.connect(DbSettings.from_env()) as db:
        try:
            result = scraper.fetch()
        except Exception as exc:
            db.log_fetch(scraper.endpoint, ok=False, note=f"{params}: {exc}"[:1000])
            raise
        db.store_disruptions(result.types, result.items)
        db.log_fetch(
            scraper.endpoint,
            ok=True,
            generated_at=result.generated_at,
            n_items=len(result.items),
            note=f"{params}, types {len(result.types)}, skipped routes {result.skipped_routes}",
        )
    print(
        f"stored {len(result.types)} disruption types and {len(result.items)} disruptions "
        f"(skipped {result.skipped_routes} routes without trainOrderId) for {params}"
    )
