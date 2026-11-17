'''Render the day's most delayed trains as a "TELEGRAM SŁUŻBOWY PKP"-style PNG.'''

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from pkp_scraper.config import DbSettings
from pkp_scraper.db import DBService
from utils import cli

# group -> label on the telegram. "others" = every carrier not listed here.
GROUPS = {
    "PR": "POLREGIO",
    "IC": "PKP INTERCITY",
    "KS": "KOLEJE ŚLĄSKIE",
    "KM": "KOLEJE MAZOWIECKIE",
    "KD": "KOLEJE DOLNOŚLĄSKIE",
    "ŁKA": "ŁÓDZKA KOLEJ AGLOMERACYJNA",
    "others": "POZOSTALI PRZEWOŹNICY",
}
NAMED_CARRIERS = [g for g in GROUPS if g != "others"]
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--group", choices=[*GROUPS, "all"], default="all",
                   help="carrier group; 'others' = carriers outside the named ones, 'all' = one PNG per group")
    p.add_argument("--date", type=date.fromisoformat, default=date.today(), help="operating date (YYYY-MM-DD), default today")
    p.add_argument("--min-delay", type=int, default=10, help="minimum arrival delay in minutes")
    p.add_argument("--limit", type=int, default=None, help="trains listed (default 20, 15 with --social)")
    p.add_argument("--out-dir", type=Path, default=OUTPUT_DIR, help="default output/, file telegram_<group>_<date>.png")
    cli.add_look_args(p)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    renderer = cli.renderer(p, args)

    with DBService.connect(DbSettings.from_env()) as db:
        for group in GROUPS if args.group == "all" else [args.group]:
            others = group == "others"
            rows = db.most_delayed_trains(
                args.date,
                NAMED_CARRIERS if others else [group],
                exclude=others,
                min_delay_min=args.min_delay,
                limit=cli.limit(args, 20, social=15),
            )
            out = cli.output(args, f"telegram_{group}_{args.date:%Y-%m-%d}")
            renderer.render(
                rows, args.date, GROUPS[group], args.min_delay, out,
                carrier=None if others else group, show_carrier=others,
            )
            print(f"{group}: {len(rows)} trains -> {out}")


if __name__ == "__main__":
    main()
