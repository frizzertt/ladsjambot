import asyncio
import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.formatting import Bold, Italic, Text, TextLink

from bot.collage import build_collage
from bot.database import Database
from bot.formatting import now_playing_html_chunks, spotify_search_url, user_name, user_name_html
from bot.lastfm import LastFMClient

logger = logging.getLogger(__name__)

router = Router(name="group_router")
# Handle messages only in group and supergroup chats
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("join"))
@router.message(F.text.lower().in_({"join", "!join"}))
async def cmd_join(message: Message, db: Database) -> None:
    """
    Handle /join command in group chats.
    Links the user to the current group if they have already registered their Last.fm profile.
    """
    user = message.from_user
    if not user:
        return

    tg_name = f"@{user.username}" if user.username else user.full_name
    is_registered = await db.add_group_member_if_registered(
        chat_id=message.chat.id,
        tg_id=user.id,
        tg_name=tg_name,
    )

    if is_registered:
        await message.answer(
            f"✅ <b>{user_name_html(tg_name)}</b>, вы успешно привязаны к этой группе!\n"
            "Теперь ваши текущие треки будут отображаться при вызове <code>/np</code>.",
            parse_mode="HTML",
        )
    else:
        bot_info = await message.bot.get_me()
        bot_username = bot_info.username or "бота"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔐 Авторизоваться через Last.fm",
                        url=f"https://t.me/{bot_username}?start=auth",
                    )
                ]
            ]
        )
        await message.answer(
            "❌ Вы ещё не привязали свой аккаунт Last.fm.\n\n"
            "Нажмите кнопку ниже, чтобы войти через официальный сайт Last.fm:",
            reply_markup=kb,
            parse_mode="HTML",
        )


@router.message(Command("np", "nowplaying"))
@router.message(F.text.lower().in_({"np", "nowplaying", "!np", "!nowplaying"}))
async def cmd_now_playing(
    message: Message,
    db: Database,
    lastfm: LastFMClient,
) -> None:
    """
    Handle /np and /nowplaying in group chats.
    Fetches currently playing tracks for all group members and builds an album cover collage.
    """
    chat_id = message.chat.id

    # Ensure caller is linked to the group if they are registered
    user = message.from_user
    if user and not user.is_bot:
        tg_name = f"@{user.username}" if user.username else user.full_name
        await db.add_group_member_if_registered(
            chat_id=chat_id,
            tg_id=user.id,
            tg_name=tg_name,
        )

    # 1. Fetch all group members with linked Last.fm accounts
    members = await db.get_group_members(chat_id)

    # Edge Case: No registered members in the group
    if not members:
        bot_info = await message.bot.get_me()
        bot_username = bot_info.username or "бота"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔐 Авторизоваться через Last.fm",
                        url=f"https://t.me/{bot_username}?start=auth",
                    )
                ]
            ]
        )
        await message.answer(
            "Никто не привязал аккаунт. Напишите мне в ЛС /reg <ник> или нажмите кнопку ниже:",
            reply_markup=kb,
        )
        return

    # Visual feedback while processing
    await message.bot.send_chat_action(chat_id=chat_id, action="upload_photo")

    # 2. Concurrently fetch currently playing tracks
    active_tracks = await lastfm.fetch_all_now_playing(members)

    # Edge Case: Nobody is currently listening to music
    if not active_tracks:
        names = ", ".join(user_name_html(m["tg_name"]) for m in members)
        await message.answer(
            f"Сейчас никто ничего не слушает\n<i>Проверены: {names}</i>",
            parse_mode="HTML",
        )
        return

    # 3. Non-blocking image generation in thread pool
    try:
        collage_buf = await asyncio.to_thread(build_collage, active_tracks)
    except Exception as e:
        logger.error("Failed to generate collage: %s", e)
        await message.answer("⚠️ Произошла ошибка при генерации коллажа.")
        return

    # 4. Construct caption list: "Юзер: Артист — Трек"
    parts = [Text("🎧 ", Bold("Сейчас слушают:"), "\n\n")]
    for item in active_tracks:
        if len(parts) > 1:
            parts.append(Text("\n"))
        parts.append(Text(
            Bold(user_name(item.tg_name)), ": ", item.artist, " — ", item.track, " ",
            TextLink("(послушать)", url=spotify_search_url(item.artist, item.track)),
        ))

    active_ids = {item.tg_id for item in active_tracks}
    idle = [user_name(m["tg_name"]) for m in members if m["tg_id"] not in active_ids]
    if idle:
        parts.append(Text("\n\n", Italic("Не слушают: " + ", ".join(idle))))
    chunks = now_playing_html_chunks(Text(*parts))

    # 5. Send photo collage to group
    photo_file = BufferedInputFile(
        file=collage_buf.getvalue(),
        filename="nowplaying_collage.jpg",
    )

    await message.answer_photo(
        photo=photo_file,
        caption=chunks[0],
        parse_mode="HTML",
    )
    for chunk in chunks[1:]:
        await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)
