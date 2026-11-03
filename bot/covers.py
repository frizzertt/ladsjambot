"""Bounded SoundCloud artwork fallback shared by every image-producing command."""
import asyncio
import io
import json
import sys
import time
from collections import OrderedDict
from urllib.parse import urlparse

import aiohttp
from PIL import Image, ImageOps

from bot.canvas.process import MediaError, run
from bot.canvas.resolver import rank_candidate, rank_approximate

MAX_IMAGE = 5 * 1024 * 1024
_cache = OrderedDict()
_gate = asyncio.Semaphore(2)


def normalized_image(raw):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 16_000_000:
                return None
            image = ImageOps.exif_transpose(image)
            image.thumbnail((640, 640))
            out = io.BytesIO()
            image.convert('RGB').save(out, 'JPEG', quality=88)
            return out.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


async def soundcloud_cover(session, artist, title):
    if not artist or not title:
        return None
    key = (artist.casefold().strip(), title.casefold().strip())
    async with _gate:
        cached = _cache.get(key)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(key)
            return cached[1]
        result = None
        try:
            async with asyncio.timeout(35):
                base = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
                        '--no-warnings', '--socket-timeout', '8', '--retries', '0']
                raw = await run(base + ['--flat-playlist', '--dump-single-json', '--',
                                        f'scsearch5:{artist} {title}'], timeout=12)
                entries = json.loads(raw).get('entries') or []
                ranked = sorted((e for e in entries if isinstance(e, dict)),
                                key=lambda e: max(rank_candidate(artist, title, e),
                                                  rank_approximate(artist, title, e)), reverse=True)
                for entry in ranked[:2]:
                    if max(rank_candidate(artist, title, entry), rank_approximate(artist, title, entry)) < .78:
                        continue
                    url = entry.get('webpage_url') or entry.get('url') or ''
                    parsed = urlparse(url)
                    if parsed.scheme != 'https' or parsed.hostname not in {'soundcloud.com', 'www.soundcloud.com'} or parsed.username:
                        continue
                    try:
                        meta = json.loads(await run(base + ['--skip-download', '--dump-single-json', '--', url], timeout=12))
                        thumbnails = sorted(meta.get('thumbnails') or [], key=lambda t: t.get('width') or 0, reverse=True)
                        urls = [t.get('url') for t in thumbnails] + [meta.get('thumbnail')]
                        for image_url in urls:
                            p = urlparse(image_url or '')
                            if p.scheme != 'https' or not (p.hostname or '').endswith('.sndcdn.com') or p.username or p.port not in (None, 443):
                                continue
                            async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=8), allow_redirects=False) as response:
                                if response.status != 200 or (response.content_length or 0) > MAX_IMAGE:
                                    continue
                                data = bytearray()
                                async for chunk in response.content.iter_chunked(65536):
                                    data.extend(chunk)
                                    if len(data) > MAX_IMAGE:
                                        break
                                if len(data) <= MAX_IMAGE:
                                    result = normalized_image(data)
                            if result:
                                break
                    except (MediaError, TimeoutError, ValueError, aiohttp.ClientError):
                        continue
                    if result:
                        break
        except (MediaError, TimeoutError, ValueError, aiohttp.ClientError):
            pass
        _cache[key] = (time.monotonic() + (3600 if result else 120), result)
        _cache.move_to_end(key)
        while len(_cache) > 32:
            _cache.popitem(last=False)
        return result
