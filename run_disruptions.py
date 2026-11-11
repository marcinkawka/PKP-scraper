from datetime import date, timedelta

from pkp_scraper.config import ApiSettings, DbSettings
from pkp_scraper.db import DBService
from pkp_scraper.disruptions import DisruptionsScraper

# The /disruptions endpoint only returns data for the stations in its `stations=` param
# (with no param it falls back to a single default station). We poll the busiest stations
# by planned-stop count in the last week — see DBService.busiest_stations.
TOP_N_STATIONS = 64

start_date = date.today()  - timedelta(days=31)  # date(2024, 1, 1)
end_date = date.today()  # date(2024, 1, 31)

if __name__ == "__main__":
    with DBService.connect(DbSettings.from_env()) as db:
        stations = db.busiest_stations(limit=TOP_N_STATIONS)
        if not stations:
            raise SystemExit("no stops in pkp.stop — run run_schedules.py first to populate the timetable")
        scraper = DisruptionsScraper(
            ApiSettings.from_env(), stations=stations, date_from=start_date, date_to=end_date
        )
        params = dict(scraper.params())
        try:
            result = scraper.fetch()
        except Exception as exc:
            db.log_fetch(scraper.endpoint, ok=False, note=f"{params}: {exc}"[:1000])
            raise
        unknown_stations = db.store_disruptions(result.types, result.items)
        note = f"{params}, types {len(result.types)}, skipped routes {result.skipped_routes}"
        if unknown_stations:
            note += f", skipped unknown stations {sorted(unknown_stations)}"
        db.log_fetch(
            scraper.endpoint,
            ok=True,
            generated_at=result.generated_at,
            n_items=len(result.items),
            note=note[:1000],
        )
    print(
        f"stored {len(result.types)} disruption types and {len(result.items)} disruptions "
        f"(skipped {result.skipped_routes} routes without trainOrderId"
        + (f", skipped {len(unknown_stations)} disruptions for unknown stations {sorted(unknown_stations)}" if unknown_stations else "")
        + f") for {params}"
    )
