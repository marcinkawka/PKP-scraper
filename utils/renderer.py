'''Draws a list of delayed trains as a "TELEGRAM SŁUŻBOWY"-style PNG form.'''

from __future__ import annotations

import logging
import random
import re
import subprocess
import textwrap
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from pkp_scraper.models import DelayedTrain, StationDisruptions, StationTrains, TrainDisruption

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
MESSAGE_WIDTH = 60      # disruption messages run up to ~400 chars: wrapped onto several strips
# --social: fixed, narrow lines so that the text stays ~11 pt on a phone, and an image
# no taller than a phone screen, else the full-screen view shrinks it again
SOCIAL_CHARS = 46
SOCIAL_MAX_RATIO = 1.8
NBSP = "\xa0"          # keeps a train name ("OS 94819") on one strip when wrapping
PLACEHOLDER = re.compile(r"\{\w+\}")   # dictionary messages are partly templates: "pociągu nr {numer}"


TypeFn = Callable[..., None]   # type_(xy, text, max_width=None): types text at (x, y)

log = logging.getLogger(__name__)


def wrap(text: str, width: int) -> list[str]:
    """textwrap.wrap that never breaks inside a word joined by NBSP; NBSP becomes a space."""
    return [part.replace(NBSP, " ") for part in textwrap.wrap(text, width)] or [""]


def keep_together(name: str) -> str:
    return name.replace(" ", NBSP)


CLOSING = "POWYŻSZE PODAJE SIĘ DO WIADOMOŚCI SŁUŻBOWEJ = STOP ="
SHORT_CLOSING = "DO WIADOMOŚCI SŁUŻBOWEJ = STOP ="


def fit(lines: list[str], width: int | None) -> list[str]:
    """Wrap lines longer than `width` characters, continuation indented; None = as they are.

    A closing "=" stays with the word before it; the closing line gets its short form.
    """
    if width is None:
        return lines
    out = []
    for line in lines:
        if line == CLOSING:
            line = SHORT_CLOSING
        if len(line) <= width:
            out.append(line)
            continue
        if line.endswith(" ="):
            line = line[:-2] + NBSP + "="
        first, *rest = textwrap.wrap(line, width)
        out.append(first.replace(NBSP, " "))
        out.extend("    " + part for part in wrap(" ".join(rest), width - 4))
    return out


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
        social: bool = False,
    ) -> None:
        """`aging` switches on the typewritten/aged/scanned look; None renders the clean form.

        `social` lays telegrams out for a phone screen: SOCIAL_CHARS-wide lines, a fixed
        image width, rows rearranged so that no table is wider than that.
        """
        self._width = SOCIAL_CHARS if social else None
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
        lines = self._body_lines(rows, label, min_delay_min, show_carrier, width=self._width)
        self._render(lines, len(rows), day, label, "OPÓŹNIEŃ", out, carrier)

    def render_unlucky(
        self, rows: list[TrainDisruption], day: date, out: Path, limit: int | None = None
    ) -> None:
        """Write the telegram about the day's most disrupted train to `out`.

        `rows` are that one train's disruptions, see DBService.unlucky_train;
        empty rows give a "nothing to report" telegram. Only the first `limit` are listed.
        """
        label = self._train_label(rows[0]) if rows else "PECHOWY POCIĄG DNIA"
        lines = self._unlucky_lines(rows, label, limit, width=self._width)
        self._render(lines, len(rows), day, label, "UTRUDNIEŃ", out, rows[0].carrier_code if rows else None)

    def render_unlucky_station(
        self, station: StationDisruptions | None, day: date, out: Path, limit: int = 8
    ) -> None:
        """Write the telegram about the day's most unlucky station to `out`.

        `station` comes from DBService.unlucky_station; None gives a "nothing to
        report" telegram. Disruptions are grouped by message; the `limit` most
        frequent messages are listed.
        """
        label = station.station_name.upper() if station else "PECHOWA STACJA DNIA"
        lines = self._unlucky_station_lines(station, label, limit, width=self._width)
        self._render(lines, len(station.disruptions) if station else 0, day, label, "UTRUDNIEŃ", out, None)

    def render_by_station(
        self, rows: list[StationTrains], day: date, title: str, out: Path, limit: int | None = None
    ) -> None:
        """Write a telegram listing the stations (with trains) of one kind of disruption.

        `rows` come from DBService.disruption_by_station; `title` names the disruption
        on the form, e.g. "INTERWENCJE SŁUŻB PORZĄDKOWYCH". With `limit`, only the
        stations with most reports are listed.
        """
        lines = self._by_station_lines(rows, title, limit, width=self._width)
        self._render(lines, sum(r.count for r in rows), day, title, "UTRUDNIEŃ", out, None)

    def _render(
        self,
        lines: list[str],
        count: int,
        day: date,
        label: str,
        subject: str,
        out: Path,
        carrier: str | None,
    ) -> None:
        """Draw the form around body `lines`; `count` goes into the telegram number,
        `subject` into the "OD:" line ("DOT. <subject>")."""
        rng = random.Random(day.toordinal())  # same day -> same jitter
        now = datetime.now()
        lines = fit(lines, self._width)
        chars = self._width or max(len(line) for line in lines)
        width = int(chars * self._char_w) + 2 * (MARGIN + STRIP_PAD + 20)
        if self._width is None:
            width = max(width, MIN_WIDTH)
        height = HEADER_H + len(lines) * LINE_H + FOOTER_H
        if self._width and height > SOCIAL_MAX_RATIO * width:
            log.warning("%s: %d lines make the image %.1f times taller than wide; "
                        "it will look small on a phone, lower the limit", out.name, len(lines), height / width)

        img = Image.new("RGB", (width, height), PAPER)
        d = ImageDraw.Draw(img)
        d.rectangle((24, 24, width - 24, height - 24), outline=FORM_INK, width=4)
        d.rectangle((34, 34, width - 34, height - 34), outline=FORM_INK, width=1)

        number = f"{day:%d%m}/{count:03d}"
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
        self._draw_header(d, type_, width, fields, label, subject)
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
        if out.suffix.lower() in (".jpg", ".jpeg"):
            # no chroma subsampling: keeps thin type and the red stamp crisp
            img.save(out, quality=90, subsampling=0, optimize=True)
        else:
            img.save(out)

    @staticmethod
    def _body_lines(
        rows: list[DelayedTrain], label: str, min_delay_min: int, show_carrier: bool, *, width: int | None = None
    ) -> list[str]:
        """With `width`, only POCIĄG, STACJA and OPÓŹN.; station names cut to fit."""
        if not rows:
            return [f"{label} = BRAK POCIĄGÓW OPÓŹNIONYCH PONAD {min_delay_min} MIN = STOP ="]
        category = (lambda c: (c or "").split("/")[0]) if width else (lambda c: c)
        table = [
            (
                " ".join(filter(None, (r.carrier_code if show_carrier else None, category(r.category), r.number))),
                (r.name or "").upper(),
                r.station_name.upper(),
                r.planned_arr.strftime("%H.%M") if r.planned_arr else "--.--",
                f"+{r.delay_min}",
            )
            for r in rows
        ]
        head = ("POCIĄG", "NAZWA", "STACJA", "PLAN.", "OPÓŹN.")
        if width:
            cols = [0, 2, 4]
        else:
            cols = [0, 1, 2, 3, 4] if any(r[1] for r in table) else [0, 2, 3, 4]  # drop empty NAZWA
        widths = {c: max(len(head[c]), *(len(r[c]) for r in table)) for c in cols}
        if width:   # "01. " + columns + " MIN =" within width: shorten STACJA
            others = len("01. ") + widths[0] + widths[4] + 2 * len("  ") + len(" MIN =")
            widths[2] = max(len(head[2]), min(widths[2], width - others))
            table = [(*r[:2], r[2] if len(r[2]) <= widths[2] else r[2][:widths[2] - 1] + ".", *r[3:])
                     for r in table]

        def fmt(cells: tuple) -> str:
            return "  ".join(
                f"{cells[c]:>{widths[c]}}" if c == 4 else f"{cells[c]:<{widths[c]}}" for c in cols
            )

        return [
            f"WYKAZ {len(rows)} NAJBARDZIEJ OPÓŹNIONYCH POCIĄGÓW = {label} =",
            f"LP  {fmt(head)}",
            *(f"{i:02d}. {fmt(r)} MIN =" for i, r in enumerate(table, 1)),
            CLOSING,
        ]

    @staticmethod
    def _train_label(r: TrainDisruption) -> str:
        """'IC 6148 OLEŃKA'"""
        return " ".join(filter(None, (r.category, r.number, (r.train_name or "").upper())))

    @staticmethod
    def _unlucky_lines(
        rows: list[TrainDisruption], label: str, limit: int | None = None, *, width: int | None = None
    ) -> list[str]:
        """With `width`, each message goes under its time and station instead of beside them."""
        if not rows:
            return [f"{label} = BRAK UTRUDNIEŃ W RUCHU POCIĄGÓW = STOP ="]
        shown, rest = rows[:limit], rows[limit:] if limit is not None else []
        stations = [r.station_name.upper() for r in shown]
        station_w = max(len("STACJA"), *(len(s) for s in stations))
        lines = [
            f"NAJBARDZIEJ PECHOWY POCIĄG DNIA = {label} =",
            f"ZGŁOSZONO {len(rows)} UTRUDNIEŃ W RUCHU =",
        ]
        if width:
            lines.append("LP  GODZ.  STACJA / UTRUDNIENIE")
            indent = " " * len("01. ")
        else:
            lines.append(f"LP  GODZ.  {'STACJA':<{station_w}}  UTRUDNIENIE")
            indent = " " * (len("01. ") + len("GODZ.  ") + station_w + 2)
        for i, (r, station) in enumerate(zip(shown, stations), 1):
            message = Renderer._message(r.message, r.number)
            lead = f"{i:02d}. {r.first_seen_at:%H.%M}  {station}"
            if width:
                lines.append(lead)
                lines.extend(indent + part for part in wrap(f"{message} =", width - len(indent)))
            else:
                wrapped = wrap(f"{message} =", MESSAGE_WIDTH)
                lines.append(f"{i:02d}. {r.first_seen_at:%H.%M}  {station:<{station_w}}  {wrapped[0]}")
                lines.extend(indent + part for part in wrapped[1:])
        if rest:
            lines.append(f"ORAZ {len(rest)} INNYCH UTRUDNIEŃ =")
        lines.append(CLOSING)
        return lines

    @staticmethod
    def _message(message: str, number: str | None = None) -> str:
        """Message for the tape: upper case, single spaces, template fields filled in or elided."""
        if number:
            message = message.replace("{numer}", number)
        return " ".join(PLACEHOLDER.sub("...", message).split()).upper()

    @staticmethod
    def _short_train(r: TrainDisruption) -> str:
        """'IC 8314'; of a 'K22/K22 BUS/R' category only the first part."""
        return " ".join(filter(None, ((r.category or "").split("/")[0], r.number)))

    @staticmethod
    def _unlucky_station_lines(
        station: StationDisruptions | None, label: str, limit: int, *, width: int | None = None
    ) -> list[str]:
        """Share of disrupted trains, summary (message + count, most frequent first),
        then the trains behind each message."""
        if not station:
            return [f"{label} = BRAK UTRUDNIEŃ W RUCHU POCIĄGÓW = STOP ="]
        rows = station.disruptions
        groups: dict[str, list[TrainDisruption]] = {}
        for r in rows:  # rows are oldest first: equal counts keep that order
            groups.setdefault(Renderer._message(r.message), []).append(r)
        ranked = sorted(groups.items(), key=lambda g: -len(g[1]))
        shown, rest = ranked[:limit], ranked[limit:]
        count_w = len("ILOŚĆ")
        indent = " " * len("01. ")
        wrap_w = min(MESSAGE_WIDTH, width - len(indent)) if width else MESSAGE_WIDTH
        max_message_w = wrap_w - len(f"  {'ILOŚĆ'} =") if width else MESSAGE_WIDTH
        message_w = min(max_message_w, max(len("UTRUDNIENIE"), *(len(m) for m, _ in shown)))

        lines = [
            f"NAJBARDZIEJ PECHOWA STACJA DNIA = {label} =",
            f"UTRUDNIENIA DOTKNĘŁY {station.disrupted_trains} Z {station.trains} POCIĄGÓW"
            f" ({100 * station.disrupted_trains / station.trains:.0f}%) =",
            f"ZGŁOSZONO {len(rows)} UTRUDNIEŃ =",
            f"LP  {'UTRUDNIENIE':<{message_w}}  {'ILOŚĆ':>{count_w}}",
        ]
        for i, (message, group) in enumerate(shown, 1):
            wrapped = wrap(message, message_w)
            lines.extend(indent + part for part in wrapped[:-1])
            lead = f"{i:02d}. " if len(wrapped) == 1 else indent
            lines.append(f"{lead}{wrapped[-1]:<{message_w}}  {len(group):>{count_w}} =")
            if len(wrapped) > 1:   # number the first line of a wrapped message
                lines[-len(wrapped)] = f"{i:02d}. " + lines[-len(wrapped)][len(indent):]
        if rest:
            lines.append(f"ORAZ {sum(len(g) for _, g in rest)} INNYCH UTRUDNIEŃ ({len(rest)} RODZAJÓW) =")

        lines.append("WYKAZ POCIĄGÓW =")
        for i, (_, group) in enumerate(shown, 1):
            # no-break space inside a name: "OS 94819" never splits over two strips
            names = sorted({keep_together(Renderer._short_train(r).upper()) for r in group},
                           key=lambda n: (len(n), n))
            wrapped = wrap(f"{', '.join(names)} =", wrap_w)
            lines.append(f"{i:02d}. {wrapped[0]}")
            lines.extend(indent + part for part in wrapped[1:])
        lines.append(CLOSING)
        return lines

    @staticmethod
    def _by_station_lines(
        rows: list[StationTrains], title: str, limit: int | None = None, *, width: int | None = None
    ) -> list[str]:
        """Stations in the given order; with `limit`, only the `limit` with most reports.
        With `width`, the trains go under their station instead of beside it."""
        if not rows:
            return [f"{title} = BRAK ZGŁOSZEŃ = STOP ="]
        total = sum(r.count for r in rows)
        shown, rest = rows, []
        if limit is not None and len(rows) > limit:
            ranked = sorted(rows, key=lambda r: -r.count)   # stable: equal counts keep the order
            shown, rest = ranked[:limit], ranked[limit:]
        station_w = max(len("STACJA"), *(len(r.station_name) for r in shown))
        count_w = len("ILOŚĆ")
        head = f"LP  {'STACJA':<{station_w}}  {'ILOŚĆ':>{count_w}}"
        if width:
            indent = " " * len("01. ")
            lines = [f"{title} = ZGŁOSZONO {total} RAZY NA {len(rows)} STACJACH =", head + " / POCIĄGI"]
        else:
            indent = " " * len(head + "  ")
            lines = [f"{title} = ZGŁOSZONO {total} RAZY NA {len(rows)} STACJACH =", head + "  POCIĄGI"]
        for i, r in enumerate(shown, 1):
            names = ", ".join(keep_together(t.upper()) for t in r.trains)
            lead = f"{i:02d}. {r.station_name.upper():<{station_w}}  {r.count:>{count_w}}"
            if width:
                lines.append(lead)
                lines.extend(indent + part for part in wrap(f"{names} =", width - len(indent)))
            else:
                wrapped = wrap(f"{names} =", MESSAGE_WIDTH)
                lines.append(f"{lead}  {wrapped[0]}")
                lines.extend(indent + part for part in wrapped[1:])
        if rest:
            lines.append(f"ORAZ {sum(r.count for r in rest)} ZGŁOSZEŃ NA {len(rest)} INNYCH STACJACH =")
        lines.append(CLOSING)
        return lines

    def _draw_header(
        self,
        d: ImageDraw.ImageDraw,
        type_: TypeFn,
        width: int,
        fields: list[tuple[str, str]],
        label: str,
        subject: str,
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
        line_w = width - 2 * MARGIN - 40
        type_((MARGIN + 40, 262), "WSZYSTKIE STACJE I DYŻURNI RUCHU", max_width=line_w)
        d.text((MARGIN, 300), "OD:", font=self._label, fill=FORM_INK)
        type_((MARGIN + 40, 294), f"TELEGRAF NIEOFICJALNY = DOT. {subject} = {label}", max_width=line_w)

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
