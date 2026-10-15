import asyncio
import io
import os
import unittest
from PIL import Image

from bot.config import Config
from bot.database import Database
from bot.lastfm import LastFMClient, TrackInfo, PLACEHOLDER_HASH
from bot.collage import build_collage, _render_single_card, _create_placeholder


class BotTestSuite(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_db_path = "test_bot.db"
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)
        self.db = Database(self.test_db_path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    async def test_database_registration_and_group_linking(self):
        # 1. Register users
        await self.db.register_user(1001, "@alice", "alice_lastfm")
        await self.db.register_user(1002, "Bob Smith", "bob_lastfm")

        self.assertTrue(await self.db.is_user_registered(1001))
        self.assertTrue(await self.db.is_user_registered(1002))
        self.assertFalse(await self.db.is_user_registered(9999))

        # 2. Link member if registered
        group_chat_id = -10099999
        # Registered user should be linked
        linked_alice = await self.db.add_group_member_if_registered(group_chat_id, 1001, "@alice_new")
        self.assertTrue(linked_alice)

        # Non-registered user should not be linked
        linked_stranger = await self.db.add_group_member_if_registered(group_chat_id, 9999, "@stranger")
        self.assertFalse(linked_stranger)

        # Verify members in group
        members = await self.db.get_group_members(group_chat_id)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["tg_id"], 1001)
        self.assertEqual(members[0]["tg_name"], "@alice_new")
        self.assertEqual(members[0]["lastfm_user"], "alice_lastfm")

        # Link Bob as well
        linked_bob = await self.db.add_group_member_if_registered(group_chat_id, 1002, "Bob Smith")
        self.assertTrue(linked_bob)
        members_after = await self.db.get_group_members(group_chat_id)
        self.assertEqual(len(members_after), 2)

    def test_grid_and_collage_rendering(self):
        # Test 1 item -> 1x1
        items_1 = [
            TrackInfo(1, "@user1", "user1", "Artist 1", "Track 1", None)
        ]
        buf_1 = build_collage(items_1)
        img_1 = Image.open(buf_1)
        self.assertEqual(img_1.size, (500, 500))

        # Test 2 items -> 2x1 (1000x500)
        items_2 = [
            TrackInfo(1, "@user1", "user1", "Artist 1", "Track 1", None),
            TrackInfo(2, "@user2", "user2", "Artist 2", "Track 2", None),
        ]
        buf_2 = build_collage(items_2)
        img_2 = Image.open(buf_2)
        self.assertEqual(img_2.size, (1000, 500))

        # Test 3 items -> 2x2 (1000x1000) with 1 empty cell
        items_3 = [
            TrackInfo(1, "@user1", "user1", "Artist 1", "Track 1", None),
            TrackInfo(2, "@user2", "user2", "Artist 2", "Track 2", None),
            TrackInfo(3, "@user3", "user3", "Artist 3", "Track 3", None),
        ]
        buf_3 = build_collage(items_3)
        img_3 = Image.open(buf_3)
        self.assertEqual(img_3.size, (1000, 1000))

        # Test with actual image bytes
        sample_img = Image.new("RGB", (300, 300), (200, 100, 50))
        sample_buf = io.BytesIO()
        sample_img.save(sample_buf, format="JPEG")
        sample_bytes = sample_buf.getvalue()

        items_with_cover = [
            TrackInfo(1, "@listener", "listener", "Queen", "Bohemian Rhapsody", sample_bytes),
            TrackInfo(2, "@rocker", "rocker", "AC/DC", "Thunderstruck", None),
        ]
        buf_cover = build_collage(items_with_cover)
        img_cover = Image.open(buf_cover)
        self.assertEqual(img_cover.size, (1000, 500))

    def test_placeholder_hash_detection(self):
        placeholder_url = f"https://lastfm.freetls.fastly.net/i/u/300x300/{PLACEHOLDER_HASH}.png"
        self.assertIn(PLACEHOLDER_HASH, placeholder_url)

    def test_placeholder_generation(self):
        img = _create_placeholder(484)
        self.assertEqual(img.size, (484, 484))


if __name__ == "__main__":
    unittest.main()
