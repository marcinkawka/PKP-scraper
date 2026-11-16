'''Render the day's most delayed trains as a "TELEGRAM SŁUŻBOWY PKP"-style PNG.'''

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from pkp_scraper.config import DbSettings
from pkp_scraper.db import DBService
from utils.aging import AgingConfig
from utils.renderer import Renderer

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
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out-dir", type=Path, default=OUTPUT_DIR, help="default output/, file telegram_<group>_<date>.png")
    p.add_argument("--clean", action="store_true", help="plain form, without the typewritten/aged/scanned look")
    p.add_argument("--intensity", type=float, default=0.6, help="strength of the aged look, 0..1 (default 0.6)")
    p.add_argument("--disable", nargs="+", default=[], choices=AgingConfig.effects(), metavar="EFFECT",
                   help="aged-look effects to switch off: " + ", ".join(AgingConfig.effects()))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not 0 <= args.intensity <= 1:
        p.error("--intensity must be within 0..1")
    aging = AgingConfig(args.intensity, **{name: False for name in args.disable}) if not args.clean else None

    renderer = Renderer(aging=aging)
    with DBService.connect(DbSettings.from_env()) as db:
        for group in GROUPS if args.group == "all" else [args.group]:
            others = group == "others"
            rows = db.most_delayed_trains(
                args.date,
                NAMED_CARRIERS if others else [group],
                exclude=others,
                min_delay_min=args.min_delay,
                limit=args.limit,
            )
            out = args.out_dir / f"telegram_{group}_{args.date:%Y-%m-%d}.png"
            renderer.render(
                rows, args.date, GROUPS[group], args.min_delay, out,
                carrier=None if others else group, show_carrier=others,
            )
            print(f"{group}: {len(rows)} trains -> {out}")


if __name__ == "__main__":
    main()
