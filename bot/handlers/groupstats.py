"""Group listening highlights, using the shared Canvas processing queue."""
import asyncio
import io
import logging
import re
import shutil
import tempfile
from html import escape
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import numpy as np
from aiogram import Router
from aiogram.filters import Filter
from aiogram.types import FSInputFile, Message

from bot import stats as st
from bot.canvas.analysis import analyse_audio
from bot.canvas.groupstats import GroupStatsFrames
from bot.canvas.process import MediaError, in_thread
from bot.canvas.render import Segment, join_segments, render_segment
from bot.canvas.resolver import resolve_audio
from bot.canvas.service import CanvasBusy, CanvasService, PART_BYTES, chunks
from bot.database import Database
from bot.handlers.stats import _parse_days
from bot.lastfm import LastFMClient, PLACEHOLDER_HASH
from bot.themes import for_chat

router = Router(name='groupstats_router')
log = logging.getLogger(__name__)
MAX_IMAGE = 5 * 1024 * 1024
MAX_MEMBERS = 64
MAX_PREPARED_BYTES = 256 * 1024 * 1024


class LimitedBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_IMAGE:
            raise ValueError('Image too large')
        return super().write(data)


async def avatar(message, user_id):
    try:
        async with asyncio.timeout(15):
            photos = await message.bot.get_user_profile_photos(user_id, limit=1)
            if not photos.photos:
                return None
            photo = photos.photos[0][-1]
            if photo.file_size and photo.file_size > MAX_IMAGE:
                return None
            file = await message.bot.get_file(photo.file_id)
            if not file.file_path or (file.file_size and file.file_size > MAX_IMAGE):
                return None
            output = LimitedBuffer()
            await message.bot.download_file(file.file_path, destination=output, timeout=12)
            return output.getvalue() or None
    except Exception:
        return None




class GroupStatsCommand(Filter):
    async def __call__(self, message: Message):
        match = re.fullmatch(r'(?i)(/?)groupstats(?:@([A-Za-z0-9_]+))?(?:\s+(.*))?', (message.text or '').strip())
        if not match or (match[2] and not match[1]):
            return False
        if match[2]:
            me = await message.bot.me()
            if match[2].casefold() != (me.username or '').casefold():
                return False
        return {'groupstats_args': (match[3] or '').lower().split()}


@router.message(GroupStatsCommand())
async def handle_groupstats(message: Message, groupstats_args: list[str], db: Database,
                            lastfm: LastFMClient, canvas_service: CanvasService):
    if message.chat.type not in {'group', 'supergroup'}:
        await message.reply('Команда /groupstats доступна в групповом чате.')
        return
    if not message.from_user:
        return
    days = _parse_days(groupstats_args, default=7, maximum=30)
    wait = None
    try:
        async with canvas_service.admit(message.chat.id):
            async with asyncio.timeout(900):
                await db.add_group_member_if_registered(message.chat.id, message.from_user.id)
                members = sorted(await db.get_group_members(message.chat.id), key=lambda member: member['tg_id'])
                if not members:
                    await message.reply('В группе пока нет участников с привязанным Last.fm.')
                    return
                if len(members) > MAX_MEMBERS:
                    await message.reply(f'Команда поддерживает до {MAX_MEMBERS} участников за один вызов.')
                    return
                wait = await message.reply('Собираю видео с любимыми треками участников…')
                theme = await for_chat(db, message.chat.id)
                since = st.since_days(days)
                await in_thread(canvas_service.analyser._load)
                skipped = []
                with tempfile.TemporaryDirectory(prefix='ladsjam-groupstats-', dir=canvas_service.temp_dir) as folder:
                    root = Path(folder)
                    parts = []
                    approximations = []
                    part_bytes = 0
                    sent_count = 0
                    part_number = 0

                    async def flush():
                        nonlocal parts, approximations, part_bytes, sent_count, part_number
                        if not parts:
                            return
                        directory = root / f'part-{part_number:03d}'
                        directory.mkdir()
                        moved = []
                        for j, part in enumerate(parts):
                            target = directory / f'segment-{j:03d}.mp4'
                            part.replace(target)
                            moved.append(target)
                        output = await join_segments(moved, directory, canvas_service.ffmpeg)
                        await message.reply_video(video=FSInputFile(output), width=720, height=720,
                                                  duration=10 * len(parts), supports_streaming=True,
                                                  request_timeout=120)
                        for block in chunks(approximations, 3500):
                            await message.reply('Приблизительное совпадение аудио (статистика относится к исходному треку):\n' + block,
                                                parse_mode='HTML')
                        sent_count += len(parts)
                        output.unlink()
                        directory.rmdir()
                        parts, approximations, part_bytes = [], [], 0
                        part_number += 1

                    # Keep prepared media on disk until its turn to render.
                    prepared = []
                    prepared_bytes = 0
                    for i, member in enumerate(members):
                        name = member['tg_name']
                        directory = root / f'member-{i:03d}'
                        directory.mkdir()
                        try:
                            async with asyncio.timeout(150):
                                scrobbles, truncated = await st.fetch_scrobbles(lastfm, member['lastfm_user'], since)
                                if scrobbles is None:
                                    skipped.append((name, 'Last.fm недоступен'))
                                    continue
                                if truncated:
                                    skipped.append((name, 'слишком много прослушиваний для точного топа'))
                                    continue
                                if not scrobbles:
                                    skipped.append((name, 'нет прослушиваний'))
                                    continue
                                stats = st.build_stats(name, days, scrobbles)
                                track, artist, plays = stats.top_tracks[0]
                                info = await lastfm.call('track.getInfo', retries=0, artist=artist, track=track)
                                try:
                                    duration = float((info or {}).get('track', {}).get('duration', 0)) / 1000 or None
                                except (TypeError, ValueError, AttributeError):
                                    duration = None
                                avatar_bytes, cover_bytes = await asyncio.gather(avatar(message, member['tg_id']), lastfm.cover(artist, track))
                                audio = await resolve_audio(artist, track, directory / 'download', duration)
                                start, pcm = await analyse_audio(audio.path, directory, canvas_service.analyser,
                                                                 canvas_service.ffmpeg, 10, preview=getattr(audio, 'preview', False))
                            np.save(directory / 'excerpt.npy', pcm, allow_pickle=False)
                            del pcm
                            if avatar_bytes:
                                (directory / 'avatar').write_bytes(avatar_bytes)
                            if cover_bytes:
                                (directory / 'cover').write_bytes(cover_bytes)
                            member_bytes = sum(path.stat().st_size for path in directory.rglob('*') if path.is_file())
                            if prepared_bytes + member_bytes > MAX_PREPARED_BYTES:
                                skipped.append((name, 'превышен лимит места для подготовки видео'))
                                continue
                            prepared_bytes += member_bytes
                            prepared.append((i, name, artist, track, plays, audio, start, directory))
                        except (MediaError, TimeoutError, ValueError, OSError) as exc:
                            log.warning('Groupstats member %s skipped (%s)', i, type(exc).__name__)
                            skipped.append((name, 'аудио недоступно или обработка не завершилась'))
                        finally:
                            if not prepared or prepared[-1][0] != i:
                                shutil.rmtree(directory, ignore_errors=True)

                    async def make_scene(entry, position):
                        _, name, artist, track, plays, audio, start, directory = entry
                        avatar_path, cover_path = directory / 'avatar', directory / 'cover'
                        frames = await in_thread(partial(GroupStatsFrames, name, artist, track, plays,
                                                 avatar_path.read_bytes() if avatar_path.exists() else None,
                                                 cover_path.read_bytes() if cover_path.exists() else None,
                                                 theme, days=days, position=position, total=len(prepared)))
                        pcm = np.load(directory / 'excerpt.npy', allow_pickle=False)
                        await in_thread(frames.prepare_audio, pcm)
                        return frames, pcm

                    current = None
                    for index, entry in enumerate(prepared):
                        i, name, artist, track, plays, audio, start, directory = entry
                        clip = root / f'clip-{i:03d}.mp4'
                        size = None
                        try:
                            if current is None:
                                current = await make_scene(entry, index + 1)
                            frames, pcm = current
                            next_scene = None
                            while index + 1 < len(prepared):
                                try:
                                    next_scene = await make_scene(prepared[index + 1], index + 2)
                                    break
                                except (MediaError, TimeoutError, ValueError, OSError) as exc:
                                    failed = prepared.pop(index + 1)
                                    log.warning('Groupstats member %s skipped (%s)', failed[0], type(exc).__name__)
                                    skipped.append((failed[1], 'аудио недоступно или обработка не завершилась'))
                                    shutil.rmtree(failed[-1], ignore_errors=True)
                            frames.total = len(prepared)
                            if next_scene:
                                frames.next_frame = next_scene[0]
                                next_scene[0].entered = True
                            await render_segment(frames, Segment(0, audio.path, start, 10, pcm), clip, canvas_service.ffmpeg)
                            size = clip.stat().st_size
                            if size > PART_BYTES:
                                raise MediaError('Single clip exceeds upload budget')
                            current = next_scene
                        except (MediaError, TimeoutError, ValueError, OSError) as exc:
                            log.warning('Groupstats member %s skipped (%s)', i, type(exc).__name__)
                            clip.unlink(missing_ok=True)
                            size = None
                            skipped.append((name, 'аудио недоступно или обработка не завершилась'))
                            current = None
                        finally:
                            shutil.rmtree(directory, ignore_errors=True)
                        if size is not None:
                            if parts and part_bytes + size > PART_BYTES:
                                await flush()
                            parts.append(clip)
                            part_bytes += size
                            if getattr(audio, 'approximate', False):
                                original = escape(f'{artist} — {track}'[:140])
                                matched = escape(str(getattr(audio, 'matched_title', '') or 'название неизвестно')[:140])
                                approximations.append(f'{escape(str(name)[:80])}: {original} → {matched}')
                    await flush()
                    if not sent_count:
                        await message.reply('Не удалось собрать видео: нет участников с доступным аудио за выбранный период.')
                if skipped:
                    lines = [f'• {escape(str(name)[:80])}: {escape(reason)}' for name, reason in skipped]
                    for start in range(0, len(lines), 20):
                        await message.reply('Пропущены участники:\n' + '\n'.join(lines[start:start + 20]), parse_mode='HTML')
    except CanvasBusy:
        await message.reply('Канвас уже готовится или очередь заполнена. Попробуй чуть позже.')
    except (MediaError, TimeoutError, OSError) as exc:
        log.warning('Groupstats request failed (%s)', type(exc).__name__)
        await message.reply('Не удалось завершить видео. Попробуй позже.')
    finally:
        if wait:
            try:
                await wait.delete()
            except Exception:
                pass
