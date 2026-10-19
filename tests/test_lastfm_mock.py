import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.lastfm import LastFMClient, PLACEHOLDER_HASH


class LastFMMockTestSuite(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = MagicMock()
        self.client = LastFMClient(
            api_key="mock_api_key",
            session=self.session,
            user_agent="MockAgent/1.0",
            semaphore_limit=4,
        )

    async def test_check_user_exists_success(self):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "user": {"name": "TestUser", "realname": "Tester"}
        })
        self.session.get.return_value.__aenter__.return_value = mock_response

        exists, name = await self.client.check_user_exists("testuser")
        self.assertTrue(exists)
        self.assertEqual(name, "TestUser")

    async def test_check_user_exists_not_found(self):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "error": 6,
            "message": "User not found"
        })
        self.session.get.return_value.__aenter__.return_value = mock_response

        exists, name = await self.client.check_user_exists("unknown_user")
        self.assertFalse(exists)
        self.assertIsNone(name)

    async def test_check_user_exists_server_error(self):
        mock_response = AsyncMock()
        mock_response.status = 503
        self.session.get.return_value.__aenter__.return_value = mock_response

        exists, name = await self.client.check_user_exists("user_error")
        self.assertFalse(exists)
        self.assertIsNone(name)

    async def test_get_user_now_playing_dict_track(self):
        # Edge case: Last.fm returns track as a dict when history has only 1 track
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "recenttracks": {
                "track": {
                    "artist": {"#text": "Pink Floyd"},
                    "name": "Time",
                    "@attr": {"nowplaying": "true"},
                    "image": [
                        {"size": "extralarge", "#text": "https://example.com/cover.jpg"}
                    ]
                }
            }
        })
        self.session.get.return_value.__aenter__.return_value = mock_response

        # Mock download cover
        with patch.object(self.client, "_download_cover", new=AsyncMock(return_value=b"fake_cover")):
            result = await self.client.get_user_now_playing({
                "tg_id": 1,
                "tg_name": "@roger",
                "lastfm_user": "roger_waters"
            })

            self.assertIsNotNone(result)
            self.assertEqual(result.artist, "Pink Floyd")
            self.assertEqual(result.track, "Time")
            self.assertEqual(result.cover_bytes, b"fake_cover")

    async def test_get_user_now_playing_placeholder_hash(self):
        # Edge case: cover contains placeholder hash
        mock_response = AsyncMock()
        mock_response.status = 200
        placeholder_url = f"https://lastfm.freetls.fastly.net/i/u/300x300/{PLACEHOLDER_HASH}.png"
        mock_response.json = AsyncMock(return_value={
            "recenttracks": {
                "track": [
                    {
                        "artist": "The Doors",
                        "name": "Riders on the Storm",
                        "@attr": {"nowplaying": "true"},
                        "image": [
                            {"size": "extralarge", "#text": placeholder_url}
                        ]
                    }
                ]
            }
        })
        self.session.get.return_value.__aenter__.return_value = mock_response

        result = await self.client.get_user_now_playing({
            "tg_id": 2,
            "tg_name": "@jim",
            "lastfm_user": "jim_morrison"
        })

        self.assertIsNotNone(result)
        self.assertEqual(result.artist, "The Doors")
        # Cover bytes must be None when URL is placeholder star
        self.assertIsNone(result.cover_bytes)

    async def test_get_user_now_playing_not_playing(self):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "recenttracks": {
                "track": [
                    {
                        "artist": {"#text": "Nirvana"},
                        "name": "Smells Like Teen Spirit"
                        # No @attr nowplaying!
                    }
                ]
            }
        })
        self.session.get.return_value.__aenter__.return_value = mock_response

        result = await self.client.get_user_now_playing({
            "tg_id": 3,
            "tg_name": "@kurt",
            "lastfm_user": "kurt_cobain"
        })
        self.assertIsNone(result)

    async def test_fetch_all_now_playing_resilience(self):
        # Simulate 3 users: 1 playing, 1 fails with exception/timeout, 1 not playing
        user1 = {"tg_id": 1, "tg_name": "@u1", "lastfm_user": "u1"}
        user2 = {"tg_id": 2, "tg_name": "@u2", "lastfm_user": "u2"}
        user3 = {"tg_id": 3, "tg_name": "@u3", "lastfm_user": "u3"}

        async def mock_get(user_entry):
            if user_entry["tg_id"] == 1:
                return MagicMock(artist="Artist 1", track="Track 1")
            elif user_entry["tg_id"] == 2:
                raise asyncio.TimeoutError("Connection timed out after 3.0s")
            else:
                return None

        with patch.object(self.client, "get_user_now_playing", side_effect=mock_get):
            results = await self.client.fetch_all_now_playing([user1, user2, user3])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].artist, "Artist 1")

    def test_calculate_api_sig(self):
        self.client.shared_secret = "mysecret"
        params = {"method": "auth.getSession", "api_key": "mykey", "token": "mytoken"}
        sig = self.client.calculate_api_sig(params)
        self.assertEqual(len(sig), 32)

    async def test_get_auth_token_success(self):
        self.client.shared_secret = "mysecret"
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"token": "sample_token_12345"})
        self.session.get.return_value.__aenter__.return_value = mock_response

        token = await self.client.get_auth_token()
        self.assertEqual(token, "sample_token_12345")
        auth_url = self.client.get_auth_url(token)
        self.assertIn("token=sample_token_12345", auth_url)

    async def test_get_session_from_token_success(self):
        self.client.shared_secret = "mysecret"
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "session": {"name": "authorized_user", "key": "sess_key"}
        })
        self.session.get.return_value.__aenter__.return_value = mock_response

        success, username, error = await self.client.get_session_from_token("token_xyz")
        self.assertTrue(success)
        self.assertEqual(username, "authorized_user")
        self.assertIsNone(error)

    async def test_get_session_from_token_not_authorized(self):
        self.client.shared_secret = "mysecret"
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "error": 14,
            "message": "This token has not been authorized"
        })
        self.session.get.return_value.__aenter__.return_value = mock_response

        success, username, error = await self.client.get_session_from_token("token_xyz")
        self.assertFalse(success)
        self.assertIsNone(username)
        self.assertIn("не подтвердили", error)

    async def test_auth_error_codes_are_not_all_expired_tokens(self):
        self.client.shared_secret = "test-secret"
        response = AsyncMock(status=200)
        self.session.get.return_value.__aenter__.return_value = response
        for code in (4, 15, "15", 14, "14", 8, 11, 13, 26, 29):
            with self.subTest(code=code):
                response.json.return_value = {"error": code, "message": "provider message"}
                success, username, error = await self.client.get_session_from_token("fake-token")
                self.assertFalse(success)
                self.assertIsNone(username)
                if int(code) in (4, 15):
                    self.assertIn("/auth", error)
                    self.assertIn("уже использована", error)
                elif int(code) == 14:
                    self.assertIn("не подтвердили", error)
                    self.assertIn("Подтвердить привязку", error)
                    self.assertNotIn("истекла", error)
                else:
                    self.assertIn(f"код {code}", error)
                    self.assertNotIn("истекла", error)

    async def test_auth_http_outage_is_not_an_expired_token(self):
        self.client.shared_secret = "test-secret"
        for status in (429, 503):
            with self.subTest(status=status):
                response = AsyncMock(status=status)
                self.session.get.return_value.__aenter__.return_value = response
                success, username, error = await self.client.get_session_from_token("fake-token")
                self.assertFalse(success)
                self.assertIsNone(username)
                self.assertIn("временно недоступен", error)
                self.assertNotIn("истекла", error)
                response.json.assert_not_awaited()

    async def test_invalid_auth_responses_do_not_register_a_user(self):
        self.client.shared_secret = "test-secret"
        response = AsyncMock(status=200)
        self.session.get.return_value.__aenter__.return_value = response
        for data in (None, [], {}, {"session": {"name": ""}}, {"session": {"name": 12}},
                     {"error": "unknown"}):
            with self.subTest(data=data):
                response.json.return_value = data
                success, username, error = await self.client.get_session_from_token("fake-token")
                self.assertFalse(success)
                self.assertIsNone(username)
                self.assertIsNotNone(error)

    async def test_auth_network_error_does_not_expose_request_secrets(self):
        self.client.shared_secret = "test-secret"
        self.session.get.side_effect = asyncio.TimeoutError("token=fake-token&api_key=mock_api_key")
        for method in (self.client.get_session_from_token, self.client.get_auth_token):
            with self.subTest(method=method.__name__), self.assertLogs("bot.lastfm", level="ERROR") as logs:
                result = await method("fake-token") if method == self.client.get_session_from_token else await method()
            output = " ".join(logs.output) + str(result)
            self.assertNotIn("fake-token", output)
            self.assertNotIn("mock_api_key", output)
            self.assertNotIn("test-secret", output)
            if isinstance(result, tuple):
                self.assertIn("Сетевая ошибка", result[2])

    async def test_invalid_auth_token_response_is_not_logged(self):
        self.client.shared_secret = "test-secret"
        response = AsyncMock(status=200)
        response.json.return_value = {"error": 13, "message": "token=fake-token"}
        self.session.get.return_value.__aenter__.return_value = response
        with self.assertLogs("bot.lastfm", level="ERROR") as logs:
            self.assertIsNone(await self.client.get_auth_token())
        self.assertNotIn("fake-token", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
