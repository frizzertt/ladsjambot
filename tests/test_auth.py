import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

from aiogram.filters import CommandObject
from aiogram.types import Message

from bot.database import Database
from bot.handlers.private import cb_change_auth, cb_check_auth, cmd_reg, cmd_start_or_auth, router


class AuthBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()
        self.user = SimpleNamespace(id=101, username="alice", full_name="Alice")
        self.message = MagicMock(spec=Message)
        self.message.from_user = self.user
        self.message.chat = SimpleNamespace(type="private")
        self.message.answer = AsyncMock()
        self.message.edit_text = AsyncMock()
        self.query = SimpleNamespace(
            from_user=self.user, message=self.message,
            data="check_auth:test-token", answer=AsyncMock(),
        )
        self.lastfm = SimpleNamespace(
            shared_secret="test-secret", get_auth_token=AsyncMock(return_value="test-token"),
            get_auth_url=MagicMock(return_value="https://www.last.fm/api/auth/?token=test-token"),
            get_session_from_token=AsyncMock(return_value=(True, "new_user", None)),
            check_user_exists=AsyncMock(return_value=(True, "manual_user")),
        )

    async def asyncTearDown(self):
        await self.db.close()

    async def seed_linked(self, username="old_user"):
        await self.db.register_user(101, "@alice", username)
        for group in (-1001, -1002):
            await self.db.add_group_member_if_registered(group, 101)

    async def assert_account_and_groups(self, username):
        self.assertEqual((await self.db.get_user(101))["lastfm_user"], username)
        for group in (-1001, -1002):
            members = await self.db.get_group_members(group)
            self.assertEqual(len(members), 1)
            self.assertEqual(members[0]["tg_id"], 101)
            self.assertEqual(members[0]["lastfm_user"], username)

    async def test_linked_start_and_auth_do_not_request_token(self):
        username = 'Имя<&"/ ?#'
        await self.seed_linked(username)
        for command in ("/start", "/auth", "/start auth"):
            with self.subTest(command=command):
                self.message.text = command
                await cmd_start_or_auth(self.message, self.lastfm, self.db)
                text = self.message.answer.call_args.args[0]
                self.assertIn("Вы уже привязаны к Last.fm:", text)
                self.assertIn("Имя&lt;&amp;&quot;/ ?#", text)
                self.assertIn("https://www.last.fm/user/" + quote(username, safe=""), text)
                button = self.message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
                self.assertEqual(button.text, "Сменить аккаунт")
                self.assertEqual(button.callback_data, "change_auth")
        self.lastfm.get_auth_token.assert_not_awaited()
        await self.assert_account_and_groups(username)

    async def test_unregistered_user_gets_existing_auth_flow(self):
        await cmd_start_or_auth(self.message, self.lastfm, self.db)
        self.lastfm.get_auth_token.assert_awaited_once()
        text = self.message.answer.call_args.args[0]
        self.assertIn("/reg", text)
        self.assertIn("Подтвердить привязку", text)
        keyboard = self.message.answer.call_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[1][0].callback_data, "check_auth:test-token")
        self.assertIsNone(await self.db.get_user(101))

    async def test_change_account_preserves_old_registration(self):
        await self.seed_linked()
        await cb_change_auth(self.query, self.lastfm)
        self.lastfm.get_auth_token.assert_awaited_once()
        self.assertIn("Старая привязка сохранится", self.message.answer.call_args.args[0])
        await self.assert_account_and_groups("old_user")

    async def test_auth_failures_do_not_write_user_or_groups(self):
        await self.seed_linked()
        for error in ("Вы ещё не подтвердили доступ на сайте Last.fm.",
                      "Ссылка истекла или уже использована. Вызовите /auth ещё раз.",
                      "Сетевая ошибка. Попробуйте позже.", "Ошибка Last.fm (код 13)."):
            with self.subTest(error=error), patch.object(self.db, "register_user", new=AsyncMock()) as save:
                self.lastfm.get_session_from_token.return_value = (False, None, error)
                await cb_check_auth(self.query, self.db, self.lastfm)
                save.assert_not_awaited()
                alert = self.query.answer.call_args.args[0]
                self.assertIn(error, alert)
                self.assertIn("Действующая привязка сохранена", alert)
                await self.assert_account_and_groups("old_user")
        self.message.edit_text.assert_not_awaited()

    async def test_successful_change_uses_upsert_and_preserves_groups(self):
        await self.seed_linked()
        self.lastfm.get_session_from_token.return_value = (True, "new<&user", None)
        with patch.object(self.db, "register_user", wraps=self.db.register_user) as save:
            await cb_check_auth(self.query, self.db, self.lastfm)
            save.assert_awaited_once_with(tg_id=101, tg_name="@alice", lastfm_user="new<&user")
        await self.assert_account_and_groups("new<&user")
        self.assertIn("new&lt;&amp;user", self.message.edit_text.call_args.args[0])
        self.query.answer.assert_awaited_once_with("Успешно привязано!", show_alert=False)

    async def test_old_callback_and_repeated_confirmation(self):
        await self.seed_linked()
        self.query.data = "check_auth:old-token"
        await cb_check_auth(self.query, self.db, self.lastfm)
        self.lastfm.get_session_from_token.assert_awaited_once_with("old-token")
        self.lastfm.get_session_from_token.return_value = (False, None, "Ссылка уже использована. Вызовите /auth ещё раз.")
        await cb_check_auth(self.query, self.db, self.lastfm)
        await self.assert_account_and_groups("new_user")
        self.assertIn("/auth", self.query.answer.call_args.args[0])

    async def test_malformed_old_callbacks_do_not_crash(self):
        await self.seed_linked()
        for data in (None, "check_auth", "check_auth:", "check_auth: "):
            with self.subTest(data=data):
                self.query.data = data
                await cb_check_auth(self.query, self.db, self.lastfm)
                self.assertIn("/auth", self.query.answer.call_args.args[0])
        self.lastfm.get_session_from_token.assert_not_awaited()
        await self.assert_account_and_groups("old_user")

    async def test_legacy_callback_routes_are_still_registered(self):
        for data in ("check_auth:old-token", "check_auth"):
            self.query.data = data
            matches = [await handler.check(self.query) for handler in router.callback_query.handlers
                       if handler.callback is cb_check_auth]
            self.assertTrue(any(matched for matched, _ in matches))

    async def test_inaccessible_change_message(self):
        self.query.message = None
        await cb_change_auth(self.query, self.lastfm)
        self.lastfm.get_auth_token.assert_not_awaited()
        self.assertIn("/auth", self.query.answer.call_args.args[0])

    async def test_reg_still_works_without_web_auth(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        self.message.answer.return_value = status
        await cmd_reg(self.message, CommandObject(command="reg", args="manual_user"), self.db, self.lastfm)
        self.assertEqual((await self.db.get_user(101))["lastfm_user"], "manual_user")
        self.lastfm.get_auth_token.assert_not_awaited()
