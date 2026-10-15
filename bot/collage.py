import io
import math
import logging
import os
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from bot.lastfm import TrackInfo

logger = logging.getLogger(__name__)

CELL_SIZE = 500
INSET = 8
CARD_RADIUS = 22
BANNER_HEIGHT = 135
BG_COLOR = (12, 13, 17, 255)

# Path to custom bundled Google Fonts
FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
MICHROMA_PATH = os.path.join(FONTS_DIR, "Michroma-Regular.ttf")
JURA_PATH = os.path.join(FONTS_DIR, "Jura.ttf")


def _get_michroma_font(size: int) -> ImageFont.ImageFont:
    """Load Google Font Michroma or fallback to default."""
    if os.path.exists(MICHROMA_PATH):
        try:
            return ImageFont.truetype(MICHROMA_PATH, size)
        except Exception as e:
            logger.warning("Failed to load Michroma font from %s: %s", MICHROMA_PATH, e)

    return _get_fallback_font(size, bold=True)


def _get_fallback_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """
    Find and return Jura (Eurostile/techno Cyrillic font) or system TrueType fallback.
    """
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


def _create_placeholder(size: int) -> Image.Image:
    """
    Generate an authentic dark vinyl record background when no cover art is available.
    """
    card = Image.new("RGBA", (size, size), (20, 22, 29, 255))
    draw = ImageDraw.Draw(card)

    cx, cy = size // 2, int(size * 0.38)

    # Concentric vinyl grooves
    max_groove = int(size * 0.44)
    for r in range(40, max_groove, 20):
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            outline=(255, 255, 255, 13),
            width=1,
        )

    # Center vinyl label
    r_label = int(size * 0.11)
    draw.ellipse(
        [cx - r_label, cy - r_label, cx + r_label, cy + r_label],
        fill=(32, 35, 48, 255),
        outline=(99, 102, 241, 150),
        width=2,
    )

    # Music note icon in center of record
    font_note = _get_fallback_font(int(size * 0.088), bold=True)
    draw.text((cx, cy), "♫", fill=(225, 230, 255, 255), font=font_note, anchor="mm")

    return card


def _create_empty_slot(size: int, radius: int) -> Image.Image:
    """
    Generate an elegant dark ghost card for unused slots in the dynamic grid.
    """
    card = Image.new("RGBA", (size, size), (16, 17, 23, 255))
    draw = ImageDraw.Draw(card)

    # Subtle rounded border
    draw.rounded_rectangle(
        [(2, 2), (size - 3, size - 3)],
        radius=radius,
        outline=(36, 40, 54, 255),
        width=2,
    )

    font_icon = _get_fallback_font(int(size * 0.08))
    font_m_title = _get_michroma_font(int(size * 0.034))
    font_j_title = _get_fallback_font(int(size * 0.034), bold=True)
    font_m_sub = _get_michroma_font(int(size * 0.026))
    font_j_sub = _get_fallback_font(int(size * 0.026))

    cy = size // 2 - 18
    draw.text((size // 2, cy), "🎧", fill=(90, 95, 115, 255), font=font_icon, anchor="mm")

    # Centered hybrid text
    title_text = "Свободное место"
    title_w = _measure_hybrid_text(title_text, font_m_title, font_j_title)
    _draw_hybrid_text(
        draw,
        (int(size // 2 - title_w // 2), cy + 42),
        title_text,
        font_m_title,
        font_j_title,
        (130, 135, 155, 255),
        anchor="lm",
    )

    sub_text = "Ожидание трека на Last.fm"
    sub_w = _measure_hybrid_text(sub_text, font_m_sub, font_j_sub)
    _draw_hybrid_text(
        draw,
        (int(size // 2 - sub_w // 2), cy + 68),
        sub_text,
        font_m_sub,
        font_j_sub,
        (85, 90, 110, 255),
        anchor="lm",
    )

    return _round_corners(card, radius)


def _draw_card_banner(
    card: Image.Image,
    tg_name: str,
    track: str,
    artist: str,
    size: int,
    banner_height: int,
) -> None:
    """
    Draw a frosted-glass bottom gradient banner with live equalizer, user handle, and track info
    using Google Font Michroma with Jura fallback.
    """
    # 1. Smooth exponential alpha gradient fade
    grad = Image.new("RGBA", (size, banner_height), (0, 0, 0, 0))
    d_grad = ImageDraw.Draw(grad)
    for y in range(banner_height):
        progress = y / float(banner_height)
        alpha = int(242 * (progress ** 1.35))
        d_grad.line([(0, y), (size, y)], fill=(8, 10, 15, alpha))
    card.paste(grad, (0, size - banner_height), grad)

    draw = ImageDraw.Draw(card)

    # 2. Sleek top hairline divider across the banner
    draw.line(
        [(0, size - banner_height), (size, size - banner_height)],
        fill=(255, 255, 255, 26),
    )

    pad_x = int(size * 0.046)

    # 3. Mini Animated Equalizer bars in vibrant Spotify Green
    eq_x = pad_x
    eq_y = size - int(banner_height * 0.76)
    bar_heights = [12, 22, 10, 18]
    for i, h in enumerate(bar_heights):
        bx = eq_x + i * 6
        draw.rounded_rectangle(
            [(bx, eq_y + (22 - h)), (bx + 3, eq_y + 22)],
            radius=2,
            fill=(30, 215, 96, 255),
        )

    # 4. Telegram user handle with Michroma
    font_m_user = _get_michroma_font(int(size * 0.038))
    font_j_user = _get_fallback_font(int(size * 0.038), bold=True)
    max_user_w = size - pad_x * 2 - 38
    user_display = _fit_hybrid_text(tg_name, font_m_user, font_j_user, max_user_w)
    _draw_hybrid_text(
        draw,
        (eq_x + 32, eq_y + 11),
        user_display,
        font_m_user,
        font_j_user,
        (255, 255, 255, 255),
        anchor="lm",
    )

    # 5. Track title with Michroma
    font_m_track = _get_michroma_font(int(size * 0.034))
    font_j_track = _get_fallback_font(int(size * 0.034), bold=True)
    max_text_w = size - pad_x * 2
    track_display = _fit_hybrid_text(track, font_m_track, font_j_track, max_text_w)
    _draw_hybrid_text(
        draw,
        (pad_x, size - int(banner_height * 0.42)),
        track_display,
        font_m_track,
        font_j_track,
        (245, 247, 255, 255),
        anchor="lm",
    )

    # 6. Artist name with Michroma
    font_m_artist = _get_michroma_font(int(size * 0.029))
    font_j_artist = _get_fallback_font(int(size * 0.029), bold=False)
    artist_display = _fit_hybrid_text(artist, font_m_artist, font_j_artist, max_text_w)
    _draw_hybrid_text(
        draw,
        (pad_x, size - int(banner_height * 0.19)),
        artist_display,
        font_m_artist,
        font_j_artist,
        (165, 172, 192, 255),
        anchor="lm",
    )


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

    # Draw bottom glassmorphism banner with Michroma typography
    _draw_card_banner(card, item.tg_name, item.track, item.artist, card_size, banner_h)

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
        canvas.paste(
            shadow_img,
            (base_x - shadow_pad, base_y - shadow_pad),
            shadow_img,
        )

        if idx < n:
            card_img = _render_single_card(items[idx], card_size, card_radius, banner_height)
        else:
            card_img = _create_empty_slot(card_size, card_radius)

        canvas.paste(card_img, (base_x, base_y), card_img)

    # Convert to RGB and export to BytesIO buffer
    output_buffer = io.BytesIO()
    canvas.convert("RGB").save(output_buffer, format="JPEG", quality=94, optimize=True)
    output_buffer.seek(0)

    return output_buffer
