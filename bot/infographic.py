"""
Pillow renderers for stats images - phone-first.

Design: loud, colourful "poster" look. Full-bleed cover mosaic hero, heavy Montserrat type,
big numbers, per-image palette taken from the covers, glow blobs behind everything.
Logical width is 1080 (text sizes are chosen so it stays readable at ~390pt phone width).
"""
import io
import math
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from bot import fonts
from bot.collage import _dominant
from bot.stats import ListeningStats

K = 1.5  # render scale (keeps width+height under Telegram's 10000px photo limit)
W = 1080
PAD = 48

BG = (11, 9, 20)
INK = (255, 255, 255)
MUTED = (178, 174, 200)
CARD = (27, 23, 46)
LINE = (52, 46, 80)

DEFAULT_PALETTE = [(255, 61, 129), (255, 214, 10), (0, 229, 255), (150, 100, 255)]

Row = Tuple[str, str, int]  # label, sublabel, value


def _open(cover: Optional[bytes]) -> Optional[Image.Image]:
    if not cover:
        return None
    try:
        with Image.open(io.BytesIO(cover)) as im:
            im = im.convert("RGB")
        m = min(im.size)
        return im.crop(((im.width - m) // 2, (im.height - m) // 2, (im.width + m) // 2, (im.height + m) // 2))
    except Exception:
        return None


def _palette(covers: Sequence[Optional[bytes]]) -> List[Tuple[int, int, int]]:
    cols: List[Tuple[int, int, int]] = []
    for c in covers:
        im = _open(c)
        if not im:
            continue
        col = _dominant(im)
        if all(sum(abs(a - b) for a, b in zip(col, o)) > 140 for o in cols):
            cols.append(col)
        if len(cols) == 4:
            break
    for d in DEFAULT_PALETTE:
        if len(cols) >= 4:
            break
        cols.append(d)
    return cols


class Sheet:
    """Logical-coordinate drawing surface (all args in 1080-wide units)."""

    def __init__(self, height: int, palette: Sequence[Tuple[int, int, int]]):
        self.h = height
        self.pal = list(palette)
        self.img = self._background(height)
        self.d = ImageDraw.Draw(self.img)

    def _background(self, height: int) -> Image.Image:
        w, h = int(W * K), int(height * K)
        base = Image.new("RGB", (w, h), BG)
        glow = Image.new("RGB", (w, h), BG)
        gd = ImageDraw.Draw(glow)
        step = int(1000 * K)
        for i, y in enumerate(range(int(500 * K), h + step, step)):
            col = self.pal[i % len(self.pal)]
            x = int((0.1 if i % 2 else 0.9) * w)
            r = int(520 * K)
            gd.ellipse([x - r, y - r, x + r, y + r], fill=col)
        glow = glow.filter(ImageFilter.GaussianBlur(int(230 * K)))
        return Image.blend(base, glow, 0.30)

    # primitives -----------------------------------------------------------
    def _b(self, x0, y0, x1, y1):
        return [int(x0 * K), int(y0 * K), int(x1 * K) - 1, int(y1 * K) - 1]

    def rect(self, x0, y0, x1, y1, fill=None):
        self.d.rectangle(self._b(x0, y0, x1, y1), fill=fill)

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=2):
        self.d.rounded_rectangle(self._b(x0, y0, x1, y1), radius=int(r * K), fill=fill,
                                 outline=outline, width=int(width * K))

    def paste(self, im: Image.Image, x, y, size=None, radius=0):
        if size:
            im = im.resize((int(size * K), int(size * K)), Image.Resampling.LANCZOS)
        im = im.convert("RGB")
        if radius:
            m = Image.new("L", (im.width * 2, im.height * 2), 0)
            ImageDraw.Draw(m).rounded_rectangle([0, 0, m.width - 1, m.height - 1], radius=int(radius * K * 2), fill=255)
            self.img.paste(im, (int(x * K), int(y * K)), m.resize(im.size, Image.Resampling.LANCZOS))
        else:
            self.img.paste(im, (int(x * K), int(y * K)))
        self.d = ImageDraw.Draw(self.img)

    # text -----------------------------------------------------------------
    def measure(self, s: str, size: float, weight=fonts.BOLD) -> float:
        return fonts.font(max(8, int(size * K)), weight).getlength(s) / K

    def text(self, x, y, s, size, fill=INK, weight=fonts.BOLD, align="l", max_w=None) -> float:
        """Draw text with vertical centre at y. Returns drawn width (logical)."""
        s = str(s)
        if max_w and self.measure(s, size, weight) > max_w:
            while s and self.measure(s + "…", size, weight) > max_w:
                s = s[:-1]
            s = s.rstrip() + "…"
        w = self.measure(s, size, weight)
        if align == "r":
            x -= w
        elif align == "m":
            x -= w / 2
        self.d.text((x * K, y * K), s, font=fonts.font(max(8, int(size * K)), weight), fill=fill, anchor="lm")
        return w

    def label(self, x, y, s, fill=MUTED, align="l", size=24):
        return self.text(x, y, s.upper(), size, fill, fonts.SEMIBOLD, align)

    def export(self, quality=92) -> io.BytesIO:
        buf = io.BytesIO()
        self.img.save(buf, format="JPEG", quality=quality, subsampling=0, optimize=True)
        buf.seek(0)
        return buf


# ---- shared blocks ---------------------------------------------------------
def _hero(sh: Sheet, covers: Sequence[Optional[bytes]], title: str, subtitle: str, tag: str, h: int = 520) -> int:
    """Full-bleed cover mosaic with huge title over a dark gradient."""
    imgs = [i for i in (_open(c) for c in covers) if i]
    hw, hh = int(W * K), int(h * K)
    hero = Image.new("RGB", (hw, hh), (20, 16, 36))
    if imgs:
        cols = 4 if len(imgs) >= 8 else 3 if len(imgs) >= 4 else 2
        tile = math.ceil(hw / cols)
        rows = math.ceil(hh / tile) + 1
        for i in range(cols * rows):
            t = imgs[i % len(imgs)].resize((tile, tile), Image.Resampling.LANCZOS)
            hero.paste(t, ((i % cols) * tile, (i // cols) * tile))
        hero = ImageEnhance.Color(hero).enhance(1.25)
    else:
        d = ImageDraw.Draw(hero)
        for i, col in enumerate(sh.pal):
            cx, cy, r = int(hw * (0.15 + 0.25 * i)), int(hh * (0.3 + 0.35 * (i % 2))), int(hh * 0.55)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
        hero = hero.filter(ImageFilter.GaussianBlur(int(60 * K)))
    grad = Image.new("RGBA", (hw, hh), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(hh):
        p = y / hh
        gd.line([(0, y), (hw, y)], fill=(11, 9, 20, int(250 * p ** 1.1 + 40 * (1 - p))))
    hero = Image.alpha_composite(hero.convert("RGBA"), grad).convert("RGB")
    sh.img.paste(hero, (0, 0))
    sh.d = ImageDraw.Draw(sh.img)

    # tag chip
    acc = sh.pal[0]
    tw = sh.measure(tag.upper(), 26, fonts.EXTRABOLD)
    sh.rrect(PAD, 44, PAD + tw + 48, 100, 28, fill=acc)
    sh.text(PAD + 24, 72, tag.upper(), 26, (12, 12, 16), fonts.EXTRABOLD)
    sh.text(W - PAD, 72, "LadsJam", 30, INK, fonts.BLACK, align="r")

    size = 96
    while size > 48 and sh.measure(title, size, fonts.BLACK) > W - 2 * PAD:
        size -= 4
    sh.text(PAD, h - 108, title, size, INK, fonts.BLACK, max_w=W - 2 * PAD)
    sh.text(PAD, h - 48, subtitle, 34, sh.pal[1], fonts.BOLD, max_w=W - 2 * PAD)
    return h + 36


def _section(sh: Sheet, y: int, num: str, title: str, note: str = "") -> int:
    col = sh.pal[(int(num) - 1) % len(sh.pal)] if num.isdigit() else sh.pal[0]
    sh.rrect(PAD, y, PAD + 72, y + 56, 28, fill=col)
    sh.text(PAD + 36, y + 28, num, 28, (12, 12, 16), fonts.BLACK, align="m")
    sh.text(PAD + 92, y + 28, title.upper(), 38, INK, fonts.BLACK)
    if note:
        sh.text(W - PAD, y + 28, note, 26, MUTED, fonts.SEMIBOLD, align="r")
    return y + 88


def _footer(sh: Sheet, y: int, extra: str = "") -> None:
    sh.text(PAD, y + 30, "LadsJam · Last.fm", 26, MUTED, fonts.BOLD)
    if extra:
        sh.text(W - PAD, y + 30, extra, 26, MUTED, fonts.BOLD, align="r")


def _kpis(sh: Sheet, y: int, items: Sequence[Tuple[str, str]]) -> int:
    """2x2 grid of big colourful stat cards."""
    gap = 20
    cw = (W - 2 * PAD - gap) // 2
    ch = 190
    for i, (value, label) in enumerate(items):
        r, c = divmod(i, 2)
        x, yy = PAD + c * (cw + gap), y + r * (ch + gap)
        col = sh.pal[i % len(sh.pal)]
        sh.rrect(x, yy, x + cw, yy + ch, 36, fill=col)
        size = 84
        while size > 36 and sh.measure(value, size, fonts.BLACK) > cw - 56:
            size -= 4
        sh.text(x + 28, yy + 84, value, size, (12, 12, 16), fonts.BLACK)
        sh.text(x + 28, yy + 148, label.upper(), 26, (12, 12, 16), fonts.EXTRABOLD)
    rows = math.ceil(len(items) / 2)
    return y + rows * (ch + gap)


def _bars(sh: Sheet, y: int, h: int, values: Sequence[int], labels: Sequence[str],
          show_values: bool, label_every: int = 1) -> int:
    x0, x1 = PAD, W - PAD
    sh.rrect(x0 - 12, y, x1 + 12, y + h, 36, fill=CARD)
    top, bottom = y + 60, y + h - 56
    vmax = max(max(values, default=0), 1)
    n = max(1, len(values))
    slot = (x1 - x0 - 24) / n
    bw = max(8, slot * (0.62 if n <= 14 else 0.72))
    imax = values.index(max(values)) if any(values) else -1
    for i, v in enumerate(values):
        cx = x0 + 12 + slot * i + slot / 2
        if v > 0:
            bh = max(10, (bottom - top) * v / vmax)
            col = sh.pal[1] if i == imax else sh.pal[0]
            sh.rrect(cx - bw / 2, bottom - bh, cx + bw / 2, bottom, min(10, bw / 2), fill=col)
            if show_values:
                sh.text(cx, bottom - bh - 20, v, 24, INK, fonts.BOLD, align="m")
        else:
            sh.rrect(cx - bw / 2, bottom - 6, cx + bw / 2, bottom, 3, fill=LINE)
        if i % label_every == 0:
            sh.text(cx, bottom + 30, labels[i], 24, MUTED, fonts.SEMIBOLD, align="m")
    return y + h


def _rank_list(sh: Sheet, y: int, rows: Sequence[Row], unit: str = "") -> int:
    vmax = max([r[2] for r in rows], default=1) or 1
    for i, (label, sub, val) in enumerate(rows):
        row_h = 128 if sub else 108
        col = sh.pal[i % len(sh.pal)]
        sh.rrect(PAD - 12, y, W - PAD + 12, y + row_h - 14, 30, fill=CARD)
        # proportional fill strip along the bottom
        bw = int((W - 2 * PAD - 24) * val / vmax)
        sh.rrect(PAD, y + row_h - 26, PAD + max(bw, 12), y + row_h - 20, 3, fill=col)
        cy = y + (row_h - 26) / 2
        sh.rrect(PAD, cy - 30, PAD + 60, cy + 30, 30, fill=col)
        sh.text(PAD + 30, cy, f"{i + 1}", 30, (12, 12, 16), fonts.BLACK, align="m")
        tx = PAD + 84
        vtxt = f"{val:,}".replace(",", " ")
        vw = sh.measure(vtxt, 36, fonts.BLACK)
        maxw = W - PAD - tx - vw - 28
        if sub:
            sh.text(tx, cy - 20, label, 36, INK, fonts.EXTRABOLD, max_w=maxw)
            sh.text(tx, cy + 22, sub, 28, MUTED, fonts.SEMIBOLD, max_w=maxw)
        else:
            sh.text(tx, cy, label, 36, INK, fonts.EXTRABOLD, max_w=maxw)
        sh.text(W - PAD - 12, cy, vtxt, 36, col, fonts.BLACK, align="r")
        y += row_h
    return y


def _fmt_time(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}ч {m:02d}м" if h else f"{m}м"


def _rows_h(n: int, sub: bool) -> int:
    return n * (128 if sub else 108)


# ---- public renderers --------------------------------------------------------
def render_stats(st: ListeningStats, covers: Sequence[Optional[bytes]] = ()) -> io.BytesIO:
    n_a, n_t = len(st.top_artists), len(st.top_tracks)
    hero_h = 520
    height = (hero_h + 36) + 20 + 2 * 210 + 40 + (88 + 380) + 40 + (88 + 340) + 40 \
        + (88 + _rows_h(n_a, False)) + 40 + (88 + _rows_h(n_t, True)) + 40 + 70
    sh = Sheet(height, _palette(covers))
    y = _hero(sh, covers, st.name.upper(), f"последние {st.days} дн.", "listening report", hero_h)
    y = _kpis(
        sh, y + 20,
        [
            (f"{st.total:,}".replace(",", " "), "скробблов"),
            (_fmt_time(st.minutes), "время ≈"),
            (str(st.unique_artists), "артистов"),
            (f"{st.streak} дн.", "серия"),
        ],
    ) + 40

    n = len(st.per_day)
    y = _section(sh, y, "1", "по дням", f"рекорд {st.best_day[1]} · {st.best_day[0]}")
    y = _bars(sh, y, 380, [v for _, v in st.per_day], [l for l, _ in st.per_day],
              show_values=n <= 10, label_every=1 if n <= 8 else (2 if n <= 16 else 5)) + 40

    peak = max(range(24), key=lambda h: st.per_hour[h]) if st.total else 0
    y = _section(sh, y, "2", "по часам", f"пик {peak:02d}:00")
    y = _bars(sh, y, 340, st.per_hour, [f"{h:02d}" for h in range(24)], False, 4) + 40

    y = _section(sh, y, "3", "топ артистов")
    y = _rank_list(sh, y, st.top_artists) + 40
    y = _section(sh, y, "4", "топ треков")
    y = _rank_list(sh, y, st.top_tracks)
    _footer(sh, y + 24, "выборка ограничена" if st.truncated else "")
    return sh.export()


def render_rows(title: str, subtitle: str, rows: Sequence[Row], unit_note: str = "", tag: str = "chart",
                covers: Sequence[Optional[bytes]] = ()) -> io.BytesIO:
    rows = list(rows)[:15]
    has_sub = any(r[1] for r in rows)
    hero_h = 460
    height = hero_h + 36 + 20 + 88 + _rows_h(len(rows), has_sub) + 100
    sh = Sheet(height, _palette(covers))
    y = _hero(sh, covers, title, subtitle, tag, hero_h)
    y = _section(sh, y + 20, "1", unit_note or "рейтинг")
    y = _rank_list(sh, y, rows)
    _footer(sh, y + 24)
    return sh.export()


def render_album_grid(
    title: str, subtitle: str, items: Sequence[dict], covers: Sequence[Optional[bytes]]
) -> io.BytesIO:
    items = list(items)[:9]
    covers = list(covers)
    n = len(items)
    cols = 2 if n > 1 else 1
    rows = math.ceil(n / cols)
    gap = 24
    size = (W - 2 * PAD - gap * (cols - 1)) // cols
    cap_h = 140
    cell_h = size + cap_h
    hero_h = 460
    height = hero_h + 36 + 20 + rows * cell_h + 100
    sh = Sheet(height, _palette(covers))
    y0 = _hero(sh, covers, title, subtitle, "top albums", hero_h) + 20
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        x = PAD + c * (size + gap)
        y = y0 + r * cell_h
        col = sh.pal[i % len(sh.pal)]
        im = _open(covers[i]) or Image.new("RGB", (size, size), CARD)
        sh.paste(im, x, y, size=size, radius=32)
        sh.rrect(x + 16, y + 16, x + 16 + 76, y + 16 + 76, 38, fill=col)
        sh.text(x + 54, y + 54, f"{i + 1}", 36, (12, 12, 16), fonts.BLACK, align="m")
        pl = f"{it['plays']}"
        pw = sh.measure(pl, 30, fonts.BLACK) + 40
        sh.rrect(x + size - 16 - pw, y + size - 76, x + size - 16, y + size - 16, 30, fill=(12, 12, 16))
        sh.text(x + size - 16 - pw / 2, y + size - 46, pl, 30, col, fonts.BLACK, align="m")
        sh.text(x + 4, y + size + 36, it["name"], 32, INK, fonts.EXTRABOLD, max_w=size - 8)
        sh.text(x + 4, y + size + 80, it["artist"], 28, MUTED, fonts.SEMIBOLD, max_w=size - 8)
    _footer(sh, y0 + rows * cell_h + 10)
    return sh.export()


def render_versus(
    name_a: str, name_b: str, subtitle: str, metrics: Sequence[Tuple[str, int, int]],
    covers: Sequence[Optional[bytes]] = (),
) -> io.BytesIO:
    hero_h = 460
    height = hero_h + 36 + 20 + 190 * len(metrics) + 110
    sh = Sheet(height, _palette(covers))
    y = _hero(sh, covers, f"{name_a} vs {name_b}", subtitle, "versus", hero_h) + 20
    wins_a = sum(1 for _, a, b in metrics if a > b)
    wins_b = sum(1 for _, a, b in metrics if b > a)
    cx = W / 2
    half = cx - PAD
    ca, cb = sh.pal[0], sh.pal[2]
    for label, a, b in metrics:
        sh.rrect(PAD - 12, y, W - PAD + 12, y + 172, 34, fill=CARD)
        sh.label(cx, y + 34, label, MUTED, align="m", size=26)
        m = max(a, b, 1)
        by = y + 118
        wa, wb = int((half - 150) * a / m), int((half - 150) * b / m)
        sh.rrect(cx - 14 - max(wa, 10), by - 14, cx - 14, by + 14, 14, fill=ca if a >= b else (90, 84, 120))
        sh.rrect(cx + 14, by - 14, cx + 14 + max(wb, 10), by + 14, 14, fill=cb if b >= a else (90, 84, 120))
        sh.text(PAD + 8, y + 34, f"{a:,}".replace(",", " "), 40, ca, fonts.BLACK)
        sh.text(W - PAD - 8, y + 34, f"{b:,}".replace(",", " "), 40, cb, fonts.BLACK, align="r")
        y += 190
    _footer(sh, y + 10, f"{wins_a} : {wins_b}")
    return sh.export()
