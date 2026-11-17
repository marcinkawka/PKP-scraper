'''Render the day's most unlucky station (largest share of disrupted trains) as a "TELEGRAM SŁUŻBOWY PKP"-style PNG.'''

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from pkp_scraper.config import DbSettings
from pkp_scraper.db import DBService
from utils import cli

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
# LIKE patterns of messages left out: replacement-bus / cancelled-section notices
# swamp every big junction and say little about bad luck
EXCLUDED_MESSAGES = ["Na odcinku od%"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", type=date.fromisoformat, default=date.today(), help="operating date (YYYY-MM-DD), default today")
    p.add_argument("--min-trains", type=int, default=50,
                   help="skip stations with fewer trains that day (default 50)")
    p.add_argument("--limit", type=int, default=None,
                   help="kinds of disruption listed, most frequent first (default 8, 4 with --social)")
    p.add_argument("--out-dir", type=Path, default=OUTPUT_DIR, help="default output/, file telegram_unlucky_station_<date>.png")
    cli.add_look_args(p)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    renderer = cli.renderer(p, args)

    with DBService.connect(DbSettings.from_env()) as db:
        station = db.unlucky_station(args.date, EXCLUDED_MESSAGES, min_trains=args.min_trains)
    out = cli.output(args, f"telegram_unlucky_station_{args.date:%Y-%m-%d}")
    renderer.render_unlucky_station(station, args.date, out, limit=cli.limit(args, 8, social=4))
    if station:
        print(f"unlucky station: {station.station_name}, {station.disrupted_trains}/{station.trains} trains,"
              f" {len(station.disruptions)} disruptions -> {out}")
    else:
        print(f"unlucky station: none -> {out}")


if __name__ == "__main__":
    main()
