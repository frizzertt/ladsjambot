import asyncio
import html
import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.collage import build_collage
from bot.database import Database
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
        await message.reply(
            f"✅ <b>{html.escape(tg_name)}</b>, вы успешно привязаны к этой группе!\n"
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
        await message.reply(
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
        await message.reply(
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
        names = ", ".join(html.escape(m["tg_name"]) for m in members)
        await message.reply(
            f"Сейчас никто ничего не слушает\n<i>Проверены: {names}</i>",
            parse_mode="HTML",
        )
        return

    # 3. Non-blocking image generation in thread pool
    try:
        collage_buf = await asyncio.to_thread(build_collage, active_tracks)
    except Exception as e:
        logger.error("Failed to generate collage: %s", e)
        await message.reply("⚠️ Произошла ошибка при генерации коллажа.")
        return

    # 4. Construct caption list: "Юзер: Артист — Трек"
    header = "🎧 <b>Сейчас слушают:</b>\n\n"
    lines = []
    for item in active_tracks:
        user_display = html.escape(item.tg_name)
        artist_display = html.escape(item.artist)
        track_display = html.escape(item.track)
        lines.append(f"<b>{user_display}</b>: {artist_display} — {track_display}")

    full_text = header + "\n".join(lines)

    active_ids = {item.tg_id for item in active_tracks}
    idle = [html.escape(m["tg_name"]) for m in members if m["tg_id"] not in active_ids]
    if idle:
        full_text += "\n\n<i>Не слушают: " + ", ".join(idle) + "</i>"

    # 5. Send photo collage to group
    photo_file = BufferedInputFile(
        file=collage_buf.getvalue(),
        filename="nowplaying_collage.jpg",
    )

    # Telegram caption character limit is 1024 characters
    if len(full_text) <= 1024:
        await message.answer_photo(
            photo=photo_file,
            caption=full_text,
            parse_mode="HTML",
        )
    else:
        # Split caption if it exceeds 1024 characters
        first_chunk = full_text[:1000]
        # Find clean newline boundary
        last_newline = first_chunk.rfind("\n")
        if last_newline != -1:
            caption_part = first_chunk[:last_newline]
            remainder = full_text[last_newline + 1 :]
        else:
            caption_part = first_chunk
            remainder = full_text[1000:]

        await message.answer_photo(
            photo=photo_file,
            caption=caption_part,
            parse_mode="HTML",
        )
        if remainder:
            await message.answer(remainder, parse_mode="HTML")
