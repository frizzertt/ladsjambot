"""
Pillow renderers for stats images.

Design: flat, near-black "editorial" look - hairline rules, tracked uppercase labels,
big display numbers, one warm accent, light film grain. Rendered at 2x for sharpness.
"""
import io
import math
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw

from bot.collage import _get_fallback_font, _get_michroma_font, _is_cyrillic
from bot.stats import ListeningStats

K = 2  # render scale
W = 1080  # logical width
PAD = 56

BG = (13, 13, 14)
INK = (232, 230, 225)
MUTED = (122, 122, 120)
LINE = (40, 40, 42)
BAR = (196, 194, 188)
ACCENT = (227, 74, 42)

Row = Tuple[str, str, int]  # label, sublabel, value


@lru_cache(maxsize=None)
def _font(px: int, display: bool, cyr: bool):
    if display and not cyr:
        return _get_michroma_font(px)
    return _get_fallback_font(px, bold=True)


class Sheet:
    """Logical-coordinate drawing surface (all args in 1080-wide units)."""

    def __init__(self, height: int):
        self.h = height
        self.img = Image.new("RGB", (W * K, height * K), BG)
        self.d = ImageDraw.Draw(self.img)

    # primitives -----------------------------------------------------------
    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width=1):
        self.d.rectangle(
            [x0 * K, y0 * K, x1 * K - 1, y1 * K - 1], fill=fill, outline=outline, width=width * K
        )

    def hline(self, x0, x1, y, fill=LINE, width=1):
        self.d.rectangle([x0 * K, y * K, x1 * K - 1, y * K + width * K - 1], fill=fill)

    def vline(self, x, y0, y1, fill=LINE, width=1):
        self.d.rectangle([x * K, y0 * K, x * K + width * K - 1, y1 * K - 1], fill=fill)

    def paste(self, im: Image.Image, x, y):
        self.img.paste(im.convert("RGB"), (int(x * K), int(y * K)))

    # text -----------------------------------------------------------------
    def measure(self, s: str, size: float, display=False, tracking=0.0) -> float:
        px = max(6, int(size * K))
        tot = 0.0
        for ch in s:
            tot += _font(px, display, _is_cyrillic(ch)).getlength(ch) + tracking * K
        return max(0.0, tot - tracking * K) / K

    def text(self, x, y, s, size, fill=INK, display=False, align="l", tracking=0.0, max_w=None) -> float:
        """Draw text with vertical center at y. Returns drawn width (logical)."""
        s = str(s)
        if max_w and self.measure(s, size, display, tracking) > max_w:
            while s and self.measure(s + "…", size, display, tracking) > max_w:
                s = s[:-1]
            s = s.rstrip() + "…"
        w = self.measure(s, size, display, tracking)
        if align == "r":
            x -= w
        elif align == "m":
            x -= w / 2
        px = max(6, int(size * K))
        cx = x * K
        for ch in s:
            f = _font(px, display, _is_cyrillic(ch))
            self.d.text((cx, y * K), ch, font=f, fill=fill, anchor="lm")
            cx += f.getlength(ch) + tracking * K
        return w

    def label(self, x, y, s, fill=MUTED, align="l", size=12):
        return self.text(x, y, s.upper(), size, fill, display=True, align=align, tracking=2.0)

    # output ---------------------------------------------------------------
    def export(self, quality=95) -> io.BytesIO:
        # subtle film grain
        noise = Image.effect_noise(self.img.size, 22).convert("RGB")
        img = Image.blend(self.img, ImageChops.add(self.img, noise, scale=2.2, offset=-52), 0.45)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, subsampling=0, optimize=True)
        buf.seek(0)
        return buf


# ---- shared blocks ---------------------------------------------------------
def _masthead(sh: Sheet, tag: str, title: str, subtitle: str, right: str = "") -> int:
    sh.label(PAD, 44, "LadsJam", INK)
    sh.label(PAD + 96, 44, f"/ {tag}", MUTED)
    if right:
        sh.label(W - PAD, 44, right, MUTED, align="r")
    sh.hline(PAD, W - PAD, 70)
    sh.text(PAD, 128, title, 46, INK, display=True, max_w=W - 2 * PAD)
    sh.label(PAD, 176, subtitle, MUTED, size=13)
    return 206


def _section(sh: Sheet, y: int, num: str, title: str, note: str = "") -> int:
    sh.hline(PAD, W - PAD, y)
    sh.label(PAD, y + 30, f"{num}", ACCENT)
    sh.label(PAD + 44, y + 30, title, INK)
    if note:
        sh.label(W - PAD, y + 30, note, MUTED, align="r", size=11)
    return y + 64


def _footer(sh: Sheet, y: int, extra: str = "") -> None:
    sh.hline(PAD, W - PAD, y)
    sh.label(PAD, y + 28, "LadsJam · Last.fm", MUTED, size=10)
    if extra:
        sh.label(W - PAD, y + 28, extra, MUTED, align="r", size=10)


def _kpis(sh: Sheet, y: int, items: Sequence[Tuple[str, str]]) -> int:
    n = len(items)
    colw = (W - 2 * PAD) / n
    for i, (value, label) in enumerate(items):
        x = PAD + i * colw
        if i:
            sh.vline(int(x), y + 6, y + 112)
        x += 0 if i == 0 else 24
        size = 44
        while size > 20 and sh.measure(value, size, True) > colw - 36:
            size -= 2
        sh.text(x, y + 44, value, size, INK, display=True)
        sh.label(x, y + 96, label, MUTED, size=11)
    return y + 130


def _bars(
    sh: Sheet, y: int, h: int, values: Sequence[int], labels: Sequence[str],
    show_values: bool, label_every: int = 1,
) -> int:
    x0, x1 = PAD, W - PAD
    top, bottom = y + 24, y + h - 36
    vmax = max(max(values, default=0), 1)
    for frac in (0.25, 0.5, 0.75, 1.0):
        gy = int(bottom - (bottom - top) * frac)
        sh.hline(x0, x1, gy, fill=(26, 26, 28))
    n = max(1, len(values))
    slot = (x1 - x0) / n
    bw = max(3, slot * (0.56 if n <= 14 else 0.7))
    imax = values.index(max(values)) if any(values) else -1
    for i, v in enumerate(values):
        cx = x0 + slot * i + slot / 2
        if v > 0:
            bh = max(3, (bottom - top) * v / vmax)
            sh.rect(cx - bw / 2, bottom - bh, cx + bw / 2, bottom, fill=ACCENT if i == imax else BAR)
            if show_values:
                sh.text(cx, bottom - bh - 12, v, 12, INK, display=True, align="m")
        else:
            sh.rect(cx - bw / 2, bottom - 2, cx + bw / 2, bottom, fill=LINE)
        if i % label_every == 0:
            sh.text(cx, bottom + 22, labels[i], 11, MUTED, display=True, align="m")
    sh.hline(x0, x1, bottom, fill=(70, 70, 72))
    return y + h


def _rank_list(sh: Sheet, y: int, rows: Sequence[Row], unit: str = "") -> int:
    vmax = max([r[2] for r in rows], default=1) or 1
    for i, (label, sub, val) in enumerate(rows):
        row_h = 74 if sub else 62
        cy = y + row_h / 2
        sh.text(PAD, cy, f"{i + 1:02d}", 15, ACCENT if i == 0 else MUTED, display=True)
        tx = PAD + 64
        right_w = 130
        maxw = W - PAD - tx - right_w
        if sub:
            sh.text(tx, cy - 10, label, 22, INK, max_w=maxw)
            sh.text(tx, cy + 15, sub, 16, MUTED, max_w=maxw)
        else:
            sh.text(tx, cy - 4, label, 22, INK, max_w=maxw)
        sh.text(W - PAD, cy - (4 if not sub else 0), f"{val:,}".replace(",", " "), 20, INK, display=True, align="r")
        # thin proportional rule under row
        by = y + row_h - 1
        sh.hline(tx, W - PAD, by, fill=LINE)
        sh.hline(tx, tx + int((W - PAD - tx) * val / vmax), by, fill=ACCENT if i == 0 else BAR, width=1)
        y += row_h
    return y


def _fmt_time(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}ч {m:02d}м" if h else f"{m}м"


# ---- public renderers --------------------------------------------------------
def render_stats(st: ListeningStats) -> io.BytesIO:
    n_a, n_t = len(st.top_artists), len(st.top_tracks)
    rows_h = lambda rs, sub: sum(74 if sub else 62 for _ in range(rs))
    height = 206 + 130 + 30 + (64 + 300) + 30 + (64 + 260) + 30 + (64 + rows_h(n_a, False)) + 30 \
        + (64 + rows_h(n_t, True)) + 80
    sh = Sheet(height)
    y = _masthead(sh, "stats", st.name.upper(), f"последние {st.days} дн.", "listening report")
    y = _kpis(
        sh, y + 14,
        [
            (f"{st.total:,}".replace(",", " "), "скробблов"),
            (_fmt_time(st.minutes), "время ≈"),
            (str(st.unique_artists), "артистов"),
            (f"{st.streak} дн.", "серия"),
        ],
    ) + 30

    n = len(st.per_day)
    y = _section(sh, y, "01", "по дням", f"рекорд {st.best_day[1]} · {st.best_day[0]}")
    y = _bars(sh, y, 300, [v for _, v in st.per_day], [l for l, _ in st.per_day],
              show_values=n <= 21, label_every=1 if n <= 14 else (2 if n <= 22 else 3)) + 30

    peak = max(range(24), key=lambda h: st.per_hour[h]) if st.total else 0
    y = _section(sh, y, "02", "по часам", f"пик {peak:02d}:00")
    y = _bars(sh, y, 260, st.per_hour, [f"{h:02d}" for h in range(24)], False, 3) + 30

    y = _section(sh, y, "03", "топ артистов")
    y = _rank_list(sh, y, st.top_artists) + 30
    y = _section(sh, y, "04", "топ треков")
    y = _rank_list(sh, y, st.top_tracks)
    _footer(sh, y + 24, "выборка ограничена" if st.truncated else "")
    return sh.export()


def render_rows(title: str, subtitle: str, rows: Sequence[Row], unit_note: str = "", tag: str = "chart") -> io.BytesIO:
    rows = list(rows)[:15]
    has_sub = any(r[1] for r in rows)
    per = 74 if has_sub else 62
    height = 206 + 64 + per * len(rows) + 90
    sh = Sheet(height)
    y = _masthead(sh, tag, title, subtitle)
    y = _section(sh, y + 10, "01", unit_note or "рейтинг")
    y = _rank_list(sh, y, rows)
    _footer(sh, y + 24)
    return sh.export()


def _tile(cover: Optional[bytes], px: int) -> Image.Image:
    if cover:
        try:
            with Image.open(io.BytesIO(cover)) as im:
                im = im.convert("RGB")
                m = min(im.size)
                im = im.crop(((im.width - m) // 2, (im.height - m) // 2,
                              (im.width + m) // 2, (im.height + m) // 2))
                return im.resize((px, px), Image.Resampling.LANCZOS)
        except Exception:
            pass
    t = Image.new("RGB", (px, px), (24, 24, 26))
    d = ImageDraw.Draw(t)
    step = max(12, px // 14)
    for i in range(-px, px, step):
        d.line([(i, px), (i + px, 0)], fill=(33, 33, 35), width=1)
    return t


def render_album_grid(
    title: str, subtitle: str, items: Sequence[dict], covers: Sequence[Optional[bytes]]
) -> io.BytesIO:
    items = list(items)[:9]
    n = len(items)
    cols = 3 if n > 4 else 2 if n > 1 else 1
    rows = math.ceil(n / cols)
    gap = 18
    size = (W - 2 * PAD - gap * (cols - 1)) // cols
    cap_h = 78
    cell_h = size + cap_h
    height = 206 + rows * cell_h + 70
    sh = Sheet(height)
    y0 = _masthead(sh, "albums", title, subtitle, "top chart") + 10
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        x = PAD + c * (size + gap)
        y = y0 + r * cell_h
        sh.paste(_tile(covers[i], size * K), x, y)
        sh.text(x, y + size + 22, f"{i + 1:02d}", 13, ACCENT if i == 0 else MUTED, display=True)
        sh.text(x + 38, y + size + 22, it["name"], 18, INK, max_w=size - 38)
        sh.text(x + 38, y + size + 46, it["artist"], 15, MUTED, max_w=size - 38 - 70)
        sh.text(x + size, y + size + 46, f"{it['plays']}", 13, INK, display=True, align="r")
    _footer(sh, y0 + rows * cell_h + 4)
    return sh.export()


def render_versus(
    name_a: str, name_b: str, subtitle: str, metrics: Sequence[Tuple[str, int, int]]
) -> io.BytesIO:
    height = 206 + 40 + 96 * len(metrics) + 90
    sh = Sheet(height)
    y = _masthead(sh, "versus", f"{name_a}  VS  {name_b}", subtitle)
    cx = W / 2
    y += 30
    wins_a = sum(1 for _, a, b in metrics if a > b)
    wins_b = sum(1 for _, a, b in metrics if b > a)
    half = cx - PAD
    for label, a, b in metrics:
        sh.hline(PAD, W - PAD, y)
        sh.label(cx, y + 26, label, MUTED, align="m", size=11)
        m = max(a, b, 1)
        by = y + 62
        wa, wb = int(half * 0.82 * a / m), int(half * 0.82 * b / m)
        sh.rect(cx - 10 - wa, by - 5, cx - 10, by + 5, fill=ACCENT if a > b else BAR)
        sh.rect(cx + 10, by - 5, cx + 10 + wb, by + 5, fill=ACCENT if b > a else BAR)
        sh.text(PAD, by, f"{a:,}".replace(",", " "), 22, INK, display=True)
        sh.text(W - PAD, by, f"{b:,}".replace(",", " "), 22, INK, display=True, align="r")
        y += 96
    _footer(sh, y + 10, f"{wins_a} : {wins_b}")
    return sh.export()
