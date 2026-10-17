import asyncio
import logging
import sys
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.config import config, validate_config
from bot.database import Database
from bot.handlers import group_router, private_router, stats_router
from bot.lastfm import LastFMClient
from bot.middlewares import GroupMemberTrackerMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("showwhatimlistening")


async def main() -> None:
    """Entry point for the Telegram bot."""
    # Validate environment variables
    try:
        validate_config()
    except ValueError as e:
        logger.error(str(e))
        logger.error("Please create or update your .env file using .env.example as a template.")
        return

    # Initialize SQLite database
    db = Database(config.DB_PATH)
    await db.connect()

    # Shared aiohttp ClientSession for Last.fm API and image downloading
    http_session = aiohttp.ClientSession()

    # Last.fm client
    lastfm_client = LastFMClient(
        api_key=config.LASTFM_API_KEY,
        shared_secret=config.LASTFM_SHARED_SECRET,
        session=http_session,
        user_agent=config.USER_AGENT,
        semaphore_limit=config.LASTFM_RATE_LIMIT_SEMAPHORE,
    )

    # Initialize aiogram Bot and Dispatcher
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Pass dependencies to all handlers via workflow_data
    dp["db"] = db
    dp["lastfm"] = lastfm_client

    # Register Middlewares (outer_middleware ensures all messages update group membership)
    dp.message.outer_middleware(GroupMemberTrackerMiddleware(db=db))

    # Register Routers
    dp.include_router(stats_router)
    dp.include_router(private_router)
    dp.include_router(group_router)

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="np", description="Коллаж: что слушают сейчас"),
                BotCommand(command="stats", description="Моя статистика за N дней"),
                BotCommand(command="top", description="Мои топы: альбомы/артисты/треки"),
                BotCommand(command="vs", description="Дуэль: сравнить статистику с другом"),
                BotCommand(command="genres", description="Мои жанры"),
                BotCommand(command="discover", description="Что послушать: рекомендации"),
                BotCommand(command="artist", description="Моя статистика по артисту"),
                BotCommand(command="roulette", description="Случайный альбом из моего топа"),
                BotCommand(command="recent", description="Последние треки"),
                BotCommand(command="loved", description="Любимые треки"),
                BotCommand(command="leaders", description="Лидеры группы по количеству прослушиваний"),
                BotCommand(command="compat", description="Музыкальная совместимость"),
                BotCommand(command="whoknows", description="Кто в группе слушает артиста"),
                BotCommand(command="join", description="Привязаться к группе"),
                BotCommand(command="help", description="Справка"),
            ]
        )
    except Exception as e:
        logger.warning("Could not set bot commands: %s", e)

    logger.info("Bot starting polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down bot...")
        await http_session.close()
        await db.close()
        await bot.session.close()
        logger.info("All connections closed. Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
