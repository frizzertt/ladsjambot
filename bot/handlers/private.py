import html
import logging
from typing import Optional, Union
from urllib.parse import quote
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.database import Database
from bot.formatting import user_name_html
from bot.lastfm import LastFMClient

logger = logging.getLogger(__name__)

router = Router(name="private_router")
# Handle messages only in private chats
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


async def _render_auth_prompt(
    target: Union[Message, CallbackQuery],
    lastfm: LastFMClient,
    is_edit: bool = False,
    replacing: bool = False,
) -> None:
    """Send or edit an authorization prompt with web login buttons if available."""
    token: Optional[str] = None
    if lastfm.shared_secret:
        token = await lastfm.get_auth_token()

    if token:
        auth_url = lastfm.get_auth_url(token)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔗 Войти через Last.fm",
                        url=auth_url,
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить привязку",
                        callback_data=f"check_auth:{token}",
                    )
                ],
            ]
        )
        text = (
            "👋 <b>Добро пожаловать в NowPlaying Bot!</b>\n\n"
            "Чтобы ваши треки попадали в общий коллаж группы, авторизуйтесь через Last.fm:\n\n"
            "1️⃣ Нажмите <b>«🔗 Войти через Last.fm»</b> (откроется официальная страница Last.fm).\n"
            "2️⃣ Нажмите кнопку <b>«Yes, allow access»</b> (разрешить доступ приложению).\n"
            "3️⃣ Вернитесь в этот чат и нажмите <b>«✅ Подтвердить привязку»</b>.\n\n"
            "<i>(Также доступна ручная привязка по нику: <code>/reg &lt;ник&gt;</code>)</i>"
        )
    else:
        keyboard = None
        text = (
            "👋 <b>Привет!</b> Я бот для отображения музыки, которую сейчас слушают участники вашей группы.\n\n"
            "<b>Как привязать аккаунт:</b>\n"
            "Отправьте команду:\n"
            "<code>/reg &lt;ваш_ник_на_lastfm&gt;</code>\n"
            "<i>Пример:</i> <code>/reg username</code>\n\n"
            "<i>💡 Подсказка: для включения входа по кнопке через сайт укажите <code>LASTFM_SHARED_SECRET</code> в .env</i>"
        )

    if replacing:
        text = "🔄 <b>Смена аккаунта Last.fm</b>\nСтарая привязка сохранится до успешного подтверждения новой.\n\n" + text

    if is_edit:
        msg = target if isinstance(target, Message) else target.message
        assert msg is not None
        await msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        if isinstance(target, Message):
            await target.answer(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            assert target.message is not None
            await target.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("start"))
@router.message(Command("auth"))
async def cmd_start_or_auth(message: Message, lastfm: LastFMClient, db: Database) -> None:
    """Handle /start and /auth commands."""
    user = await db.get_user(message.from_user.id) if message.from_user else None
    if user:
        username = user["lastfm_user"]
        profile_url = "https://www.last.fm/user/" + quote(username, safe="")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Сменить аккаунт", callback_data="change_auth")
        ]])
        await message.answer(
            f"Вы уже привязаны к Last.fm: <b>{html.escape(username)}</b>\n"
            f'<a href="{html.escape(profile_url, quote=True)}">Открыть профиль Last.fm</a>',
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return
    await _render_auth_prompt(message, lastfm)


@router.callback_query(F.data == "change_auth")
async def cb_change_auth(query: CallbackQuery, lastfm: LastFMClient) -> None:
    """Start a new authorization without changing the existing registration."""
    if not isinstance(query.message, Message):
        await query.answer("Сообщение недоступно. Вызовите /auth ещё раз.", show_alert=True)
        return
    await _render_auth_prompt(query, lastfm, replacing=True)
    await query.answer()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Send help message."""
    text = (
        "📖 <b>Справка по командам:</b>\n\n"
        "<b>В личных сообщениях:</b>\n"
        "• <code>/auth</code> или <code>/start</code> — войти через сайт Last.fm\n"
        "• <code>/reg &lt;ник&gt;</code> — привязать свой Last.fm аккаунт вручную по нику\n"
        "• <code>/help</code> — это справочное сообщение\n\n"
        "<b>Статистика (в ЛС и в группе):</b>\n"
        "• <code>/stats [дней]</code> — инфографика прослушиваний (по умолчанию 7, макс. 30)\n"
        "• <code>/top [7d|1m|3m|6m|12m|all] [albums|artists|tracks]</code> — ваши топы\n"
        "• <code>/recent</code> — последние 10 треков\n"
        "• <code>/compat @ник</code> — музыкальная совместимость\n"
        "• <code>/vs @ник [дней]</code> — дуэль статистик\n"
        "• <code>/genres [период]</code> — ваши жанры\n"
        "• <code>/discover</code> — рекомендации новых артистов\n"
        "• <code>/artist &lt;имя&gt;</code> — ваша статистика по артисту\n"
        "• <code>/loved</code> — любимые треки\n"
        "• <code>/roulette</code> — случайный альбом из вашего топа\n\n"
        "<b>В групповом чате:</b>\n"
        "• <code>/leaders [дней]</code> — лидеры группы по количеству прослушиваний\n"
        "• <code>/whoknows [артист]</code> — кто в группе слушает артиста больше всех\n"
        "• <code>/join</code> или <code>join</code> — подтвердить участие в группе\n"
        "• <code>/np</code> или <code>nowplaying</code> — сгенерировать коллаж треков группы"
    )
    await message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("check_auth:"))
@router.callback_query(F.data == "check_auth")
async def cb_check_auth(
    query: CallbackQuery,
    db: Database,
    lastfm: LastFMClient,
) -> None:
    """Check whether the user has authorized the token on Last.fm website."""
    prefix, separator, token = (query.data or "").partition(":")
    if prefix != "check_auth" or not separator or not token.strip():
        await query.answer("Ссылка привязки недействительна. Вызовите /auth ещё раз.", show_alert=True)
        return
    success, username, error_message = await lastfm.get_session_from_token(token)

    if not success or not username:
        text = error_message or "Не удалось подтвердить новую привязку. Попробуйте ещё раз."
        if await db.get_user(query.from_user.id):
            text += "\nДействующая привязка сохранена."
        await query.answer(text, show_alert=True)
        return

    # User successfully authorized! Save to database
    user = query.from_user
    tg_name = f"@{user.username}" if user.username else user.full_name

    await db.register_user(
        tg_id=user.id,
        tg_name=tg_name,
        lastfm_user=username,
    )

    success_text = (
        f"🎉 <b>Авторизация успешна!</b>\n\n"
        f"Вы успешно вошли как <b>{html.escape(username)}</b> на Last.fm.\n"
        f"Ваше имя в Telegram: <b>{user_name_html(tg_name)}</b>.\n\n"
        "Теперь добавьте меня в группу (или напишите в ней сообщение), "
        "и по команде <code>/np</code> ваши треки будут появляться в коллаже!"
    )

    if isinstance(query.message, Message):
        await query.message.edit_text(success_text, parse_mode="HTML")
    await query.answer("Успешно привязано!", show_alert=False)


@router.message(Command("reg"))
async def cmd_reg(
    message: Message,
    command: CommandObject,
    db: Database,
    lastfm: LastFMClient,
) -> None:
    """
    Handle /reg <lastfm_username>.
    Verifies existence via Last.fm API before persisting to SQLite.
    """
    username = command.args

    if not username or not username.strip():
        await message.reply(
            "⚠️ Пожалуйста, укажите ваш никнейм на Last.fm.\n\n"
            "<b>Использование:</b>\n"
            "<code>/reg &lt;никнейм_lastfm&gt;</code>\n\n"
            "<i>Пример:</i> <code>/reg rock_lover</code>\n\n"
            "Или просто используйте <code>/auth</code> для входа через сайт в один клик!",
            parse_mode="HTML",
        )
        return

    lastfm_user = username.strip()
    status_msg = await message.answer(
        f"🔍 Проверяю существование профиля <code>{html.escape(lastfm_user)}</code> на Last.fm...",
        parse_mode="HTML",
    )

    exists, canonical_name = await lastfm.check_user_exists(lastfm_user)

    if not exists or not canonical_name:
        await status_msg.edit_text(
            f"❌ Пользователь <b>{html.escape(lastfm_user)}</b> не найден на Last.fm "
            "или возникла ошибка при обращении к API.\n\n"
            "Пожалуйста, проверьте правильность написания ника и попробуйте снова.",
            parse_mode="HTML",
        )
        return

    # Build Telegram display name
    user = message.from_user
    assert user is not None
    tg_name = f"@{user.username}" if user.username else user.full_name

    # Save to database
    await db.register_user(
        tg_id=user.id,
        tg_name=tg_name,
        lastfm_user=canonical_name,
    )

    await status_msg.edit_text(
        f"✅ Профиль Last.fm <b>{html.escape(canonical_name)}</b> успешно привязан!\n\n"
        f"Ваше отображаемое имя: <b>{user_name_html(tg_name)}</b>.\n\n"
        "Теперь в любой группе с ботом вы будете участвовать в генерации коллажа "
        "по команде <code>/np</code> (или <code>nowplaying</code>).",
        parse_mode="HTML",
    )
