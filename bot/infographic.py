"""Pillow renderers for stats infographics (dark neon style matching the collage)."""
import io
import math
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from bot.collage import (
    _draw_hybrid_text,
    _fit_hybrid_text,
    _get_fallback_font,
    _get_michroma_font,
    _measure_hybrid_text,
    _round_corners,
)
from bot.stats import ListeningStats

W = 1080
PAD = 40
GAP = 20
BG = (10, 11, 16)
CARD = (21, 23, 32)
BORDER = (40, 44, 60)
TEXT = (244, 246, 255)
MUTED = (140, 147, 170)
DIM = (62, 67, 86)
GREEN = (30, 215, 96)
PURPLE = (139, 92, 246)
PINK = (236, 72, 153)
AMBER = (251, 191, 36)

Row = Tuple[str, str, int]  # label, sublabel, value


@lru_cache(maxsize=None)
def _fonts(size: int, display: bool):
    if display:
        return _get_michroma_font(size), _get_fallback_font(size, bold=True)
    f = _get_fallback_font(size, bold=True)
    return f, f


def _text(draw, x, y, s, size, fill=TEXT, display=False, align="l", max_w=None) -> float:
    fp, ff = _fonts(size, display)
    s = str(s)
    if max_w:
        s = _fit_hybrid_text(s, fp, ff, int(max_w))
    w = _measure_hybrid_text(s, fp, ff)
    if align == "r":
        x -= w
    elif align == "m":
        x -= w / 2
    _draw_hybrid_text(draw, (int(x), int(y)), s, fp, ff, fill + (255,), anchor="lm")
    return w


def _card(draw, box, radius=26):
    draw.rounded_rectangle(box, radius=radius, fill=CARD, outline=BORDER, width=2)


def _canvas(height: int) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (W, height), BG + (255,))
    # soft accent glows in the corners
    glow = Image.new("RGBA", (W, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-260, -260, 420, 420], fill=GREEN + (26,))
    gd.ellipse([W - 420, height - 420, W + 260, height + 260], fill=PURPLE + (30,))
    from PIL import ImageFilter

    glow = glow.filter(ImageFilter.GaussianBlur(110))
    img.alpha_composite(glow)
    return img, ImageDraw.Draw(img)


def _header(draw, title: str, subtitle: str) -> int:
    draw.rounded_rectangle([PAD, PAD + 4, PAD + 8, PAD + 62], radius=4, fill=GREEN)
    _text(draw, PAD + 26, PAD + 18, title, 34, TEXT, display=True)
    _text(draw, PAD + 26, PAD + 54, subtitle, 22, MUTED)
    return PAD + 96


def _kpi_row(draw, y: int, items: Sequence[Tuple[str, str, Tuple[int, int, int]]]) -> int:
    n = len(items)
    w = (W - 2 * PAD - GAP * (n - 1)) // n
    h = 140
    for i, (value, label, color) in enumerate(items):
        x = PAD + i * (w + GAP)
        _card(draw, (x, y, x + w, y + h))
        draw.rounded_rectangle([x + 22, y + 24, x + 30, y + 52], radius=3, fill=color)
        vs = 38
        while vs > 22 and _measure_hybrid_text(value, *_fonts(vs, True)) > w - 64:
            vs -= 2
        _text(draw, x + 44, y + 38, value, vs, TEXT, display=True, max_w=w - 60)
        _text(draw, x + 22, y + 100, label, 21, MUTED, max_w=w - 40)
    return y + h + GAP


def _bar_chart(
    draw,
    box,
    values: Sequence[int],
    labels: Sequence[str],
    color,
    title: str,
    note: str = "",
    show_values: bool = True,
    label_every: int = 1,
) -> int:
    x0, y0, x1, y1 = box
    _card(draw, box)
    _text(draw, x0 + 28, y0 + 36, title, 24, TEXT, display=True)
    if note:
        _text(draw, x1 - 28, y0 + 36, note, 20, MUTED, align="r")

    px0, px1 = x0 + 28, x1 - 28
    top, bottom = y0 + 92, y1 - 52
    n = max(1, len(values))
    slot = (px1 - px0) / n
    bw = max(4, int(slot * 0.62))
    vmax = max(max(values, default=0), 1)
    imax = values.index(max(values)) if values and max(values) > 0 else -1

    # baseline
    draw.line([(px0, bottom), (px1, bottom)], fill=BORDER, width=2)

    for i, v in enumerate(values):
        cx = px0 + slot * i + slot / 2
        bh = int((bottom - top) * v / vmax)
        if v > 0:
            bh = max(bh, 6)
            col = color if i != imax else tuple(min(255, c + 45) for c in color)
            draw.rounded_rectangle(
                [cx - bw / 2, bottom - bh, cx + bw / 2, bottom],
                radius=min(8, bw // 2),
                fill=col,
            )
            if show_values:
                _text(draw, cx, bottom - bh - 14, v, 17 if n > 14 else 20, TEXT, align="m")
        else:
            draw.rounded_rectangle(
                [cx - bw / 2, bottom - 4, cx + bw / 2, bottom], radius=2, fill=DIM
            )
        if i % label_every == 0:
            _text(draw, cx, bottom + 26, labels[i], 17 if n > 14 else 19, MUTED, align="m")
    return y1 + GAP


def _rank_rows(draw, box, title: str, rows: Sequence[Row], color) -> int:
    x0, y0, x1, y1 = box
    _card(draw, box)
    _text(draw, x0 + 28, y0 + 36, title, 24, TEXT, display=True)
    vmax = max([r[2] for r in rows], default=1) or 1
    y = y0 + 84
    row_h = 70
    for i, (label, sub, val) in enumerate(rows):
        _text(draw, x0 + 28, y + 22, f"{i + 1}", 26, color if i == 0 else MUTED, display=True)
        tx = x0 + 84
        max_w = (x1 - x0) - 84 - 130
        _text(draw, tx, y + 18, label, 25, TEXT, max_w=max_w)
        if sub:
            _text(draw, tx, y + 46, sub, 19, MUTED, max_w=max_w)
        _text(draw, x1 - 28, y + 22, val, 25, color, display=True, align="r")
        bar_y = y + (62 if sub else 46)
        bar_w = int((x1 - x0 - 84 - 28) * val / vmax)
        draw.rounded_rectangle([tx, bar_y, x1 - 28, bar_y + 6], radius=3, fill=DIM)
        draw.rounded_rectangle([tx, bar_y, tx + max(bar_w, 8), bar_y + 6], radius=3, fill=color)
        y += row_h
    return y1 + GAP


def _finish(img: Image.Image, footer_y: int, text: str) -> io.BytesIO:
    d = ImageDraw.Draw(img)
    _text(d, W // 2, footer_y, text, 18, DIM, align="m")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=93, optimize=True)
    buf.seek(0)
    return buf


def _fmt_time(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}ч {m:02d}м" if h else f"{m}м"


def render_stats(st: ListeningStats) -> io.BytesIO:
    rows_h = lambda n: 84 + 70 * n + 20
    height = (
        PAD + 96 + 140 + GAP + 360 + GAP + 330 + GAP
        + rows_h(len(st.top_artists)) + GAP + rows_h(len(st.top_tracks)) + GAP + 70
    )
    img, d = _canvas(height)
    y = _header(d, "LISTENING STATS", f"{st.name}  ·  последние {st.days} дн.")

    y = _kpi_row(
        d,
        y,
        [
            (f"{st.total:,}".replace(",", " "), "скробблов", GREEN),
            (_fmt_time(st.minutes), "времени (≈)", PURPLE),
            (str(st.unique_artists), "артистов", PINK),
            (f"{st.streak} дн.", "серия подряд", AMBER),
        ],
    )

    n = len(st.per_day)
    every = 1 if n <= 14 else (2 if n <= 22 else 3)
    y = _bar_chart(
        d,
        (PAD, y, W - PAD, y + 360),
        [v for _, v in st.per_day],
        [l for l, _ in st.per_day],
        GREEN,
        "ПО ДНЯМ",
        f"рекорд: {st.best_day[1]} ({st.best_day[0]})",
        show_values=n <= 21,
        label_every=every,
    )

    peak = max(range(24), key=lambda h: st.per_hour[h]) if st.total else 0
    y = _bar_chart(
        d,
        (PAD, y, W - PAD, y + 330),
        st.per_hour,
        [f"{h:02d}" for h in range(24)],
        PURPLE,
        "ПО ЧАСАМ",
        f"пик: {peak:02d}:00" if st.total else "",
        show_values=False,
        label_every=3,
    )

    y = _rank_rows(
        d,
        (PAD, y, W - PAD, y + rows_h(len(st.top_artists))),
        "ТОП АРТИСТОВ",
        st.top_artists,
        PINK,
    )
    y = _rank_rows(
        d,
        (PAD, y, W - PAD, y + rows_h(len(st.top_tracks))),
        "ТОП ТРЕКОВ",
        st.top_tracks,
        GREEN,
    )
    foot = "LadsJam · Last.fm"
    if st.truncated:
        foot += " · показаны последние скробблы (лимит выборки)"
    return _finish(img, y + 10, foot)


def render_rows(title: str, subtitle: str, rows: Sequence[Row], color=GREEN, unit_note: str = "") -> io.BytesIO:
    """Generic leaderboard / top list image."""
    rows = list(rows)[:15]
    card_h = 84 + 70 * len(rows) + 20
    height = PAD + 96 + card_h + GAP + 70
    img, d = _canvas(height)
    y = _header(d, title, subtitle)
    y = _rank_rows(d, (PAD, y, W - PAD, y + card_h), unit_note or "РЕЙТИНГ", rows, color)
    return _finish(img, y + 10, "LadsJam · Last.fm")


def _tile(cover: Optional[bytes], size: int, name: str, artist: str, plays: int, rank: int) -> Image.Image:
    tile = None
    if cover:
        try:
            with Image.open(io.BytesIO(cover)) as im:
                im = im.convert("RGBA")
                m = min(im.size)
                im = im.crop(((im.width - m) // 2, (im.height - m) // 2,
                              (im.width + m) // 2, (im.height + m) // 2))
                tile = im.resize((size, size), Image.Resampling.LANCZOS)
        except Exception:
            tile = None
    if tile is None:
        tile = Image.new("RGBA", (size, size), CARD + (255,))
        td = ImageDraw.Draw(tile)
        _text(td, size // 2, size // 2, "♫", size // 4, DIM, align="m")

    grad_h = int(size * 0.5)
    grad = Image.new("RGBA", (size, grad_h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for yy in range(grad_h):
        gd.line([(0, yy), (size, yy)], fill=(6, 7, 11, int(240 * (yy / grad_h) ** 1.5)))
    tile.alpha_composite(grad, (0, size - grad_h))

    td = ImageDraw.Draw(tile)
    p = int(size * 0.05)
    # rank badge + plays pill
    td.rounded_rectangle([p, p, p + 50, p + 40], radius=20, fill=(8, 9, 14, 190))
    _text(td, p + 25, p + 20, f"#{rank}", 19, TEXT, display=True, align="m")
    pill = f"{plays}"
    pw = int(_measure_hybrid_text(pill, *_fonts(19, True))) + 36
    td.rounded_rectangle([size - p - pw, p, size - p, p + 40], radius=20, fill=GREEN + (255,))
    _text(td, size - p - pw // 2, p + 20, pill, 18, (6, 20, 10), display=True, align="m")

    _text(td, p, size - int(size * 0.12), name, int(size * 0.062), TEXT, max_w=size - 2 * p)
    _text(td, p, size - int(size * 0.055), artist, int(size * 0.05), (190, 196, 214), max_w=size - 2 * p)
    return _round_corners(tile, 24)


def render_album_grid(
    title: str, subtitle: str, items: Sequence[dict], covers: Sequence[Optional[bytes]]
) -> io.BytesIO:
    items = list(items)[:9]
    n = len(items)
    cols = 3 if n > 4 else 2 if n > 1 else 1
    rows = math.ceil(n / cols)
    inner = W - 2 * PAD - GAP * (cols - 1)
    size = inner // cols
    height = PAD + 96 + rows * size + (rows - 1) * GAP + GAP + 70
    img, d = _canvas(height)
    y0 = _header(d, title, subtitle)
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        tile = _tile(covers[i], size, it["name"], it["artist"], it["plays"], i + 1)
        img.alpha_composite(tile, (PAD + c * (size + GAP), y0 + r * (size + GAP)))
    return _finish(img, height - 40, "LadsJam · Last.fm")
