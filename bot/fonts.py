"""Bundled Montserrat (variable) loader: heavy, highly legible Latin + Cyrillic."""
import os
from functools import lru_cache

from PIL import ImageFont

FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
MONTSERRAT = os.path.join(FONTS_DIR, "Montserrat.ttf")

MEDIUM, SEMIBOLD, BOLD, EXTRABOLD, BLACK = 500, 600, 700, 800, 900

_SYSTEM = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


@lru_cache(maxsize=None)
def font(px: int, weight: int = BOLD) -> ImageFont.ImageFont:
    try:
        f = ImageFont.truetype(MONTSERRAT, px)
        f.set_variation_by_axes([weight])
        return f
    except Exception:
        for c in _SYSTEM:
            try:
                return ImageFont.truetype(c, px)
            except Exception:
                continue
    return ImageFont.load_default(size=px)


@lru_cache(maxsize=None)
def symbol_font(px: int) -> ImageFont.ImageFont:
    """System font with ♫-style glyphs (Montserrat lacks them)."""
    for c in _SYSTEM:
        try:
            return ImageFont.truetype(c, px)
        except Exception:
            continue
    return ImageFont.load_default(size=px)
