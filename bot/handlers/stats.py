import asyncio
import html
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from bot import stats as st
from bot.database import Database
from bot.infographic import render_album_grid, render_rows, render_stats
from bot.lastfm import LastFMClient

logger = logging.getLogger(__name__)

router = Router(name="stats_router")

KINDS = {
    "albums": "albums", "album": "albums", "альбомы": "albums", "альбом": "albums",
    "artists": "artists", "artist": "artists", "артисты": "artists", "артист": "artists",
    "tracks": "tracks", "track": "tracks", "треки": "tracks", "трек": "tracks",
}
KIND_TITLES = {"albums": "ТОП АЛЬБОМОВ", "artists": "ТОП АРТИСТОВ", "tracks": "ТОП ТРЕКОВ"}
MEDALS = ["🥇", "🥈", "🥉"]
NOT_REGISTERED = (
    "❌ Сначала привяжите Last.fm: напишите боту в личку <code>/start</code> "
    "или <code>/reg &lt;ник&gt;</code>."
)
API_ERROR = "⚠️ Last.fm сейчас не отвечает или профиль скрыт. Попробуйте ещё раз через пару секунд."


def _display_name(message: Message) -> str:
    u = message.from_user
    if not u:
        return "?"
    return f"@{u.username}" if u.username else u.full_name


def _args(command: CommandObject) -> List[str]:
    return (command.args or "").lower().split()


def _parse_days(args: List[str], default: int = 7, maximum: int = 30) -> int:
    for a in args:
        m = re.fullmatch(r"(\d{1,3})d?", a)
        if m:
            return max(1, min(int(m.group(1)), maximum))
    return default


async def _self_user(message: Message, db: Database) -> Optional[Dict[str, Any]]:
    if not message.from_user:
        return None
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.reply(NOT_REGISTERED, parse_mode="HTML")
    return user


async def _send_photo(message: Message, buf, caption: str = "", name: str = "stats.jpg") -> None:
    await message.answer_photo(
        photo=BufferedInputFile(buf.getvalue(), filename=name),
        caption=caption or None,
        parse_mode="HTML",
    )


@router.message(Command("stats", "me"))
async def cmd_stats(message: Message, command: CommandObject, db: Database, lastfm: LastFMClient) -> None:
    """/stats [N] — infographic of your listening for the last N days (default 7, max 30)."""
    user = await _self_user(message, db)
    if not user:
        return
    days = _parse_days(_args(command), default=7, maximum=30)
    await message.bot.send_chat_action(message.chat.id, "upload_photo")

    scrobbles, truncated = await st.fetch_scrobbles(lastfm, user["lastfm_user"], st.since_days(days))
    if scrobbles is None:
        await message.reply(API_ERROR)
        return
    if not scrobbles:
        await message.reply(f"За последние {days} дн. скробблов нет 🤷")
        return

    data = st.build_stats(user["tg_name"], days, scrobbles, truncated)
    buf = await asyncio.to_thread(render_stats, data)
    await _send_photo(message, buf, name="listening_stats.jpg")


@router.message(Command("top"))
async def cmd_top(message: Message, command: CommandObject, db: Database, lastfm: LastFMClient) -> None:
    """/top [7d|1m|3m|6m|12m|all] [albums|artists|tracks] — your all-time/period tops."""
    user = await _self_user(message, db)
    if not user:
        return
    args = _args(command)
    period_key = next((a for a in args if a in st.PERIODS), "1m")
    kind = next((KINDS[a] for a in args if a in KINDS), "albums")
    period, period_label = st.PERIODS[period_key]
    await message.bot.send_chat_action(message.chat.id, "upload_photo")

    items = await st.fetch_top(lastfm, user["lastfm_user"], kind, period, 9 if kind == "albums" else 10)
    if items is None:
        await message.reply(API_ERROR)
        return
    if not items:
        await message.reply("Пока нет данных за этот период 🤷")
        return

    subtitle = f"{user['tg_name']}  ·  {period_label}"
    if kind == "albums":
        covers = await asyncio.gather(*[lastfm._download_cover(i["cover"]) for i in items])
        buf = await asyncio.to_thread(render_album_grid, KIND_TITLES[kind], subtitle, items, covers)
    else:
        rows = [
            (i["name"], i["artist"] if kind == "tracks" else "", i["plays"]) for i in items
        ]
        buf = await asyncio.to_thread(
            render_rows, KIND_TITLES[kind], subtitle, rows, unit_note="ПРОСЛУШИВАНИЯ"
        )
    await _send_photo(message, buf, name="top.jpg")


@router.message(Command("leaders", "leaderboard", "lb"))
async def cmd_leaders(message: Message, command: CommandObject, db: Database, lastfm: LastFMClient) -> None:
    """/leaders [N] — group leaderboard by scrobbles over the last N days (default 7)."""
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Эта команда работает только в группе.")
        return
    days = _parse_days(_args(command), default=7, maximum=90)
    members = await db.get_group_members(message.chat.id)
    if not members:
        await message.reply("В группе пока никто не привязал Last.fm. Используйте /join.")
        return
    await message.bot.send_chat_action(message.chat.id, "upload_photo")

    since = st.since_days(days)
    counts = await asyncio.gather(*[st.count_scrobbles(lastfm, m["lastfm_user"], since) for m in members])
    rows = sorted(
        ((m["tg_name"], f"last.fm/{m['lastfm_user']}", c) for m, c in zip(members, counts) if c),
        key=lambda r: r[2],
        reverse=True,
    )
    if not rows:
        await message.reply(f"За последние {days} дн. ни у кого нет скробблов 🤷")
        return
    buf = await asyncio.to_thread(
        render_rows, "LEADERBOARD", f"{message.chat.title or 'группа'}  ·  {days} дн.", rows,
        unit_note="СКРОББЛЫ",
    )
    await _send_photo(message, buf, name="leaders.jpg")


async def _find_target(message: Message, command: CommandObject, db: Database) -> Optional[Dict[str, Any]]:
    """Resolve target user from reply, @username argument or raw Last.fm name."""
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        u = await db.get_user(reply.from_user.id)
        if u:
            return u
    arg = (command.args or "").strip().split()
    if arg:
        name = arg[0]
        u = await db.get_user_by_name(name if name.startswith("@") else f"@{name}")
        if u:
            return u
        u = await db.get_user_by_name(name)
        if u:
            return u
        return {"tg_id": 0, "tg_name": name, "lastfm_user": name.lstrip("@")}
    return None


@router.message(Command("compat", "match"))
async def cmd_compat(message: Message, command: CommandObject, db: Database, lastfm: LastFMClient) -> None:
    """/compat @user (or reply) — taste compatibility from top artists (6 months)."""
    me = await _self_user(message, db)
    if not me:
        return
    other = await _find_target(message, command, db)
    if not other:
        await message.reply(
            "Укажите пользователя: <code>/compat @username</code> или ответьте командой на его сообщение.",
            parse_mode="HTML",
        )
        return

    a, b = await asyncio.gather(
        st.fetch_top(lastfm, me["lastfm_user"], "artists", "6month", 100),
        st.fetch_top(lastfm, other["lastfm_user"], "artists", "6month", 100),
    )
    if a is None or b is None:
        await message.reply(API_ERROR)
        return
    if not a or not b:
        await message.reply("Недостаточно данных за последние 6 месяцев 🤷")
        return

    score, shared = st.compatibility(a, b)
    filled = round(score / 10)
    bar = "🟩" * filled + "⬛" * (10 - filled)
    verdict = (
        "родственные души 💞" if score >= 70 else
        "очень похожий вкус 🔥" if score >= 45 else
        "есть что обсудить 🎧" if score >= 20 else
        "совсем разные миры 🌌"
    )
    lines = [
        f"🧬 <b>Совместимость</b>\n"
        f"{html.escape(me['tg_name'])} × {html.escape(other['tg_name'])}\n\n"
        f"{bar}  <b>{score}%</b> — {verdict}"
    ]
    if shared:
        lines.append("\n<b>Общие артисты:</b>")
        for name, pa, pb in shared[:6]:
            lines.append(f"• {html.escape(name)} — {pa} / {pb}")
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("whoknows", "wk"))
async def cmd_whoknows(message: Message, command: CommandObject, db: Database, lastfm: LastFMClient) -> None:
    """/whoknows [artist] — who in the group listens to this artist the most."""
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Эта команда работает только в группе.")
        return
    members = await db.get_group_members(message.chat.id)
    if not members:
        await message.reply("В группе пока никто не привязал Last.fm. Используйте /join.")
        return

    artist = (command.args or "").strip()
    if not artist:
        me = await db.get_user(message.from_user.id) if message.from_user else None
        if not me:
            await message.reply(NOT_REGISTERED, parse_mode="HTML")
            return
        recent = await lastfm.call("user.getrecenttracks", user=me["lastfm_user"], limit=1)
        try:
            t = recent["recenttracks"]["track"]
            t = t[0] if isinstance(t, list) else t
            a = t["artist"]
            artist = a.get("#text") if isinstance(a, dict) else a
        except (TypeError, KeyError, IndexError):
            artist = ""
        if not artist:
            await message.reply("Укажите артиста: <code>/whoknows Radiohead</code>", parse_mode="HTML")
            return

    async def one(m: Dict[str, Any]) -> Tuple[str, int, str]:
        d = await lastfm.call(
            "artist.getinfo", artist=artist, username=m["lastfm_user"], autocorrect=1
        )
        try:
            art = d["artist"]
            return m["tg_name"], int(art.get("stats", {}).get("userplaycount", 0) or 0), art.get("name", artist)
        except (TypeError, KeyError, ValueError):
            return m["tg_name"], 0, artist

    results = await asyncio.gather(*[one(m) for m in members])
    canonical = next((r[2] for r in results if r[2]), artist)
    ranked = sorted((r for r in results if r[1] > 0), key=lambda r: r[1], reverse=True)
    if not ranked:
        await message.reply(f"Никто в группе не слушал <b>{html.escape(canonical)}</b> 🤷", parse_mode="HTML")
        return

    lines = [f"🎤 <b>Кто знает {html.escape(canonical)}</b>\n"]
    for i, (name, plays, _) in enumerate(ranked[:10]):
        medal = MEDALS[i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} {html.escape(name)} — <b>{plays}</b>")
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(Command("recent"))
async def cmd_recent(message: Message, db: Database, lastfm: LastFMClient) -> None:
    """/recent — your last 10 scrobbles."""
    user = await _self_user(message, db)
    if not user:
        return
    data = await lastfm.call("user.getrecenttracks", user=user["lastfm_user"], limit=10)
    try:
        tracks = data["recenttracks"]["track"]
        tracks = [tracks] if isinstance(tracks, dict) else tracks
    except (TypeError, KeyError):
        await message.reply(API_ERROR)
        return
    lines = ["🕘 <b>Последние треки</b>\n"]
    for t in tracks[:10]:
        a = t.get("artist")
        a = a.get("#text", "?") if isinstance(a, dict) else a
        live = (t.get("@attr") or {}).get("nowplaying") == "true"
        mark = "▶️" if live else "•"
        lines.append(f"{mark} {html.escape(a or '?')} — {html.escape(t.get('name', '?'))}")
    await message.reply("\n".join(lines), parse_mode="HTML")
