'''Command-line options shared by the run_*_telegram.py scripts: the look and --social.'''

from __future__ import annotations

import argparse
from pathlib import Path

from .aging import AgingConfig
from .renderer import Renderer

INTENSITY = 0.6
SOCIAL_INTENSITY = 0.3      # blur and grain cost most legibility at phone size


def add_look_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--social", action="store_true",
                   help="for posting (X/Twitter on a phone): narrow lines, fewer rows, lighter aging, JPEG")
    p.add_argument("--clean", action="store_true", help="plain form, without the typewritten/aged/scanned look")
    p.add_argument("--intensity", type=float, default=None,
                   help=f"strength of the aged look, 0..1 (default {INTENSITY}, {SOCIAL_INTENSITY} with --social)")
    p.add_argument("--disable", nargs="+", default=[], choices=AgingConfig.effects(), metavar="EFFECT",
                   help="aged-look effects to switch off: " + ", ".join(AgingConfig.effects()))


def renderer(p: argparse.ArgumentParser, args: argparse.Namespace) -> Renderer:
    intensity = args.intensity if args.intensity is not None else SOCIAL_INTENSITY if args.social else INTENSITY
    if not 0 <= intensity <= 1:
        p.error("--intensity must be within 0..1")
    aging = None if args.clean else AgingConfig(intensity, **{name: False for name in args.disable})
    return Renderer(aging=aging, social=args.social)


def limit(args: argparse.Namespace, default: int, social: int) -> int:
    """--limit as given, else the default for the chosen layout."""
    return args.limit if args.limit is not None else social if args.social else default


def output(args: argparse.Namespace, stem: str) -> Path:
    """<out-dir>/<stem>.png, or <stem>_social.jpg with --social."""
    return args.out_dir / (f"{stem}_social.jpg" if args.social else f"{stem}.png")
