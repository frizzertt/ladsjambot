import asyncio
import re
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.lastfm import LastFMClient, _np_request_id


class NowPlayingDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = MagicMock()
        self.client = LastFMClient("private-test-key", self.session, shared_secret="private-test-secret")
        self.client._download_cover = AsyncMock(return_value=b"cover")
        self.member = {"tg_id": 1, "tg_name": "@alice", "lastfm_user": "alice_fm"}

    @staticmethod
    def payload(flag=None, timestamp=None):
        track = {"name": "Track", "artist": {"#text": "Artist"}}
        if flag is not None:
            track["@attr"] = {"nowplaying": flag}
        if timestamp is not None:
            track["date"] = {"uts": str(timestamp)}
        return {"recenttracks": {"track": [track]}}

    def response(self, status, payload):
        response = AsyncMock(status=status)
        response.json.return_value = payload
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        return context

    async def test_false_negative_then_active_has_distinct_correlated_diagnostics(self):
        self.session.get.side_effect = [
            self.response(200, self.payload(timestamp=1000)),
            self.response(200, self.payload(flag="true")),
        ]
        with patch("bot.lastfm.time.time", return_value=1300), self.assertLogs("bot.lastfm", "INFO") as logs:
            self.assertEqual(await self.client.fetch_all_now_playing([self.member]), [])
            self.assertEqual(len(await self.client.fetch_all_now_playing([self.member])), 1)
        output = "\n".join(logs.output)
        self.assertIn("reason=outside_activity_window", output)
        self.assertIn("timestamp=1000 age_s=300", output)
        self.assertIn("reason=active_nowplaying", output)
        self.assertIn("nowplaying='true'", output)
        self.assertIn("user='alice_fm'", output)
        self.assertIn("http_status=200", output)
        self.assertIn("elapsed_ms=", output)
        self.assertIn("queue_ms=", output)
        ids = re.findall(r"np_batch_start request_id=(\w+)", output)
        self.assertEqual(len(set(ids)), 2)
        for request_id in ids:
            self.assertIn(f"np_batch_end request_id={request_id}", output)
        self.assertIsNone(_np_request_id.get())
        # A successful but inactive response still has no retry: diagnostics do not change behavior.
        self.assertEqual(self.session.get.call_count, 2)

    async def test_activity_rules_and_window_boundaries_are_unchanged(self):
        for flag, timestamp, active, reason in [
            ("true", None, True, "active_nowplaying"),
            ("true", 1, True, "active_nowplaying"),
            (None, 1000, True, "active_recent_scrobble"),
            (None, 999, False, "outside_activity_window"),
            (None, 1300, True, "active_recent_scrobble"),  # Existing acceptance of future timestamps.
            (None, None, False, "timestamp_missing_or_invalid"),
            (None, "bad-date", False, "timestamp_missing_or_invalid"),
            (True, None, False, "timestamp_missing_or_invalid"),  # Only the string "true" is recognized.
        ]:
            with self.subTest(flag=flag, timestamp=timestamp):
                self.session.get.side_effect = [self.response(200, self.payload(flag, timestamp))]
                with patch("bot.lastfm.time.time", return_value=1240), self.assertLogs("bot.lastfm", "INFO") as logs:
                    result = await self.client.get_user_now_playing(self.member)
                self.assertEqual(result is not None, active)
                self.assertIn(f"reason={reason}", "\n".join(logs.output))

    async def test_transient_error_retries_and_eventual_success_are_logged(self):
        self.session.get.side_effect = [
            self.response(200, {"error": 11, "message": "DO-NOT-LOG"}),
            self.response(503, None),
            self.response(200, self.payload("true")),
        ]
        with patch("bot.lastfm.asyncio.sleep", new=AsyncMock()) as sleep, self.assertLogs("bot.lastfm", "INFO") as logs:
            self.assertIsNotNone(await self.client.get_user_now_playing(self.member))
        output = "\n".join(logs.output)
        self.assertIn("attempt=1 http_status=200 lastfm_error=11", output)
        self.assertIn("attempt=2 http_status=503", output)
        self.assertIn("attempt=3 http_status=200", output)
        self.assertNotIn("DO-NOT-LOG", output)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [0.5, 1.0])

    async def test_timeout_exhaustion_still_returns_none_without_logging_request_url(self):
        self.session.get.side_effect = asyncio.TimeoutError(
            "https://example.com/?api_key=private-test-key&token=private-test-secret"
        )
        with patch("bot.lastfm.asyncio.sleep", new=AsyncMock()), self.assertLogs("bot.lastfm", "INFO") as logs:
            self.assertEqual(await self.client.fetch_all_now_playing([self.member]), [])
        output = "\n".join(logs.output)
        self.assertEqual(self.session.get.call_count, 3)
        self.assertIn("exception=TimeoutError", output)
        self.assertIn("reason=retries_exhausted", output)
        self.assertIn("active=0 excluded=1", output)
        for sensitive in ("private-test-key", "private-test-secret", "https://", "api_key="):
            self.assertNotIn(sensitive, output)

    async def test_nonretryable_http_and_lastfm_error_keep_their_existing_results(self):
        for status, payload, reason in [(403, None, "http_nonretryable"),
                                        (200, {"error": 29}, "lastfm_error")]:
            with self.subTest(status=status):
                self.session.get.side_effect = [self.response(status, payload)]
                with self.assertLogs("bot.lastfm", "INFO") as logs:
                    self.assertIsNone(await self.client.get_user_now_playing(self.member))
                self.assertIn(f"reason={reason}", "\n".join(logs.output))

    async def test_retryable_http_and_lastfm_errors_still_exhaust_three_attempts(self):
        for status, payload in [(429, None), (503, None),
                                (200, {"error": 8}), (200, {"error": 11}),
                                (200, {"error": 16}), (200, {"error": "16"})]:
            with self.subTest(status=status, payload=payload):
                self.session.get.reset_mock()
                self.session.get.side_effect = [self.response(status, payload) for _ in range(3)]
                with patch("bot.lastfm.asyncio.sleep", new=AsyncMock()) as sleep, self.assertLogs("bot.lastfm", "INFO") as logs:
                    self.assertIsNone(await self.client.get_user_now_playing(self.member))
                output = "\n".join(logs.output)
                self.assertIn("reason=retries_exhausted", output)
                self.assertIn(f"attempt=3 http_status={status}", output)
                self.assertEqual(self.session.get.call_count, 3)
                self.assertEqual(sleep.await_count, 2)

    async def test_invalid_payload_still_raises_inside_task_and_is_excluded_by_gather(self):
        self.session.get.side_effect = [self.response(200, ["DO-NOT-LOG"])]
        with self.assertLogs("bot.lastfm", "INFO") as logs:
            self.assertEqual(await self.client.fetch_all_now_playing([self.member]), [])
        output = "\n".join(logs.output)
        self.assertIn("reason=payload_not_object", output)
        self.assertIn("reason=member_exception exception=AttributeError", output)
        self.assertNotIn("DO-NOT-LOG", output)

    async def test_empty_payload_shapes_have_specific_reasons(self):
        for payload, reason in [(None, "empty_json_response"), ({}, "recenttracks_missing_or_invalid"),
                                ({"recenttracks": {}}, "tracks_missing_or_invalid"),
                                ({"recenttracks": {"track": []}}, "tracks_empty"),
                                ({"recenttracks": {"track": [None]}}, "first_track_invalid")]:
            with self.subTest(reason=reason):
                self.session.get.side_effect = [self.response(200, payload)]
                with self.assertLogs("bot.lastfm", "INFO") as logs:
                    self.assertIsNone(await self.client.get_user_now_playing(self.member))
                self.assertIn(f"reason={reason}", "\n".join(logs.output))

    async def test_parallel_batches_keep_request_ids_separate(self):
        self.session.get.side_effect = lambda *args, **kwargs: self.response(200, self.payload("true"))
        other = {**self.member, "lastfm_user": "bob_fm"}
        with self.assertLogs("bot.lastfm", "INFO") as logs:
            results = await asyncio.gather(self.client.fetch_all_now_playing([self.member]),
                                           self.client.fetch_all_now_playing([other]))
        self.assertEqual([len(r) for r in results], [1, 1])
        user_ids = {}
        for line in logs.output:
            match = re.search(r"np_decision request_id=(\w+) user='(\w+)'", line)
            if match:
                user_ids[match[2]] = match[1]
        self.assertNotEqual(user_ids["alice_fm"], user_ids["bob_fm"])
        self.assertIsNone(_np_request_id.get())
