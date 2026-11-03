"""One listener's animated ten-second group statistics scene."""
import colorsys
import hashlib
from pathlib import Path
import io
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from bot.fonts import font, symbol_font, BOLD, EXTRABOLD, SEMIBOLD

class GroupStatsFrames:

    def __init__(self, username: str, artist: str, title: str, plays: int, avatar_bytes: bytes | None, cover_bytes: bytes | None, theme, size=720, *, days=7, position=1, total=1):
        if size != 720:
            raise ValueError('Groupstats renders at 720 pixels')
        size = 720
        self.username, self.artist, self.title = (username, artist, title)
        self.plays = max(0, int(plays))
        self.days, self.position, self.total = (days, position, total)
        self.entered = False
        self.next_frame = None
        self.beats = []
        self.width = self.height = size
        self.scale = size / 720
        self.theme = theme
        self.side = self._s(340)
        self.top = self._s(60)
        avatar = self._image(avatar_bytes)
        if avatar is None:
            from bot.collage import _goofy_files
            files = _goofy_files()
            if files:
                offset = int.from_bytes(hashlib.sha256(str(username).encode()).digest()[:4], 'big') % len(files)
                for path in files[offset:] + files[:offset]:
                    try:
                        asset = Path(path)
                        if asset.stat().st_size > 5 * 1024 * 1024:
                            continue
                        avatar = self._image(asset.read_bytes())
                    except OSError:
                        continue
                    if avatar is not None:
                        break
        self.accent = self._accent(avatar, theme)
        if avatar is None:
            avatar = Image.new('RGB', (self.side, self.side), theme.card)
            draw = ImageDraw.Draw(avatar)
            initials = ''.join((part[0] for part in username.strip().lstrip('@').split()[:2])).upper() or '?'
            text, face = self._fit(initials, self._s(112), EXTRABOLD, self.side * 0.8)
            draw.text((self.side / 2, self.side / 2), text, font=face, fill=theme.ink, anchor='mm')
        cover = self._image(cover_bytes)
        if cover is None:
            cover = Image.new('RGB', (self.side, self.side), theme.card)
            ImageDraw.Draw(cover).text((self.side / 2, self.side / 2), '♪', font=symbol_font(self._s(120)), fill=theme.muted, anchor='mm')
        self.avatar = self._rounded(avatar)
        self.cover = self._rounded(cover)
        self.background = Image.new('RGB', (size, size), theme.bg)
        glow = Image.new('RGBA', (size, size))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((self._s(130), self._s(20), self._s(590), self._s(480)), fill=(*self.accent, 35))
        glow = glow.filter(ImageFilter.GaussianBlur(self._s(90)))
        self.background = Image.alpha_composite(self.background.convert('RGBA'), glow).convert('RGB')
        self.name = self._text_layer([(username or '?', 38, EXTRABOLD, theme.ink)])
        self.track = self._text_layer([(f'{artist} — {title}', 32, EXTRABOLD, theme.ink), (self._plays(plays), 22, SEMIBOLD, theme.muted)])
        self.name_blur = self.name.filter(ImageFilter.GaussianBlur(self._s(8)))
        self.track_blur = self.track.filter(ImageFilter.GaussianBlur(self._s(8)))
        self.intro = self._text_layer([(username, 34, EXTRABOLD, theme.ink), (f'Главный трек за {days} дн.', 19, SEMIBOLD, theme.muted)])
        self.halo = Image.new('RGBA', (720, 720))
        ImageDraw.Draw(self.halo).rounded_rectangle((145, 55, 575, 475), radius=80, fill=(*self.accent, 210))
        self.halo = self.halo.filter(ImageFilter.GaussianBlur(60))
        self.inner_halo = Image.new('RGBA', (720, 720))
        ImageDraw.Draw(self.inner_halo).rounded_rectangle((165, 65, 555, 455), radius=50, fill=(*self.accent, 180))
        self.inner_halo = self.inner_halo.filter(ImageFilter.GaussianBlur(26))

    def prepare_audio(self, pcm):
        """Find sparse energy onsets in the 16 kHz excerpt, outside the frame loop."""
        audio = np.asarray(pcm, dtype=np.float32).reshape(-1)
        self.beats = []
        if len(audio) < 960:
            return
        blocks = audio[:len(audio) // 320 * 320].reshape(-1, 320)
        rms = np.sqrt(np.mean(blocks ** 2, axis=1) + 1e-10)
        rise = np.maximum(0, rms - np.roll(rms, 2))
        threshold = np.percentile(rise, 85)
        for k in range(2, len(rise) - 1):
            t = k * 0.02
            if t > 1.8 and rise[k] > threshold and (rise[k] >= rise[k - 1]) and (rise[k] > rise[k + 1]) and (not self.beats or t - self.beats[-1] > 0.55):
                self.beats.append(t)

    def _s(self, value):
        return max(1, round(value * self.scale))

    def _image(self, raw):
        if not raw:
            return None
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.width * image.height > 16000000:
                    return None
                image.thumbnail((self.side * 2, self.side * 2))
                image = ImageOps.exif_transpose(image).convert('RGB')
                return ImageOps.fit(image, (self.side, self.side), method=Image.Resampling.LANCZOS)
        except (OSError, ValueError, Image.DecompressionBombError):
            return None

    @staticmethod
    def _accent(image, theme):
        if image is None:
            return theme.palette[0] if theme.palette else theme.muted
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in image.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))))
        return tuple((round(c * 255) for c in colorsys.hsv_to_rgb(h, max(0.4, s), max(0.7, v))))

    def _rounded(self, image):
        mask = Image.new('L', image.size)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=self._s(24), fill=255)
        result = image.convert('RGBA')
        result.putalpha(mask)
        return result

    @staticmethod
    def _fit(text, size, weight, width):
        text = str(text or '?').replace('\n', ' ')
        for step in range(7):
            face = font(max(8, round(size * (1 - 0.06 * step))), weight)
            if face.getlength(text) <= width:
                return (text, face)
        while len(text) > 1 and face.getlength(text + '…') > width:
            text = text[:-1]
        return (text.rstrip() + '…', face)

    @staticmethod
    def _plays(value):
        n = max(0, int(value))
        ending = 'прослушивание' if n % 10 == 1 and n % 100 != 11 else 'прослушивания' if 2 <= n % 10 <= 4 and (not 12 <= n % 100 <= 14) else 'прослушиваний'
        return f'{n} {ending}'

    def _text_layer(self, lines):
        layer = Image.new('RGBA', (self.width, self._s(110)))
        draw = ImageDraw.Draw(layer)
        y = self._s(7)
        for text, size, weight, colour in lines:
            text, face = self._fit(text, self._s(size), weight, self.width - self._s(72))
            draw.text((self.width / 2, y), text, font=face, fill=colour, anchor='mt')
            y += face.size + self._s(12)
        return layer

    @staticmethod
    def _ease(value):
        p = min(1.0, max(0.0, value))
        return p * p * (3 - 2 * p)

    @staticmethod
    def _paste(canvas, image, position, alpha=1.0):
        if alpha <= 0:
            return
        if alpha < 1:
            image = image.copy()
            image.putalpha(image.getchannel('A').point(lambda value: round(value * alpha)))
        canvas.paste(image, position, image)

    def motion(self, index, t, seconds, previous, bars, fps, samples=4, came_from=None):
        v = np.asarray(bars).flatten()
        energy = float(np.mean(np.clip(v, 0, 1))) if len(v) else 0
        flip = self._ease((t - 1.05) / 0.65)
        p = min(1, t / 0.65)
        entry = 1 + 2.70158 * (p - 1) ** 3 + 1.70158 * (p - 1) ** 2
        entry = 1 if self.entered else entry
        exitp = self._ease((t - 9.5) / 0.5)
        dx = round(-600 * (1 - entry) + 740 * exitp)
        result = self.background.copy()
        d = ImageDraw.Draw(result)
        d.text((40, 30), 'LadsJam', font=font(22, EXTRABOLD), fill=self.theme.ink)
        d.text((680, 32), f'{self.position:02} / {self.total:02}', font=font(18, SEMIBOLD), fill=self.accent, anchor='rt')
        d.rounded_rectangle((40, 675, 680, 679), radius=2, fill=self.theme.line)
        d.rounded_rectangle((40, 675, 40 + int(640 * t / 10), 679), radius=2, fill=self.accent)
        layer = Image.new('RGBA', (720, 720))
        draw = ImageDraw.Draw(layer)
        beat = 0.0
        for onset in self.beats:
            age = t - onset
            if 0 <= age < 0.42:
                beat = max(beat, (1 - math.exp(-age / 0.025)) * math.exp(-age / 0.12))
        reveal = max(0, t - 1.7)
        settle = 0.024 * math.sin(min(1, reveal / 0.45) * math.pi) * math.exp(-reveal * 3) if t >= 1.7 else 0
        pulse = 1 + 0.04 * min(1, reveal / 7.8) + 0.018 * beat * flip + settle
        side = round(340 * pulse)
        w = max(1, round(side * abs(math.cos(math.pi * flip))))
        card = (self.avatar if flip < 0.5 else self.cover).resize((w, side), Image.Resampling.BICUBIC)
        x = (720 - card.width) // 2
        y = 90 + (340 - card.height) // 2
        self._paste(layer, self.halo, (0, 0), min(1.0, 0.16 + energy * 1.5 + beat * 0.32))
        self._paste(layer, self.inner_halo, (0, 0), min(1.0, energy * 1.35 + beat * 0.4))
        draw.rounded_rectangle((178, 78, 542, 442), radius=28, outline=(*self.accent, int(35 + 70 * beat)), width=2)
        if t < 0.65:
            for shift, alpha in [(40, 0.08), (23, 0.12)]:
                self._paste(layer, card, (x - shift, y), alpha)
        self._paste(layer, card, (x, y))
        name = self.intro
        self._paste(layer, name, (0, 470 - int(18 * flip)), 1 - flip)
        if flip:
            textlayer = Image.new('RGBA', (720, 180))
            td = ImageDraw.Draw(textlayer)
            artist, artist_face = self._fit(self.artist.upper(), 19, SEMIBOLD, 650)
            td.text((360, 0), artist, font=artist_face, fill=self.accent, anchor='mt')
            title, face = self._fit(self.title, 48, EXTRABOLD, 650)
            td.text((360, 30), title, font=face, fill=self.theme.ink, anchor='mt')
            n = round(self.plays * self._ease((t - 1.5) / 0.65))
            td.text((360, 88), self._plays(n), font=font(20, SEMIBOLD), fill=self.theme.muted, anchor='mt')
            self._paste(layer, textlayer, (0, 465 + int(15 * (1 - flip))), flip)
        for j in range(28):
            val = float(v[j % len(v)]) if len(v) else 0
            h = 6 + int(55 * min(1, val * 1.6))
            xx = 137 + j * 16
            draw.rounded_rectangle((xx, 625 - h // 2, xx + 9, 625 + h // 2), radius=4, fill=(*self.accent, int(255 * flip)))
        self._paste(result, layer, (dx, 0))
        if exitp and self.next_frame:
            nx = int(-530 + 720 * exitp)
            self._paste(result, self.next_frame.avatar, (nx, 90))
            intro = self.next_frame.intro
            self._paste(result, intro, (nx - 190, 470), exitp)
        return result
