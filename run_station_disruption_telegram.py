'''Render one kind of the day's disruption reports, by station, as a "TELEGRAM SŁUŻBOWY PKP"-style PNG.'''

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from pkp_scraper.config import DbSettings
from pkp_scraper.db import DBService
from utils import cli

# kind -> (message, exact text in pkp.disruption_type; title on the telegram)
KINDS = {
    "interventions": ("Interwencja służb porządkowych", "INTERWENCJE SŁUŻB PORZĄDKOWYCH"),
    "weather": ("Trudne warunki atmosferyczne", "TRUDNE WARUNKI ATMOSFERYCZNE"),
    "medical": ("Interwencja służb medycznych", "INTERWENCJE SŁUŻB MEDYCZNYCH"),
    "animals": ("Kolizja ze zwierzętami", "KOLIZJE ZE ZWIERZĘTAMI"),
}
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kind", choices=[*KINDS, "all"], default="all", help="kind of disruption; 'all' = one PNG per kind")
    p.add_argument("--date", type=date.fromisoformat, default=date.today(), help="operating date (YYYY-MM-DD), default today")
    p.add_argument("--limit", type=int, default=None,
                   help="stations listed, most reports first (default all, 6 with --social)")
    p.add_argument("--out-dir", type=Path, default=OUTPUT_DIR, help="default output/, file telegram_<kind>_<date>.png")
    cli.add_look_args(p)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    renderer = cli.renderer(p, args)

    limit = args.limit if args.limit is not None else 6 if args.social else None
    with DBService.connect(DbSettings.from_env()) as db:
        for kind in KINDS if args.kind == "all" else [args.kind]:
            message, title = KINDS[kind]
            rows = db.disruption_by_station(args.date, message)
            out = cli.output(args, f"telegram_{kind}_{args.date:%Y-%m-%d}")
            renderer.render_by_station(rows, args.date, title, out, limit=limit)
            print(f"{kind}: {sum(r.count for r in rows)} reports at {len(rows)} stations -> {out}")


if __name__ == "__main__":
    main()
