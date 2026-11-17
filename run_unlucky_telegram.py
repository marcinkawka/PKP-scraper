'''Render the day's most unlucky (most disrupted) train as a "TELEGRAM SŁUŻBOWY PKP"-style PNG.'''

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from pkp_scraper.config import DbSettings
from pkp_scraper.db import DBService
from utils import cli

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", type=date.fromisoformat, default=date.today(), help="operating date (YYYY-MM-DD), default today")
    p.add_argument("--limit", type=int, default=None, help="disruptions listed (default all, 8 with --social)")
    p.add_argument("--out-dir", type=Path, default=OUTPUT_DIR, help="default output/, file telegram_unlucky_<date>.png")
    cli.add_look_args(p)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    renderer = cli.renderer(p, args)

    with DBService.connect(DbSettings.from_env()) as db:
        rows = db.unlucky_train(args.date)
    out = cli.output(args, f"telegram_unlucky_{args.date:%Y-%m-%d}")
    renderer.render_unlucky(rows, args.date, out, limit=args.limit if args.limit is not None else 8 if args.social else None)
    train = " ".join(filter(None, (rows[0].category, rows[0].number))) if rows else "none"
    print(f"unlucky: {train}, {len(rows)} disruptions -> {out}")


if __name__ == "__main__":
    main()
