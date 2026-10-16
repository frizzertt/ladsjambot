import io
import math
import logging
import os
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from bot import fonts as _mw
from bot.fonts import font as _mfont
from bot.lastfm import TrackInfo

logger = logging.getLogger(__name__)

CELL_SIZE = 800
INSET = 10
CARD_RADIUS = 6
BANNER_HEIGHT = 340
BG_COLOR = (13, 13, 14, 255)

# Path to custom bundled Google Fonts
FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
MICHROMA_PATH = os.path.join(FONTS_DIR, "Michroma-Regular.ttf")
JURA_PATH = os.path.join(FONTS_DIR, "Jura.ttf")


def _get_michroma_font(size: int) -> ImageFont.ImageFont:
    """Heavy display face (Montserrat ExtraBold)."""
    return _mfont(size, _mw.EXTRABOLD)


def _legacy_michroma(size: int) -> ImageFont.ImageFont:
    if os.path.exists(MICHROMA_PATH):
        try:
            return ImageFont.truetype(MICHROMA_PATH, size)
        except Exception as e:
            logger.warning("Failed to load Michroma font from %s: %s", MICHROMA_PATH, e)

    return _get_fallback_font(size, bold=True)


def _get_fallback_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Body face (Montserrat Bold / SemiBold)."""
    return _mfont(size, _mw.BOLD if bold else _mw.SEMIBOLD)


def _legacy_fallback(size: int, bold: bool = False) -> ImageFont.ImageFont:
    if os.path.exists(JURA_PATH):
        try:
            return ImageFont.truetype(JURA_PATH, size)
        except Exception:
            pass

    font_candidates = [
        # macOS
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        # Linux (Ubuntu, Debian, Fedora, Alpine)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        # Windows
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "arial.ttf",
    ]

    for candidate in font_candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue

    return ImageFont.load_default(size=size)


def _is_cyrillic(ch: str) -> bool:
    """Check if character is within Cyrillic Unicode ranges."""
    code = ord(ch)
    return (0x0400 <= code <= 0x04FF) or (0x0500 <= code <= 0x052F) or (0x2DE0 <= code <= 0x2DFF)


def _split_by_script(text: str) -> List[Tuple[str, bool]]:
    """
    Split text into contiguous chunks of (Latin/Symbols, is_cyrillic).
    Enables rendering Latin with Michroma and Cyrillic with Jura seamlessly.
    """
    if not text:
        return []

    chunks: List[Tuple[str, bool]] = []
    current_is_cyr = _is_cyrillic(text[0])
    current_chars = [text[0]]

    for ch in text[1:]:
        c_cyr = _is_cyrillic(ch)
        if c_cyr == current_is_cyr:
            current_chars.append(ch)
        else:
            chunks.append(("".join(current_chars), current_is_cyr))
            current_is_cyr = c_cyr
            current_chars = [ch]

    chunks.append(("".join(current_chars), current_is_cyr))
    return chunks


def _measure_hybrid_text(
    text: str,
    font_primary: ImageFont.ImageFont,
    font_fallback: ImageFont.ImageFont,
) -> float:
    """Measure total horizontal width of hybrid text."""
    total = 0.0
    for chunk, is_cyr in _split_by_script(text):
        f = font_fallback if is_cyr else font_primary
        total += f.getlength(chunk)
    return total


def _fit_hybrid_text(
    text: str,
    font_primary: ImageFont.ImageFont,
    font_fallback: ImageFont.ImageFont,
    max_width: int,
) -> str:
    """Truncate text with ellipsis if its hybrid width exceeds max_width."""
    if not text:
        return ""

    if _measure_hybrid_text(text, font_primary, font_fallback) <= max_width:
        return text

    truncated = text
    while truncated and _measure_hybrid_text(truncated + "...", font_primary, font_fallback) > max_width:
        truncated = truncated[:-1]

    return (truncated.rstrip() + "...") if truncated else "..."


def _draw_hybrid_text(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font_primary: ImageFont.ImageFont,
    font_fallback: ImageFont.ImageFont,
    fill: Tuple[int, int, int, int],
    anchor: str = "lm",
) -> float:
    """Draw text switching seamlessly between Michroma (Latin) and Jura (Cyrillic)."""
    x, y = xy
    for chunk, is_cyr in _split_by_script(text):
        f = font_fallback if is_cyr else font_primary
        draw.text((x, y), chunk, font=f, fill=fill, anchor=anchor)
        x += draw.textlength(chunk, font=f)
    return x


def _round_corners(image: Image.Image, radius: int) -> Image.Image:
    """Clip image to rounded corners using 2x supersampled anti-aliasing."""
    scale = 2
    w, h = image.size
    mask = Image.new("L", (w * scale, h * scale), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle(
        [(0, 0), (w * scale - 1, h * scale - 1)],
        radius=radius * scale,
        fill=255,
    )
    mask = mask.resize((w, h), Image.Resampling.LANCZOS)

    output = image.copy().convert("RGBA")
    output.putalpha(mask)
    return output


def _create_shadow(card_size: int, radius: int) -> Tuple[Image.Image, int]:
    """Generate a soft floating drop shadow for a card."""
    blur = 16
    pad = blur * 2
    w = card_size + pad
    h = card_size + pad
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle(
        [(blur, blur + 4), (w - blur, h - blur + 4)],
        radius=radius,
        fill=(0, 0, 0, 110),
    )
    return shadow.filter(ImageFilter.GaussianBlur(blur // 2)), blur


def _dominant(img: Image.Image) -> Tuple[int, int, int]:
    """Vivid dominant colour of an image (saturation-boosted average)."""
    from PIL import ImageEnhance

    small = img.convert("RGB").resize((24, 24), Image.Resampling.BILINEAR)
    small = ImageEnhance.Color(small).enhance(1.9)
    r, g, b = small.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    m = max(r, g, b, 1)
    k = 235 / m  # normalise brightness so chips pop on dark photos
    return (min(255, int(r * k)), min(255, int(g * k)), min(255, int(b * k)))


def _create_placeholder(size: int) -> Image.Image:
    """Vivid gradient + vinyl rings when no cover art is available."""
    card = Image.new("RGBA", (size, size), (30, 24, 60, 255))
    px = card.load()
    for y in range(size):
        for x in range(0, size, 4):
            t = (x + y) / (2.0 * size)
            c = (int(70 + 150 * t), int(40 + 20 * t), int(190 - 80 * t), 255)
            for dx in range(4):
                if x + dx < size:
                    px[x + dx, y] = c
    d = ImageDraw.Draw(card)
    cx, cy = size // 2, int(size * 0.38)
    for r in range(30, int(size * 0.42), 26):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 40), width=2)
    d.text((cx, cy), "♫", fill=(255, 255, 255, 255), font=_mw.symbol_font(int(size * 0.2)), anchor="mm")
    return card


def _create_empty_slot(size: int, radius: int, item: Optional[TrackInfo] = None) -> Image.Image:
    """Filler tile: tinted, blurred cover of someone else's track instead of a blank slot."""
    card = None
    if item is not None and item.cover_bytes:
        try:
            with Image.open(io.BytesIO(item.cover_bytes)) as im:
                im = im.convert("RGB")
                m = min(im.size)
                im = im.crop(((im.width - m) // 2, (im.height - m) // 2,
                              (im.width + m) // 2, (im.height + m) // 2))
                card = im.resize((size, size), Image.Resampling.LANCZOS).filter(
                    ImageFilter.GaussianBlur(size // 28)
                )
                card = Image.blend(card, Image.new("RGB", card.size, (12, 12, 18)), 0.45).convert("RGBA")
        except Exception:
            card = None
    if card is None:
        card = _create_placeholder(size)
    draw = ImageDraw.Draw(card)
    draw.text((size // 2, size // 2), "♫", fill=(255, 255, 255, 150),
              font=_mw.symbol_font(int(size * 0.22)), anchor="mm")
    f = _mfont(int(size * 0.05), _mw.BOLD)
    draw.text((size // 2, int(size * 0.68)), "СВОБОДНО", fill=(255, 255, 255, 170), font=f, anchor="mm")
    return _round_corners(card, radius)


def _draw_card_banner(
    card: Image.Image,
    tg_name: str,
    track: str,
    artist: str,
    size: int,
    banner_height: int,
    accent: Tuple[int, int, int] = (255, 90, 60),
) -> None:
    """Big, heavy type over a strong gradient + colourful listener chip (phone-first sizes)."""
    banner_height = int(banner_height * 1.25)
    grad = Image.new("RGBA", (size, banner_height), (0, 0, 0, 0))
    d_grad = ImageDraw.Draw(grad)
    for y in range(banner_height):
        progress = y / float(banner_height)
        d_grad.line([(0, y), (size, y)], fill=(4, 4, 8, int(245 * (progress ** 1.25))))
    card.paste(grad, (0, size - banner_height), grad)

    pad = int(size * 0.05)
    max_w = size - pad * 2
    draw = ImageDraw.Draw(card)

    # listener chip (top-left): accent-coloured, dark bold text
    f_user = _mfont(int(size * 0.052), _mw.EXTRABOLD)
    chip_h = int(size * 0.1)
    name = _fit_hybrid_text(tg_name, f_user, f_user, size - pad * 3)
    tw = f_user.getlength(name)
    chip_w = int(tw + chip_h * 0.9)
    draw.rounded_rectangle([(pad, pad), (pad + chip_w, pad + chip_h)], radius=chip_h // 2, fill=accent + (255,))
    draw.text((pad + chip_h * 0.45, pad + chip_h / 2), name, font=f_user, fill=(12, 12, 16, 255), anchor="lm")

    # artist (accent colour) and track (white, up to 2 lines)
    f_artist = _mfont(int(size * 0.056), _mw.BOLD)
    f_track = _mfont(int(size * 0.088), _mw.EXTRABOLD)
    artist_d = _fit_hybrid_text(artist, f_artist, f_artist, max_w)
    y_artist = size - pad - int(size * 0.02)
    draw.text((pad, y_artist), artist_d, font=f_artist, fill=accent + (255,), anchor="ls")

    words, lines, cur = track.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f_track.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    lines = [_fit_hybrid_text(ln, f_track, f_track, max_w) for ln in lines]
    lh = int(size * 0.1)
    y = y_artist - int(size * 0.075) - lh * (len(lines) - 1)
    for ln in lines:
        draw.text((pad, y), ln, font=f_track, fill=(255, 255, 255, 255), anchor="ls")
        y += lh


def _build_background(items: List[TrackInfo], width: int, height: int) -> Image.Image:
    """Blurred, darkened cover art as ambient canvas background (falls back to flat color)."""
    for item in items:
        if not item.cover_bytes:
            continue
        try:
            with Image.open(io.BytesIO(item.cover_bytes)) as im:
                bg = im.convert("RGB")
            bg = bg.resize((64, 64), Image.Resampling.BILINEAR)
            bg = bg.resize((width, height), Image.Resampling.BICUBIC)
            bg = bg.filter(ImageFilter.GaussianBlur(max(width, height) // 12))
            dark = Image.new("RGB", (width, height), (10, 11, 16))
            return Image.blend(bg, dark, 0.78).convert("RGBA")
        except Exception:
            continue
    return Image.new("RGBA", (width, height), BG_COLOR)


def _render_single_card(item: TrackInfo, card_size: int, radius: int, banner_h: int) -> Image.Image:
    """Build a complete individual song card with cover, banner, rounded corners, and border."""
    card: Optional[Image.Image] = None

    if item.cover_bytes:
        try:
            with Image.open(io.BytesIO(item.cover_bytes)) as original:
                cover = original.convert("RGBA")
                w, h = cover.size
                if w != h:
                    min_dim = min(w, h)
                    left = (w - min_dim) // 2
                    top = (h - min_dim) // 2
                    cover = cover.crop((left, top, left + min_dim, top + min_dim))
                card = cover.resize((card_size, card_size), Image.Resampling.LANCZOS)
        except Exception as e:
            logger.warning("Failed to decode cover image for %s: %s", item.tg_name, e)

    if card is None:
        card = _create_placeholder(card_size)

    accent = _dominant(card)
    _draw_card_banner(card, item.tg_name, item.track, item.artist, card_size, banner_h, accent)

    # Clip with rounded corners
    card = _round_corners(card, radius)

    # Add subtle glass border
    border = Image.new("RGBA", (card_size, card_size), (0, 0, 0, 0))
    b_draw = ImageDraw.Draw(border)
    b_draw.rounded_rectangle(
        [(0, 0), (card_size - 1, card_size - 1)],
        radius=radius,
        outline=(255, 255, 255, 40),
        width=2,
    )
    card.paste(border, (0, 0), border)

    return card


def build_collage(items: List[TrackInfo], cell_size: int = CELL_SIZE) -> io.BytesIO:
    """
    Synchronous image rendering function to be run via asyncio.to_thread().
    Calculates dynamic grid layout:
      1 -> 1x1
      2 -> 2x1
      3-4 -> 2x2
      5-6 -> 3x2
      7-8 -> 3x3
      etc.
    Renders floating glassmorphism music cards with Google Font Michroma,
    rounded corners, drop shadows, live equalizer badges, and sleek empty slot cards.
    Returns: BytesIO object containing JPEG image data.
    """
    n = len(items)
    if n == 0:
        raise ValueError("Cannot create collage from empty items list")

    # Proportional parameters
    scale = cell_size / float(CELL_SIZE)
    inset = max(4, int(INSET * scale))
    card_size = cell_size - inset * 2
    card_radius = max(8, int(CARD_RADIUS * scale))
    banner_height = max(50, int(BANNER_HEIGHT * scale))

    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)

    canvas_width = cols * cell_size
    canvas_height = rows * cell_size

    # Initialize canvas with deep obsidian background
    canvas = Image.new("RGBA", (canvas_width, canvas_height), BG_COLOR)

    # Pre-render card drop shadow
    shadow_img, shadow_pad = _create_shadow(card_size, card_radius)

    total_cells = cols * rows
    for idx in range(total_cells):
        col = idx % cols
        row = idx // cols
        base_x = col * cell_size + inset
        base_y = row * cell_size + inset

        # Paste soft shadow beneath card

        if idx < n:
            card_img = _render_single_card(items[idx], card_size, card_radius, banner_height)
        else:
            card_img = _create_empty_slot(card_size, card_radius, items[(idx - n) % n])

        canvas.paste(card_img, (base_x, base_y), card_img)

    # Convert to RGB and export to BytesIO buffer
    output_buffer = io.BytesIO()
    canvas.convert("RGB").save(output_buffer, format="JPEG", quality=94, optimize=True)
    output_buffer.seek(0)

    return output_buffer
