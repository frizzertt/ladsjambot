import io
import unittest
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote, urlparse

from aiogram.utils.formatting import Bold, Italic, Text, TextLink

from bot.formatting import now_playing_html_chunks, spotify_search_url
from bot.handlers.group import cmd_now_playing
from bot.lastfm import TrackInfo


class ParsedHTML(HTMLParser):
    """Check balanced formatting and inspect the visible text and hyperlink targets."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = ""
        self.links = []
        self.feed(source)
        self.close()
        if self.stack:
            raise AssertionError(f"Unclosed tags: {self.stack}")

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
        if tag == "a":
            self.links.append(dict(attrs)["href"])

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise AssertionError(f"Unbalanced closing tag: {tag}")

    def handle_data(self, data):
        self.text += data


class SpotifyLinkTests(unittest.TestCase):
    def test_search_urls_encode_metadata_as_a_single_path(self):
        for artist, track in [("Drake", "God's Plan"), ("Kanye West", "POWER"),
                              ("AC/DC & друзья", 'Трек / "?" #1 🎧')]:
            with self.subTest(artist=artist):
                url = spotify_search_url(artist, track)
                parsed = urlparse(url)
                self.assertEqual(parsed.scheme, "https")
                self.assertEqual(parsed.netloc, "open.spotify.com")
                self.assertEqual(parsed.query, "")
                self.assertEqual(parsed.fragment, "")
                self.assertEqual(unquote(parsed.path.removeprefix("/search/")), f"{artist} {track}")
                self.assertEqual(parsed.path.count("/"), 2)

    def assert_chunks(self, content):
        chunks = now_playing_html_chunks(content)
        parsed = [ParsedHTML(chunk) for chunk in chunks]
        self.assertEqual("".join(p.text for p in parsed), content.render()[0])
        for i, part in enumerate(parsed):
            size = len(part.text.encode("utf-16-le")) // 2
            self.assertLessEqual(size, 1024 if i == 0 else 4096)
        return chunks, parsed

    def test_long_link_markup_does_not_consume_caption_limit(self):
        url = spotify_search_url("Исполнитель" * 30, "Трек" * 30)
        content = Text(Bold("alice"), " — ", TextLink('Трек <live> & "mix"', url=url))
        chunks, parsed = self.assert_chunks(content)
        self.assertEqual(len(chunks), 1)
        self.assertGreater(len(chunks[0]), 1024)
        self.assertEqual(parsed[0].links, [url])

    def test_large_group_preserves_every_link(self):
        parts = [Text("🎧 ", Bold("Сейчас слушают:"), "\n\n")]
        urls = []
        for i in range(150):
            url = spotify_search_url("Artist", f"Track {i}")
            urls.append(url)
            parts.append(Text(Bold(f"listener_{i}"), " — ", TextLink(f"Track {i}", url=url), "\n"))
        parts.append(Italic("Не слушают: " + ", ".join(f"idle_{i}" for i in range(400))))
        chunks, parsed = self.assert_chunks(Text(*parts))
        self.assertGreater(len(chunks), 2)
        self.assertEqual([url for part in parsed for url in part.links], urls)

    def test_oversized_link_text_and_emoji_can_be_split_safely(self):
        url = spotify_search_url("Artist", "Track")
        chunks, parsed = self.assert_chunks(Text(Bold("🎧" * 5), TextLink("🎵<&>" * 1800, url=url)))
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(url in part.links for part in parsed))


class NowPlayingSpotifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_listener_has_a_link_without_mentions(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-100), from_user=None,
            bot=SimpleNamespace(send_chat_action=AsyncMock()),
            answer=AsyncMock(), answer_photo=AsyncMock(),
        )
        members = [{"tg_id": 1, "tg_name": "@alice"}, {"tg_id": 2, "tg_name": "@bob"}]
        db = SimpleNamespace(get_group_members=AsyncMock(return_value=members))
        tracks = [TrackInfo(1, "@alice", "alice_fm", "Drake", "God's Plan"),
                  TrackInfo(2, "@bob", "bob_fm", "Kanye West", "POWER")]
        lastfm = SimpleNamespace(fetch_all_now_playing=AsyncMock(return_value=tracks))
        with patch("bot.handlers.group.build_collage", return_value=io.BytesIO(b"photo")):
            await cmd_now_playing(message, db, lastfm)
        parsed = ParsedHTML(message.answer_photo.call_args.kwargs["caption"])
        self.assertEqual(parsed.links, [spotify_search_url(t.artist, t.track) for t in tracks])
        caption = message.answer_photo.call_args.kwargs["caption"]
        self.assertEqual(caption.count('>(послушать)</a>'), 2)
        self.assertIn("alice: Drake — God's Plan (послушать)", parsed.text)
        self.assertIn("bob: Kanye West — POWER (послушать)", parsed.text)
        self.assertNotIn("@", parsed.text)
        message.answer.assert_not_awaited()
