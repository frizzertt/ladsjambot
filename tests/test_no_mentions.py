import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.filters import CommandObject

from bot.database import Database
from bot.formatting import user_name_html
from bot.handlers.group import cmd_join, cmd_now_playing
from bot.handlers.stats import _find_target, cmd_compat, cmd_recent, cmd_whoknows
from bot.lastfm import TrackInfo


class NoMentionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup"),
            from_user=SimpleNamespace(id=1, username="alice", full_name="Alice", is_bot=False),
            reply_to_message=None,
            bot=SimpleNamespace(send_chat_action=AsyncMock()),
            answer=AsyncMock(), answer_photo=AsyncMock(), reply=AsyncMock(),
        )
        self.members = [
            {"tg_id": 1, "tg_name": "@alice", "lastfm_user": "alice_fm"},
            {"tg_id": 2, "tg_name": "Bob & @bob", "lastfm_user": "bob_fm"},
        ]
        self.db = SimpleNamespace(
            get_group_members=AsyncMock(return_value=self.members),
            add_group_member_if_registered=AsyncMock(return_value=True),
            get_user=AsyncMock(return_value=self.members[0]),
            get_user_by_name=AsyncMock(return_value=self.members[1]),
        )
        self.lastfm = SimpleNamespace(call=AsyncMock(), fetch_all_now_playing=AsyncMock(return_value=[]))

    def assert_no_mentions(self, text):
        self.assertNotIn("@", text)
        self.assertNotIn("tg://user", text)
        self.message.reply.assert_not_awaited()

    def test_name_formatting(self):
        self.assertEqual(user_name_html("@alice"), "alice")
        self.assertEqual(user_name_html("Bob & @bob <3"), "Bob &amp; bob &lt;3")
        self.assertEqual(user_name_html("Имя Фамилия"), "Имя Фамилия")

    async def test_join_keeps_stored_name(self):
        await cmd_join(self.message, self.db)
        self.assert_no_mentions(self.message.answer.call_args.args[0])
        self.db.add_group_member_if_registered.assert_awaited_once_with(
            chat_id=-100, tg_id=1, tg_name="@alice"
        )

    async def test_np_nobody_listening(self):
        await cmd_now_playing(self.message, self.db, self.lastfm)
        text = self.message.answer.call_args.args[0]
        self.assert_no_mentions(text)
        self.assertIn("alice, Bob &amp; bob", text)

    async def test_np_collage_and_idle_names(self):
        self.lastfm.fetch_all_now_playing.return_value = [TrackInfo(1, "@alice", "alice_fm", "Artist", "Track", None)]
        with patch("bot.handlers.group.build_collage", return_value=io.BytesIO(b"image")):
            await cmd_now_playing(self.message, self.db, self.lastfm)
        caption = self.message.answer_photo.call_args.kwargs["caption"]
        self.assert_no_mentions(caption)
        self.assertIn("<b>alice</b>: Artist — Track <a href=", caption)
        self.assertIn('>(послушать)</a>', caption)
        self.assertIn("Не слушают: Bob &amp; bob", caption)

    async def test_np_long_caption_continuation(self):
        self.members[:] = [
            {"tg_id": i, "tg_name": f"@listener_{i}", "lastfm_user": f"fm_{i}"}
            for i in range(60)
        ]
        self.lastfm.fetch_all_now_playing.return_value = [
            TrackInfo(m["tg_id"], m["tg_name"], m["lastfm_user"], "Artist", "Track", None)
            for m in self.members
        ]
        with patch("bot.handlers.group.build_collage", return_value=io.BytesIO(b"image")):
            await cmd_now_playing(self.message, self.db, self.lastfm)
        self.assert_no_mentions(self.message.answer_photo.call_args.kwargs["caption"])
        self.assert_no_mentions(self.message.answer.call_args.args[0])

    async def test_compat_and_username_lookup(self):
        command = CommandObject(command="compat", args="@bob")
        with patch("bot.handlers.stats.st.fetch_top", new=AsyncMock(return_value=[{"name": "Artist", "plays": 10}])):
            await cmd_compat(self.message, command, self.db, self.lastfm)
        text = self.message.answer.call_args.args[0]
        self.assert_no_mentions(text)
        self.assertIn("alice × Bob &amp; bob", text)
        self.db.get_user_by_name.assert_awaited_once_with("@bob")

    async def test_whoknows(self):
        self.lastfm.call.side_effect = [
            {"artist": {"name": "Drake", "stats": {"userplaycount": "5"}}},
            {"artist": {"name": "Drake", "stats": {"userplaycount": "10"}}},
        ]
        await cmd_whoknows(self.message, CommandObject(command="wk", args="Drake"), self.db, self.lastfm)
        text = self.message.answer.call_args.args[0]
        self.assert_no_mentions(text)
        self.assertLess(text.index("Bob &amp; bob"), text.index("alice"))

    async def test_recent_sends_without_reply(self):
        self.lastfm.call.return_value = {"recenttracks": {"track": [{"artist": "Drake", "name": "Track"}]}}
        await cmd_recent(self.message, self.db, self.lastfm)
        self.assert_no_mentions(self.message.answer.call_args.args[0])

    async def test_database_lookup_and_reply_target_preserved(self):
        db = Database(":memory:")
        await db.connect()
        try:
            await db.register_user(2, "@bob", "bob_fm")
            command = CommandObject(command="vs", args="@bob 7")
            target = await _find_target(self.message, command, db)
            self.assertEqual(target["lastfm_user"], "bob_fm")
            self.assertEqual(target["tg_name"], "@bob")
            self.message.reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=2, is_bot=False))
            target = await _find_target(self.message, CommandObject(command="vs"), db)
            self.assertEqual(target["tg_id"], 2)
        finally:
            await db.close()
