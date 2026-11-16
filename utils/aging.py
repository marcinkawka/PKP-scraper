'''Deterministic "typewritten in the 1950s, aged, scanned" effects for the telegram renderer.

Everything here is procedural (no downloaded textures) and seeded: the same seed
gives the same image. Each effect draws from its own random stream, so switching
one effect off does not change how the others look.
'''

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, fields

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

RUST = np.array([0.62, 0.40, 0.20], dtype=np.float32)
TEA = np.array([0.66, 0.50, 0.30], dtype=np.float32)
SCANNER_BED = (238, 236, 230)

# A machine with bent typebars: these letters are always off. (dy px, rotation deg)
BAD_TYPEBARS = {
    "A": (-1.5, 0.0), "a": (-1.5, 0.0),
    "Ł": (0.4, 2.4), "ł": (0.4, 2.4),
    "N": (1.2, -1.0), "n": (1.2, -1.0),
}
COUNTER_LETTERS = set("OADPRBQÓĄoadpbqeóą")
COUNTER_DIGITS = set("0689")


@dataclass(frozen=True)
class AgingConfig:
    """`intensity` (0..1) scales every effect; the flags switch single effects off."""

    intensity: float = 0.6
    # typewriter, per glyph
    glyph_jitter: bool = True       # vertical offset, rotation, opacity
    bad_typebars: bool = True
    filled_counters: bool = True
    ribbon_wear: bool = True        # ink fading along the line, uneven density
    double_strike: bool = True
    row_jitter: bool = True         # each line slightly shifted
    # stamp
    stamp_wear: bool = True         # uneven pressure, gaps, misaligned date insert
    # paper and scan
    paper_texture: bool = True      # mottling, fibres, yellowing
    folds: bool = True
    vignette: bool = True
    ragged_edge: bool = True
    foxing: bool = True
    cup_ring: bool = True
    scan: bool = True               # sheet rotation, uneven light, grain

    def __post_init__(self) -> None:
        if not 0.0 <= self.intensity <= 1.0:
            raise ValueError(f"intensity must be within 0..1, got {self.intensity}")

    @classmethod
    def effects(cls) -> list[str]:
        return [f.name for f in fields(cls) if f.type == "bool"]


def seed_for(*parts: str) -> int:
    """Stable seed from text (e.g. the telegram number); hash() is salted per process."""
    return int.from_bytes(hashlib.sha256("|".join(parts).encode()).digest()[:8], "big")


def _noise(rng: np.random.Generator, width: int, height: int, cell: float, octaves: int = 4) -> np.ndarray:
    """Fractal value noise, zero mean, roughly within -1..1. `cell` = size of the largest blobs in px."""
    out = np.zeros((height, width), dtype=np.float32)
    amp = 1.0
    for octave in range(octaves):
        c = max(cell / 2**octave, 1.0)
        grid = rng.random((max(int(height / c) + 2, 2), max(int(width / c) + 2, 2)))
        layer = Image.fromarray((grid * 255).astype(np.uint8)).resize((width, height), Image.BICUBIC)
        out += amp * (np.asarray(layer, dtype=np.float32) / 255 - 0.5)
        amp *= 0.5
    return np.clip(out / (3 * out.std() + 1e-6), -1, 1)


class Aging:
    """Effects for one image of a known size."""

    def __init__(self, config: AgingConfig, seed: int, size: tuple[int, int]) -> None:
        self.cfg = config
        self.k = config.intensity
        self.width, self.height = size
        self._seed = seed
        r = self.rng("folds")
        self.fold_x = round(self.width * r.uniform(0.47, 0.53))
        self.fold_y = round(self.height * r.uniform(0.45, 0.55))

    def rng(self, stream: str) -> random.Random:
        return random.Random(f"{self._seed}:{stream}")

    def np_rng(self, stream: str) -> np.random.Generator:
        return np.random.default_rng(seed_for(str(self._seed), stream))

    # ---------------------------------------------------------------- folds

    def _fold_distance(self) -> tuple[np.ndarray, np.ndarray]:
        """Signed distance in px to the vertical and to the horizontal fold."""
        x = np.arange(self.width, dtype=np.float32)[None, :] - self.fold_x
        y = np.arange(self.height, dtype=np.float32)[:, None] - self.fold_y
        return x, y

    def fold_fade(self) -> np.ndarray:
        """0..1 map of how much ink is rubbed off along the folds."""
        if not self.cfg.folds:
            return np.zeros((self.height, self.width), dtype=np.float32)
        x, y = self._fold_distance()
        return np.maximum(np.exp(-((x / 5) ** 2)), np.exp(-((y / 5) ** 2)))

    # ---------------------------------------------------------------- stamp

    def distress_stamp(self, stamp: Image.Image) -> Image.Image:
        """Uneven pressure, missing patches and ink grain in the stamp's alpha."""
        if not self.cfg.stamp_wear:
            return stamp
        rng, k = self.np_rng("stamp"), self.k
        w, h = stamp.size
        rgba = np.asarray(stamp, dtype=np.float32) / 255
        alpha = rgba[..., 3]
        angle = rng.uniform(0, 2 * math.pi)                       # the lighter side
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        across = ((xx - w / 2) * math.cos(angle) + (yy - h / 2) * math.sin(angle)) / (w / 2)
        alpha = alpha * (1 - 0.40 * k * np.clip((across + 1) / 2, 0, 1))
        patches = _noise(rng, w, h, cell=w / 4, octaves=3)
        alpha = alpha * np.clip(1 - 1.8 * k * np.clip(patches - 0.30, 0, 1), 0.35, 1)   # gaps in the ring
        alpha = alpha * np.where(rng.random((h, w)) < 0.16 * k, 0.25, 1.0)              # grain
        out = np.dstack([rgba[..., :3], alpha])
        return Image.fromarray((out * 255).astype(np.uint8), "RGBA")

    @staticmethod
    def multiply_paste(img: Image.Image, stamp: Image.Image, xy: tuple[int, int]) -> None:
        """Paste `stamp` (RGBA) with multiply blending, so what is under the ink stays readable."""
        x, y = xy
        box = (x, y, x + stamp.width, y + stamp.height)
        base = np.asarray(img.crop(box), dtype=np.float32) / 255
        s = np.asarray(stamp, dtype=np.float32) / 255
        out = base * (1 - s[..., 3:4] * (1 - s[..., :3]))
        img.paste(Image.fromarray((out * 255).astype(np.uint8), "RGB"), box)

    # ---------------------------------------------------------------- paper

    def age_paper(self, img: Image.Image) -> Image.Image:
        """Post-process the finished sheet: texture, folds, stains, then the "scan"."""
        cfg, k = self.cfg, self.k
        w, h = self.width, self.height
        a = np.asarray(img, dtype=np.float32) / 255
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

        if cfg.paper_texture:
            rng = self.np_rng("paper")
            a *= 1 + 0.07 * k * _noise(rng, w, h, cell=220)[..., None]                    # mottling
            a *= 1 + 0.03 * k * _noise(rng, w, h, cell=5, octaves=2)[..., None]           # tooth
            a *= 1 + 0.05 * k * self._fibres()[..., None]
            a *= 1 - k * np.array([0.02, 0.06, 0.15], dtype=np.float32)                   # yellowing
            a[..., 2] *= 1 - 0.07 * k * np.clip(_noise(rng, w, h, cell=320, octaves=2), 0, 1)

        if cfg.folds:
            rng = self.np_rng("folds")
            x, y = self._fold_distance()
            for d, along in ((x, h), (y, w)):
                ridge = np.exp(-((d / 1.6) ** 2))                 # paper lifted towards the light
                shadow = np.exp(-(((d - 4) / 5) ** 2))            # and the shade behind it
                # creases are not equally sharp along their length
                strength = 0.75 + 0.25 * np.repeat(rng.random(along // 40 + 2), 40)[:along]
                strength = strength[:, None] if d is x else strength[None, :]
                a *= 1 + (k * strength * (0.11 * ridge - 0.09 * shadow))[..., None]
                a *= 1 + (0.012 * k * np.tanh(d / 30))[..., None]  # panels catch light differently

        edge = np.minimum(np.minimum(xx, w - 1 - xx), np.minimum(yy, h - 1 - yy))
        if cfg.vignette:
            rng = self.np_rng("vignette")
            dark = np.exp(-edge / (0.07 * min(w, h))) * (0.75 + 0.25 * _noise(rng, w, h, cell=90))
            a *= 1 - 0.26 * k * dark[..., None] * np.array([0.8, 1.0, 1.3], dtype=np.float32)

        if cfg.foxing:
            self._foxing(a)
        if cfg.cup_ring:
            self._cup_ring(a, xx, yy)

        if cfg.scan:
            rng = self.np_rng("scan")
            angle = rng.uniform(0, 2 * math.pi)
            light = ((xx / w - 0.5) * math.cos(angle) + (yy / h - 0.5) * math.sin(angle)) * 2
            a *= 1 + 0.04 * k * light[..., None]
            a = (a - 0.5) * (1 + 0.06 * k) + 0.5                                           # photocopy contrast
            a += rng.normal(0, 0.011 * k, (h, w)).astype(np.float32)[..., None]            # grain

        sheet = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "RGB")
        if not (cfg.scan or cfg.ragged_edge):
            return sheet
        return self._scan(sheet, edge)

    def _fibres(self) -> np.ndarray:
        """Faint lighter and darker paper fibres, -1..1."""
        r = self.rng("fibres")
        layer = Image.new("L", (self.width, self.height), 128)
        d = ImageDraw.Draw(layer)
        for _ in range(self.width * self.height // 2500):
            x, y = r.uniform(0, self.width), r.uniform(0, self.height)
            length, angle = r.uniform(6, 28), r.uniform(0, math.pi)
            d.line((x, y, x + length * math.cos(angle), y + length * math.sin(angle)),
                   fill=128 + r.choice((-1, 1)) * r.randint(30, 110), width=1)
        layer = layer.filter(ImageFilter.GaussianBlur(0.7))
        return (np.asarray(layer, dtype=np.float32) - 128) / 127

    def _foxing(self, a: np.ndarray) -> None:
        """A few small rust-coloured spots, mostly towards the margins."""
        r, k = self.rng("foxing"), self.k
        w, h = self.width, self.height
        for _ in range(round(3 + 7 * k)):
            if r.random() < 0.7:        # margins
                cx, cy = r.choice([
                    (r.uniform(0, 0.08 * w), r.uniform(0, h)), (r.uniform(0.92 * w, w), r.uniform(0, h)),
                    (r.uniform(0, w), r.uniform(0, 0.06 * h)), (r.uniform(0, w), r.uniform(0.94 * h, h)),
                ])
            else:
                cx, cy = r.uniform(0, w), r.uniform(0, h)
            radius, strength = r.uniform(2.5, 9), r.uniform(0.18, 0.42) * k
            x0, x1 = max(int(cx - 3 * radius), 0), min(int(cx + 3 * radius) + 1, w)
            y0, y1 = max(int(cy - 3 * radius), 0), min(int(cy + 3 * radius) + 1, h)
            if x0 >= x1 or y0 >= y1:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            blob = strength * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / radius**2))
            a[y0:y1, x0:x1] *= 1 - blob[..., None] * (1 - RUST)

    def _cup_ring(self, a: np.ndarray, xx: np.ndarray, yy: np.ndarray) -> None:
        """One faint, incomplete tea-cup ring in the top-left corner, clear of the table."""
        r, k = self.rng("cup"), self.k
        radius = r.uniform(70, 85)
        cx, cy = r.uniform(70, 105), r.uniform(60, 85)
        dist = np.hypot(xx - cx, yy - cy)
        theta = np.arctan2(yy - cy, xx - cx)
        # the liquid pooled unevenly around the rim
        uneven = 0.55 + 0.45 * np.sin(theta * 1.0 + r.uniform(0, 6.28)) * np.sin(theta * 2.3 + r.uniform(0, 6.28))
        ring = np.exp(-(((dist - radius) / 2.2) ** 2)) * np.clip(uneven, 0.05, 1)
        stain = 0.20 * k * ring + 0.025 * k * (dist < radius)
        a *= 1 - stain[..., None] * (1 - TEA)

    def _scan(self, sheet: Image.Image, edge: np.ndarray) -> Image.Image:
        """Ragged sheet, slightly rotated, lying on the scanner bed."""
        cfg, k = self.cfg, self.k
        w, h = self.width, self.height
        alpha = np.full((h, w), 255, dtype=np.uint8)
        if cfg.ragged_edge:
            rng = self.np_rng("edge")
            wobble = (1.2 + 2.8 * k) * (_noise(rng, w, h, cell=18, octaves=3) + 1) / 2
            alpha = np.where(edge >= wobble, 255, 0).astype(np.uint8)
        rgba = sheet.convert("RGBA")
        rgba.putalpha(Image.fromarray(alpha))
        if cfg.scan:
            r = self.rng("rotation")
            angle = r.choice((-1, 1)) * (0.3 + 0.5 * k) * r.uniform(0.8, 1.0)
            rgba = rgba.rotate(angle, resample=Image.BICUBIC, expand=True)
        pad = 16
        out = Image.new("RGB", (rgba.width + 2 * pad, rgba.height + 2 * pad), SCANNER_BED)
        shadow = Image.new("L", out.size, 0)
        shadow.paste(rgba.getchannel("A"), (pad + 3, pad + 4))
        shadow = shadow.filter(ImageFilter.GaussianBlur(5)).point(lambda v: int(v * 0.30))
        out.paste((70, 62, 50), mask=shadow)
        out.paste(rgba, (pad, pad), rgba)
        return out


class Typewriter:
    """Types text glyph by glyph into an ink mask, with the flaws of a worn machine."""

    PAD = 6

    def __init__(self, aging: Aging, font: ImageFont.FreeTypeFont) -> None:
        self._aging = aging
        self._cfg, self._k = aging.cfg, aging.k
        self._font = font
        self._char_w = font.getlength("M")
        ascent, descent = font.getmetrics()
        self._tile = (math.ceil(self._char_w) + 2 * self.PAD, ascent + descent + 2 * self.PAD)
        self._mask = Image.new("L", (aging.width, aging.height), 0)
        self._rng = aging.rng("typewriter")
        self._strikes_left = (1 if self._k < 0.5 else 2) if self._cfg.double_strike and self._k > 0 else 0

    def type(self, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont | None = None) -> None:
        """Type `text` with its top-left at `xy`; `font` = a smaller size of the machine's face."""
        cfg, k, r = self._cfg, self._k, self._rng
        x0, y0 = xy
        char_w = self._char_w
        if font is None:
            font = self._font
        else:   # keep the smaller type on the same baseline
            char_w = font.getlength("M")
            y0 += self._font.getmetrics()[0] - font.getmetrics()[0]
        if cfg.row_jitter:      # paper insertion / platen advance
            x0 += r.uniform(-1.5, 1.5) * k
            y0 += r.uniform(-1.2, 1.2) * k
        ribbon = r.uniform(0.90, 1.0) if cfg.ribbon_wear else 1.0
        phase = r.uniform(0, 6.28)
        for i, ch in enumerate(text):
            if ch.isspace():
                continue
            digit = ch.isdigit()
            dy = rot = 0.0
            opacity = 1.0
            if cfg.glyph_jitter:
                dy = r.uniform(-1.0, 1.0) * k
                rot = r.uniform(-0.8, 0.8) * k
                opacity -= r.uniform(0.0, 0.30) * k
            if cfg.bad_typebars and ch in BAD_TYPEBARS:
                bar_dy, bar_rot = BAD_TYPEBARS[ch]
                dy += bar_dy * k
                rot += bar_rot * k
            if cfg.ribbon_wear:     # lighter towards the end of the line, uneven along it
                opacity *= ribbon * (1 - 0.20 * k * min(i / 70, 1)) * (1 - 0.06 * k * (1 + math.sin(i / 5 + phase)))
            if digit:               # times and delays must stay fully readable
                opacity = max(opacity, 0.88)

            tile = Image.new("L", self._tile, 0)
            ImageDraw.Draw(tile).text((self.PAD, self.PAD), ch, font=font, fill=255)
            if cfg.filled_counters:
                if ch in COUNTER_LETTERS and r.random() < 0.05 * k:
                    tile = self._fill_counters(tile, round(70 + 60 * k))     # clogged, not a blob
                elif ch in COUNTER_DIGITS and r.random() < 0.02 * k:
                    tile = self._fill_counters(tile, round(35 + 30 * k))

            px, py = x0 + i * char_w, y0 + dy
            ix, iy = math.floor(px), math.floor(py)
            tile = tile.rotate(rot, resample=Image.BICUBIC, translate=(px - ix, py - iy))
            tile = tile.point(lambda v, o=opacity: int(v * o))
            self._strike(tile, ix, iy)
            if (self._strikes_left and ch.isalpha() and r.random() < 0.006
                    and not any(c.isdigit() for c in text[max(i - 1, 0):i + 2])):
                self._strikes_left -= 1
                self._strike(tile.point(lambda v: int(v * 0.6)), ix + 1, iy + r.choice((0, 1)))

    @staticmethod
    def _fill_counters(tile: Image.Image, level: int) -> Image.Image:
        """Clog the enclosed parts of a glyph (ink and lint in the type slug)."""
        # .copy(): images made by fromarray are read-only, floodfill would silently do nothing
        paper = Image.fromarray(np.where(np.asarray(tile) < 96, 255, 0).astype(np.uint8)).copy()
        ImageDraw.floodfill(paper, (0, 0), 128)                 # everything reachable from outside
        holes = np.asarray(paper) == 255
        arr = np.asarray(tile).copy()
        arr[holes] = np.maximum(arr[holes], level)
        return Image.fromarray(arr)

    def _strike(self, tile: Image.Image, ix: int, iy: int) -> None:
        box = (ix - self.PAD, iy - self.PAD)
        region = self._mask.crop((*box, box[0] + tile.width, box[1] + tile.height))
        self._mask.paste(ImageChops.lighter(region, tile), box)

    def ink_mask(self) -> Image.Image:
        """Finished ink coverage: bled into the paper, unevenly inked, rubbed off at the folds."""
        cfg, k = self._cfg, self._k
        w, h = self._mask.size
        mask = self._mask.filter(ImageFilter.GaussianBlur(0.3 + 0.3 * k))
        m = np.asarray(mask, dtype=np.float32) / 255
        m = np.clip(m * 1.45, 0, 1)                             # the blur must not thin the strokes
        if cfg.ribbon_wear:
            rng = self._aging.np_rng("ribbon")
            m *= 1 - 0.16 * k * (_noise(rng, w, h, cell=60, octaves=3) + 1) / 2
            m *= 1 - 0.18 * k * rng.random((h, w), dtype=np.float32)
        m *= 1 - 0.28 * k * self._aging.fold_fade()
        return Image.fromarray((m * 255).astype(np.uint8), "L")
