import time
import unittest

from PIL import Image

from bot.infographic import render_album_grid, render_stats
from bot.stats import build_stats


class CompactLayoutTests(unittest.TestCase):
    def assert_compact(self, buf):
        with Image.open(buf) as image:
            self.assertEqual(image.width, 1620)
            self.assertLessEqual(image.height / image.width, 1.8)
            image.load()

    def test_stats_periods_and_long_names(self):
        now = int(time.time())
        tracks = [
            {"ts": now - i * 3600, "artist": f"Очень длинное имя артиста {i % 5}",
             "track": f"Очень длинное название композиции {i % 5}"}
            for i in range(200)
        ]
        for days in (1, 7, 30):
            with self.subTest(days=days):
                stats = build_stats("Очень длинное имя пользователя", days, tracks, truncated=True)
                self.assertEqual(len(stats.top_artists), 5)
                self.assertEqual(len(stats.top_tracks), 5)
                self.assert_compact(render_stats(stats))

    def test_stats_sparse_and_empty(self):
        now = int(time.time())
        for tracks in ([], [{"ts": now, "artist": "Artist", "track": "Track"}]):
            with self.subTest(count=len(tracks)):
                self.assert_compact(render_stats(build_stats("listener", 7, tracks)))

    def test_album_grid_counts_and_missing_covers(self):
        for count in (0, 1, 2, 3, 4, 5, 9):
            with self.subTest(count=count):
                albums = [
                    {"name": f"Очень длинное название альбома {i}", "artist": "Artist", "plays": 1000 - i}
                    for i in range(count)
                ]
                # Missing artwork must not change grid geometry or prevent rendering.
                self.assert_compact(render_album_grid("ТОП АЛЬБОМОВ", "listener · месяц", albums, []))
