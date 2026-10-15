import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot.database import Database

logger = logging.getLogger(__name__)


class GroupMemberTrackerMiddleware(BaseMiddleware):
    """
    Middleware that monitors all messages in groups and supergroups.
    Automatically links the sender's Telegram ID to the current group chat_id
    if the sender has already registered their Last.fm account in the bot.
    """

    def __init__(self, db: Database):
        super().__init__()
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.chat.type in ("group", "supergroup"):
            user = event.from_user
            if user and not user.is_bot:
                tg_name = f"@{user.username}" if user.username else user.full_name
                try:
                    await self.db.add_group_member_if_registered(
                        chat_id=event.chat.id,
                        tg_id=user.id,
                        tg_name=tg_name,
                    )
                except Exception as e:
                    logger.error("Failed to auto-link member in middleware: %s", e)

        return await handler(event, data)
