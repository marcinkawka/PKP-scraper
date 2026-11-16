'''Draws a list of delayed trains as a "TELEGRAM SŁUŻBOWY"-style PNG form.'''

from __future__ import annotations

import random
import subprocess
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from pkp_scraper.models import DelayedTrain

from .aging import Aging, AgingConfig, Typewriter, seed_for
from .stations import station_for

PAPER = (233, 222, 190)
STRIP = (246, 240, 220)
STRIP_EDGE = (214, 204, 176)
STRIP_SHADOW = (205, 193, 160)
INK = (34, 30, 38)
FORM_INK = (92, 72, 50)
STAMP = (170, 30, 40)

FONT_SIZE = 26
TYPEWRITER_SIZE = 28    # TT2020 is narrower than Courier New Bold; keeps the columns as wide
TYPEWRITER_FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "TT2020Base-Regular.ttf"
MARGIN = 60
MIN_WIDTH = 1100
HEADER_H = 330
FOOTER_H = 260
LINE_H = FONT_SIZE + 20
STRIP_PAD = 14
# Header cells as shares of the form width: NR, STACJA NADANIA, DATA, GODZ., SŁÓW.
# The station cell is the wide one so that "W-WA PETERSBURSKA" fits at full type size.
FIELD_SHARES = (0.17, 0.33, 0.22, 0.14, 0.14)


TypeFn = Callable[..., None]   # type_(xy, text, max_width=None): types text at (x, y)


class FontNotFoundError(RuntimeError):
    """No Courier-like TrueType font is installed."""


@lru_cache(maxsize=None)
def courier(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Courier New via fontconfig, falling back to any monospace with Polish glyphs."""
    for pattern in ("Courier New", "Courier", "Liberation Mono", "DejaVu Sans Mono"):
        spec = f"{pattern}:bold" if bold else pattern
        try:
            path = subprocess.run(
                ["fc-match", "-f", "%{file}", spec], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            break
        if path.lower().endswith((".ttf", ".otf", ".ttc")):
            return ImageFont.truetype(path, size)
    raise FontNotFoundError(
        "No usable Courier/monospace TrueType font found (install ttf-mscorefonts-installer)"
    )


def typewriter_font(size: int) -> ImageFont.FreeTypeFont:
    """Bundled TT2020 (OFL), else an installed Courier Prime, else plain Courier."""
    if TYPEWRITER_FONT.is_file():
        return ImageFont.truetype(str(TYPEWRITER_FONT), size)
    try:
        family, _, path = subprocess.run(
            ["fc-match", "-f", "%{family}|%{file}", "Courier Prime"], capture_output=True, text=True, check=True
        ).stdout.partition("|")
        if "Courier Prime" in family:   # fc-match answers with *some* font when the family is missing
            return ImageFont.truetype(path, size)
    except (OSError, subprocess.CalledProcessError):
        pass
    return courier(size, bold=True)


class Renderer:
    """Renders one telegram per call; fonts and the sender handle are shared between calls."""

    def __init__(
        self,
        sender: str = "@OpPociagow",
        source: str = "Źródło danych: API PKP Polskie Linie Kolejowe S.A.",
        aging: AgingConfig | None = None,
    ) -> None:
        """`aging` switches on the typewritten/aged/scanned look; None renders the clean form."""
        self._sender = sender
        self._source = source
        self._aging = aging
        self._font = typewriter_font(TYPEWRITER_SIZE) if aging else courier(FONT_SIZE, bold=True)
        self._small = courier(18)
        self._label = courier(15)
        self._title = courier(54, bold=True)
        self._char_w = self._font.getlength("M")
        # typed text is positioned for Courier; keep another face on the same optical line
        ref, own = courier(FONT_SIZE, bold=True).getbbox("M"), self._font.getbbox("M")
        self._type_dy = round((ref[1] + ref[3] - own[1] - own[3]) / 2)

    def render(
        self,
        rows: list[DelayedTrain],
        day: date,
        label: str,
        min_delay_min: int,
        out: Path,
        *,
        carrier: str | None = None,
        show_carrier: bool = False,
    ) -> None:
        """Write the telegram for `rows` to `out`.

        `carrier` is the carrier code the telegram is about (None = several carriers);
        it decides the station of origin, see utils.stations.

        `label` names the carrier group on the form; `show_carrier` prefixes each
        train with its carrier code (for groups mixing several carriers).
        """
        rng = random.Random(day.toordinal())  # same day -> same jitter
        now = datetime.now()
        lines = self._body_lines(rows, label, min_delay_min, show_carrier)
        text_w = int(max(len(line) for line in lines) * self._char_w)
        width = max(text_w + 2 * (MARGIN + STRIP_PAD + 20), MIN_WIDTH)
        height = HEADER_H + len(lines) * LINE_H + FOOTER_H

        img = Image.new("RGB", (width, height), PAPER)
        d = ImageDraw.Draw(img)
        d.rectangle((24, 24, width - 24, height - 24), outline=FORM_INK, width=4)
        d.rectangle((34, 34, width - 34, height - 34), outline=FORM_INK, width=1)

        number = f"{day:%d%m}/{len(rows):03d}"
        # everything random is seeded by the telegram (number + group): same telegram, same result
        seed = seed_for(number, label)
        aging = Aging(self._aging, seed, (width, height)) if self._aging else None
        typist = Typewriter(aging, self._font) if aging else None

        def type_(xy: tuple[float, float], text: str, max_width: float | None = None) -> None:
            """Typed (not pre-printed) text; set in smaller type when wider than `max_width`."""
            font, x, y = self._font, *xy
            text_w = len(text) * self._char_w
            if max_width is not None and text_w > max_width:
                font = self._font.font_variant(size=int(self._font.size * max_width / text_w))
            if typist:
                typist.type((x, y + self._type_dy), text, None if font is self._font else font)
            else:
                if font is not self._font:
                    y += self._font.getmetrics()[0] - font.getmetrics()[0]
                d.text((x, y), text, font=font, fill=INK)

        fields = [
            ("NR TELEGRAMU", number),
            ("STACJA NADANIA", station_for(carrier, seed)),
            ("DATA", f"{day:%d.%m.%Y}"),
            ("GODZ.", f"{now:%H.%M}"),
            ("SŁÓW", str(sum(len(line.split()) for line in lines))),
        ]
        self._draw_header(d, type_, width, fields, label)
        self._draw_body(d, type_, lines, rng)
        self._draw_footer(d, type_, width, height)
        if typist:
            img = Image.composite(Image.new("RGB", img.size, INK), img, typist.ink_mask())

        stamp = self._stamp(day, now, rng, aging)
        stamp_xy = (width - MARGIN - stamp.width - 10, height - stamp.height - 40)
        if aging:
            # pressed a bit higher, over the footer rule; multiply keeps what is under it readable
            aging.multiply_paste(img, stamp, (stamp_xy[0], stamp_xy[1] - round(30 * aging.k)))
            img = aging.age_paper(img)
        else:
            img.paste(stamp, stamp_xy, stamp)
            # paper grain + slight ink bleed
            noise = Image.effect_noise((width, height), 14).convert("RGB")
            img = Image.blend(img, noise, 0.06).filter(ImageFilter.GaussianBlur(0.4))

        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)

    @staticmethod
    def _body_lines(
        rows: list[DelayedTrain], label: str, min_delay_min: int, show_carrier: bool
    ) -> list[str]:
        if not rows:
            return [f"{label} = BRAK POCIĄGÓW OPÓŹNIONYCH PONAD {min_delay_min} MIN = STOP ="]
        table = [
            (
                " ".join(filter(None, (r.carrier_code if show_carrier else None, r.category, r.number))),
                (r.name or "").upper(),
                r.station_name.upper(),
                r.planned_arr.strftime("%H.%M") if r.planned_arr else "--.--",
                f"+{r.delay_min}",
            )
            for r in rows
        ]
        head = ("POCIĄG", "NAZWA", "STACJA", "PLAN.", "OPÓŹN.")
        cols = [0, 1, 2, 3, 4] if any(r[1] for r in table) else [0, 2, 3, 4]  # drop empty NAZWA
        widths = {c: max(len(head[c]), *(len(r[c]) for r in table)) for c in cols}

        def fmt(cells: tuple) -> str:
            return "  ".join(
                f"{cells[c]:>{widths[c]}}" if c == 4 else f"{cells[c]:<{widths[c]}}" for c in cols
            )

        return [
            f"WYKAZ {len(rows)} NAJBARDZIEJ OPÓŹNIONYCH POCIĄGÓW = {label} =",
            f"LP  {fmt(head)}",
            *(f"{i:02d}. {fmt(r)} MIN =" for i, r in enumerate(table, 1)),
            "POWYŻSZE PODAJE SIĘ DO WIADOMOŚCI SŁUŻBOWEJ = STOP =",
        ]

    def _draw_header(
        self, d: ImageDraw.ImageDraw, type_: TypeFn, width: int, fields: list[tuple[str, str]], label: str
    ) -> None:
        d.text((width - MARGIN, 56), "Druk R-48", font=self._small, fill=FORM_INK, anchor="ra")
        d.text((width / 2, 130), "TELEGRAM SŁUŻBOWY", font=self._title, fill=FORM_INK, anchor="mm")
        d.line((MARGIN, 172, width - MARGIN, 172), fill=FORM_INK, width=3)

        # form fields: printed label + typed value
        top, bottom = 184, 250
        d.rectangle((MARGIN, top, width - MARGIN, bottom), outline=FORM_INK, width=2)
        x = float(MARGIN)
        for i, ((name, value), share) in enumerate(zip(fields, FIELD_SHARES, strict=True)):
            cell_w = share * (width - 2 * MARGIN)
            if i:
                d.line((x, top, x, bottom), fill=FORM_INK, width=2)
            d.text((x + 8, top + 5), name, font=self._label, fill=FORM_INK)
            type_((x + 10, top + 28), value, max_width=cell_w - 20)     # never wraps, never overflows
            x += cell_w
        d.text((MARGIN, 268), "DO:", font=self._label, fill=FORM_INK)
        type_((MARGIN + 40, 262), "WSZYSTKIE STACJE I DYŻURNI RUCHU")
        d.text((MARGIN, 300), "OD:", font=self._label, fill=FORM_INK)
        type_((MARGIN + 40, 294), f"TELEGRAF NIEOFICJALNY = DOT. OPÓŹNIEŃ = {label}")

    def _draw_body(self, d: ImageDraw.ImageDraw, type_: TypeFn, lines: list[str], rng: random.Random) -> None:
        """Teleprinter tape strips glued onto the form, one per line."""
        y = HEADER_H + 10
        for line in lines:
            x0 = MARGIN + 10 + rng.randint(-10, 10)
            x1 = x0 + int(len(line) * self._char_w) + 2 * STRIP_PAD
            d.rectangle((x0 + 3, y + 4, x1 + 3, y + LINE_H - 4), fill=STRIP_SHADOW)
            d.rectangle((x0, y + 1, x1, y + LINE_H - 7), fill=STRIP, outline=STRIP_EDGE)
            type_((x0 + STRIP_PAD, y + 6), line)
            y += LINE_H

    def _draw_footer(self, d: ImageDraw.ImageDraw, type_: TypeFn, width: int, height: int) -> None:
        fy = height - FOOTER_H + 40
        d.line((MARGIN, fy, width - MARGIN, fy), fill=FORM_INK, width=2)
        d.text((MARGIN, fy + 14), "NADAŁ:", font=self._label, fill=FORM_INK)
        type_((MARGIN + 70, fy + 8), self._sender)
        type_((MARGIN, fy + 50), "ZAINTERESOWANYCH POWIADOMIĆ!")
        d.text((MARGIN, fy + 100), "PRZYJĄŁ: ....................  PODPIS: ....................",
               font=self._small, fill=FORM_INK)
        d.text((MARGIN, height - 72), self._source, font=self._small, fill=FORM_INK)

    def _stamp(self, day: date, now: datetime, rng: random.Random, aging: Aging | None) -> Image.Image:
        """Round date stamp, slightly rotated; worn and with a loose date insert when aged."""
        worn = aging is not None and aging.cfg.stamp_wear
        stamp = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
        d = ImageDraw.Draw(stamp)
        ring, ink = STAMP + (200,), STAMP + (210,)
        d.ellipse((6, 6, 214, 214), outline=ring, width=5)
        d.ellipse((22, 22, 198, 198), outline=ring, width=2)
        if worn:    # the date is a separate insert in the stamp: never quite square to the ring
            r = aging.rng("stamp-date")
            insert = Image.new("RGBA", stamp.size, (0, 0, 0, 0))
            ImageDraw.Draw(insert).text((110, 66), f"{day:%d.%m.%Y}", font=courier(20, bold=True), fill=ink, anchor="mm")
            shift = (r.uniform(-2, 2) * aging.k, r.uniform(-1, 2) * aging.k)
            insert = insert.rotate(r.uniform(-4, 4) * aging.k, resample=Image.BICUBIC, center=(110, 66), translate=shift)
            stamp.alpha_composite(insert)
        for y, text, size in (
            *(() if worn else ((64, f"{day:%d.%m.%Y}", 20),)),
            (102, self._sender, 21),
            (138, f"GODZ. {now:%H.%M}", 20),
            (172, "TELEGRAF", 16),
        ):
            d.text((110, y), text, font=courier(size, bold=True), fill=ink, anchor="mm")
        stamp = stamp.resize((190, 190), Image.LANCZOS)
        if aging:
            stamp = aging.distress_stamp(stamp)
            return stamp.rotate(aging.rng("stamp-angle").uniform(-20, -8), resample=Image.BICUBIC, expand=True)
        return stamp.rotate(rng.uniform(-18, -8), resample=Image.BICUBIC, expand=True)
