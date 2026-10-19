import asyncio
import hashlib
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
import aiohttp

from bot.config import config

logger = logging.getLogger(__name__)

# Last.fm default placeholder star image hash
PLACEHOLDER_HASH = "2a96cbd8b46e442fc41c2b86b821562f"
COVER_RETRIES = 2
RECENT_TRACKS_RETRIES = 2
TRANSIENT_LASTFM_ERRORS = (8, 11, 16, "8", "11", "16")
COVER_CACHE_SIZE = 64
RECENT_SCROBBLE_WINDOW = 240  # seconds a just-scrobbled track still counts as playing
_np_request_id: ContextVar[Optional[str]] = ContextVar("np_request_id", default=None)


@dataclass
class TrackInfo:
    tg_id: int
    tg_name: str
    lastfm_user: str
    artist: str
    track: str
    cover_bytes: Optional[bytes] = None


class LastFMClient:
    """Async client for interacting with the Last.fm API."""

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        shared_secret: str = config.LASTFM_SHARED_SECRET,
        user_agent: str = config.USER_AGENT,
        semaphore_limit: int = config.LASTFM_RATE_LIMIT_SEMAPHORE,
    ):
        self.api_key = api_key
        self.shared_secret = shared_secret
        self.session = session
        self.user_agent = user_agent
        self.semaphore = asyncio.Semaphore(semaphore_limit)
        self.api_url = config.LASTFM_API_URL
        self._cover_cache: Dict[str, bytes] = {}

    async def check_user_exists(self, username: str) -> Tuple[bool, Optional[str]]:
        """
        Verify if a user exists on Last.fm via user.getInfo.
        Returns: (exists: bool, canonical_username: Optional[str])
        """
        params = {
            "method": "user.getinfo",
            "user": username,
            "api_key": self.api_key,
            "format": "json",
        }
        headers = {"User-Agent": self.user_agent}

        try:
            timeout = aiohttp.ClientTimeout(total=config.LASTFM_REQUEST_TIMEOUT)
            async with self.semaphore:
                async with self.session.get(
                    self.api_url, params=params, headers=headers, timeout=timeout
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Last.fm user.getinfo returned HTTP %s for %s", resp.status, username
                        )
                        return False, None

                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        return False, None

                    if "error" in data:
                        logger.info(
                            "Last.fm error for user %s: code=%s message=%s",
                            username,
                            data.get("error"),
                            data.get("message"),
                        )
                        return False, None

                    user_obj = data.get("user")
                    if isinstance(user_obj, dict):
                        canonical_name = user_obj.get("name", username)
                        return True, canonical_name

                    return False, None
        except Exception as e:
            logger.error("Exception checking Last.fm user %s: %s", username, e)
            return False, None

    async def call(self, method: str, retries: int = 2, **params: Any) -> Optional[Dict[str, Any]]:
        """
        Generic read-only Last.fm API call with retries on transient failures.
        Returns the decoded JSON dict (may contain an "error" key) or None on network failure.
        """
        query: Dict[str, str] = {
            "method": method,
            "api_key": self.api_key,
            "format": "json",
        }
        query.update({k: str(v) for k, v in params.items() if v is not None})
        headers = {"User-Agent": self.user_agent}
        timeout = aiohttp.ClientTimeout(total=config.LASTFM_REQUEST_TIMEOUT * 2)

        for attempt in range(retries + 1):
            try:
                async with self.semaphore:
                    async with self.session.get(
                        self.api_url, params=query, headers=headers, timeout=timeout
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            if isinstance(data, dict):
                                if data.get("error") in TRANSIENT_LASTFM_ERRORS:
                                    raise RuntimeError(f"transient Last.fm error {data['error']}")
                                return data
                        elif resp.status < 500 and resp.status != 429:
                            return None
            except Exception as e:
                logger.debug("Last.fm %s attempt %d failed: %r", method, attempt + 1, e)
            if attempt < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
        return None

    async def _download_cover(self, url: Optional[str]) -> Optional[bytes]:
        """
        Download album cover bytes with in-memory cache, retries and backoff.
        Last.fm CDN often fails/timeouts on the first request, so we retry.
        """
        if not url or PLACEHOLDER_HASH in url:
            return None

        cached = self._cover_cache.get(url)
        if cached:
            return cached

        headers = {"User-Agent": self.user_agent}
        timeout = aiohttp.ClientTimeout(total=config.COVER_DOWNLOAD_TIMEOUT)
        attempts = COVER_RETRIES + 1

        for attempt in range(attempts):
            try:
                async with self.session.get(url, headers=headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if len(data) > 0:
                            if len(self._cover_cache) >= COVER_CACHE_SIZE:
                                self._cover_cache.pop(next(iter(self._cover_cache)))
                            self._cover_cache[url] = data
                            return data
                    else:
                        logger.debug("Cover download HTTP %s for %s", resp.status, url)
                        if resp.status == 404:
                            return None
            except Exception as e:
                logger.debug("Cover download attempt %d failed for %s: %s", attempt + 1, url, e)

            if attempt < attempts - 1:
                await asyncio.sleep(0.4 * (attempt + 1))

        return None

    async def _fetch_cover_via_track_info(self, artist: str, track: str) -> Optional[str]:
        """Fallback: ask track.getInfo for the album image when recenttracks has none."""
        params = {
            "method": "track.getInfo",
            "artist": artist,
            "track": track,
            "api_key": self.api_key,
            "format": "json",
            "autocorrect": "1",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=config.LASTFM_REQUEST_TIMEOUT * 2)
            async with self.semaphore:
                async with self.session.get(
                    self.api_url, params=params, headers={"User-Agent": self.user_agent}, timeout=timeout
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json(content_type=None)
            images = (data.get("track") or {}).get("album", {}).get("image", [])
            for img in reversed(images if isinstance(images, list) else []):
                url = (img.get("#text") or "").strip() if isinstance(img, dict) else ""
                if url and PLACEHOLDER_HASH not in url:
                    return url
        except Exception as e:
            logger.debug("track.getInfo fallback failed for %s - %s: %s", artist, track, e)
        return None

    async def get_user_now_playing(self, user_entry: Dict[str, Any]) -> Optional[TrackInfo]:
        """
        Fetch currently playing track for a single user.
        Respects semaphore and timeout. Catches API and network errors gracefully.
        """
        tg_id = user_entry["tg_id"]
        tg_name = user_entry["tg_name"]
        lastfm_user = user_entry["lastfm_user"]

        request_id = _np_request_id.get() or uuid4().hex[:12]
        started = attempt_started = time.perf_counter()
        network_started = None
        http_status = lastfm_error = nowplaying = timestamp = age = None
        attempt = 0

        def diagnostic(event: str, reason: str, exception: Optional[Exception] = None) -> None:
            # Never log exception text: aiohttp exceptions can contain signed request URLs.
            now = time.perf_counter()
            network_start = network_started if network_started is not None else now
            logger.info(
                "np_%s request_id=%s user=%r attempt=%d http_status=%s lastfm_error=%s "
                "elapsed_ms=%.1f queue_ms=%.1f total_ms=%.1f nowplaying=%r "
                "timestamp=%s age_s=%s reason=%s exception=%s",
                event, request_id, lastfm_user, attempt + 1, http_status, lastfm_error,
                (now - network_start) * 1000, (network_start - attempt_started) * 1000,
                (now - started) * 1000, nowplaying, timestamp,
                round(age, 3) if age is not None else None, reason,
                type(exception).__name__ if exception is not None else None,
            )

        params = {
            "method": "user.getrecenttracks",
            "user": lastfm_user,
            "api_key": self.api_key,
            "format": "json",
            "limit": "1",
        }
        headers = {"User-Agent": self.user_agent}
        timeout = aiohttp.ClientTimeout(total=config.LASTFM_REQUEST_TIMEOUT)

        # Transient failures (timeout, 5xx, Last.fm error 8/11/16) must not look like
        # "nobody is listening", so retry a few times before giving up.
        data = None
        for attempt in range(RECENT_TRACKS_RETRIES + 1):
            attempt_started = time.perf_counter()
            network_started = None
            http_status = lastfm_error = None
            attempt_reason = "request_exception"
            try:
                async with self.semaphore:
                    network_started = time.perf_counter()
                    async with self.session.get(
                        self.api_url, params=params, headers=headers, timeout=timeout
                    ) as resp:
                        http_status = resp.status
                        if resp.status == 200:
                            candidate = await resp.json(content_type=None)
                            err = candidate.get("error") if isinstance(candidate, dict) else None
                            # Only numeric error codes are diagnostic data, never the provider's message.
                            if isinstance(err, int):
                                lastfm_error = err
                            elif isinstance(err, str) and err.isascii() and err.isdecimal() and len(err) <= 6:
                                lastfm_error = err
                            elif err is not None:
                                lastfm_error = "<invalid code>"
                            attempt_reason = "lastfm_transient_error" if err in TRANSIENT_LASTFM_ERRORS else "response_received"
                            if err not in TRANSIENT_LASTFM_ERRORS:
                                data = candidate
                                diagnostic("attempt", attempt_reason)
                                break
                        elif resp.status < 500 and resp.status != 429:
                            diagnostic("decision", "http_nonretryable")
                            return None
                        else:
                            attempt_reason = "http_retryable"
                diagnostic("attempt", attempt_reason)
            except Exception as e:
                diagnostic("attempt", "request_exception", e)
            if attempt < RECENT_TRACKS_RETRIES:
                await asyncio.sleep(0.5 * (attempt + 1))

        if data is None:
            diagnostic("decision", "empty_json_response" if attempt_reason == "response_received" else "retries_exhausted")
            return None

        # Parse recenttracks payload
        if not isinstance(data, dict):
            # Preserve the existing parser's exception; only explain it before gather excludes this user.
            diagnostic("decision", "payload_not_object")
        recenttracks = data.get("recenttracks")
        if not isinstance(recenttracks, dict):
            diagnostic("decision", "lastfm_error" if isinstance(data, dict) and "error" in data else "recenttracks_missing_or_invalid")
            return None

        raw_track = recenttracks.get("track")

        # Edge case: If user has only 1 track in history, Last.fm returns a dict instead of list
        if isinstance(raw_track, dict):
            tracks = [raw_track]
        elif isinstance(raw_track, list):
            tracks = raw_track
        else:
            diagnostic("decision", "tracks_missing_or_invalid")
            return None

        if not tracks:
            diagnostic("decision", "tracks_empty")
            return None

        first_track = tracks[0]
        if not isinstance(first_track, dict):
            diagnostic("decision", "first_track_invalid")
            return None

        # Track is active if flagged nowplaying, or if it was scrobbled moments ago.
        # Last.fm drops the nowplaying flag as soon as the scrobble is committed
        # (and some scrobblers never send it), so a freshly scrobbled track is
        # almost certainly still playing.
        attr = first_track.get("@attr")
        raw_nowplaying = attr.get("nowplaying") if isinstance(attr, dict) else None
        nowplaying = raw_nowplaying if raw_nowplaying in (None, True, False, "true", "false", "True", "False", "1", "0") else "<unexpected value>"
        is_now = isinstance(attr, dict) and attr.get("nowplaying") == "true"
        if not is_now:
            date = first_track.get("date")
            try:
                timestamp = int(date["uts"])
                age = time.time() - timestamp
            except (TypeError, KeyError, ValueError) as e:
                diagnostic("decision", "timestamp_missing_or_invalid", e)
                return None
            if age > RECENT_SCROBBLE_WINDOW:
                diagnostic("decision", "outside_activity_window")
                return None
        else:
            # Observation only: @attr.nowplaying remains sufficient even without a valid date.
            try:
                timestamp = int(first_track["date"]["uts"])
                age = time.time() - timestamp
            except (TypeError, KeyError, ValueError, OverflowError):
                pass
        diagnostic("decision", "active_nowplaying" if is_now else "active_recent_scrobble")

        # Extract artist name
        artist_raw = first_track.get("artist")
        if isinstance(artist_raw, dict):
            artist_name = artist_raw.get("#text", "Неизвестный исполнитель")
        elif isinstance(artist_raw, str):
            artist_name = artist_raw
        else:
            artist_name = "Неизвестный исполнитель"

        track_name = first_track.get("name") or "Неизвестный трек"

        # Extract album cover URL
        image_url = None
        images = first_track.get("image", [])
        if isinstance(images, list):
            size_map: Dict[str, str] = {}
            for img in images:
                if isinstance(img, dict):
                    sz = img.get("size")
                    url = img.get("#text")
                    if sz and url:
                        size_map[sz] = url.strip()

            # Priority order for cover image size
            for preferred_size in ("extralarge", "large", "medium", "small"):
                candidate_url = size_map.get(preferred_size)
                if candidate_url and PLACEHOLDER_HASH not in candidate_url:
                    image_url = candidate_url
                    break

        # Download cover image if available
        cover_bytes = await self._download_cover(image_url)

        # Fallback: no image in recenttracks or download failed -> try track.getInfo
        if cover_bytes is None:
            fallback_url = await self._fetch_cover_via_track_info(
                artist_name.strip(), track_name.strip()
            )
            if fallback_url and fallback_url != image_url:
                cover_bytes = await self._download_cover(fallback_url)

        return TrackInfo(
            tg_id=tg_id,
            tg_name=tg_name,
            lastfm_user=lastfm_user,
            artist=artist_name.strip(),
            track=track_name.strip(),
            cover_bytes=cover_bytes,
        )

    async def fetch_all_now_playing(
        self, members: List[Dict[str, Any]]
    ) -> List[TrackInfo]:
        """
        Concurrently fetch currently playing tracks for all group members.
        Uses asyncio.gather with return_exceptions=True so failures for any single user
        do not crash the entire batch.
        """
        if not members:
            return []

        request_id = uuid4().hex[:12]
        started = time.perf_counter()
        logger.info("np_batch_start request_id=%s members=%d", request_id, len(members))
        context = _np_request_id.set(request_id)
        try:
            tasks = [self.get_user_now_playing(member) for member in members]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            _np_request_id.reset(context)

        active_tracks: List[TrackInfo] = []
        for member, res in zip(members, results):
            if isinstance(res, Exception):
                logger.warning(
                    "np_decision request_id=%s user=%r reason=member_exception exception=%s",
                    request_id, member.get("lastfm_user"), type(res).__name__,
                )
                continue
            if res is not None:
                active_tracks.append(res)

        logger.info(
            "np_batch_end request_id=%s members=%d active=%d excluded=%d elapsed_ms=%.1f",
            request_id, len(members), len(active_tracks), len(members) - len(active_tracks),
            (time.perf_counter() - started) * 1000,
        )
        return active_tracks

    def calculate_api_sig(self, params: Dict[str, str]) -> str:
        """
        Calculate Last.fm API method signature (MD5).
        Concatenates sorted parameter pairs + shared_secret.
        """
        sorted_pairs = sorted(
            (k, v) for k, v in params.items() if k not in ("format", "api_sig", "callback")
        )
        sig_base = "".join(f"{k}{v}" for k, v in sorted_pairs) + self.shared_secret
        return hashlib.md5(sig_base.encode("utf-8")).hexdigest()

    async def get_auth_token(self) -> Optional[str]:
        """
        Fetch an unauthorized request token via auth.getToken.
        """
        if not self.shared_secret:
            logger.warning("LASTFM_SHARED_SECRET is not configured; cannot call auth.getToken")
            return None

        params = {
            "method": "auth.getToken",
            "api_key": self.api_key,
        }
        params["api_sig"] = self.calculate_api_sig(params)
        params["format"] = "json"

        headers = {"User-Agent": self.user_agent}
        try:
            timeout = aiohttp.ClientTimeout(total=config.LASTFM_REQUEST_TIMEOUT)
            async with self.semaphore:
                async with self.session.get(
                    self.api_url, params=params, headers=headers, timeout=timeout
                ) as resp:
                    if resp.status != 200:
                        logger.error("Last.fm auth.getToken returned HTTP %s", resp.status)
                        return None
                    data = await resp.json(content_type=None)
                    if isinstance(data, dict) and "token" in data:
                        return data["token"]
                    logger.error("Invalid response from auth.getToken")
                    return None
        except Exception as e:
            logger.error("Exception in auth.getToken (%s)", type(e).__name__)
            return None

    def get_auth_url(self, token: str) -> str:
        """Construct the Last.fm authorization URL for the user to open."""
        return f"https://www.last.fm/api/auth/?api_key={self.api_key}&token={token}"

    async def get_session_from_token(
        self, token: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Exchange an authorized token for user session and username via auth.getSession.
        Returns: (success: bool, username: Optional[str], error_message: Optional[str])
        """
        if not self.shared_secret:
            return False, None, "Не настроен LASTFM_SHARED_SECRET в .env."

        params = {
            "method": "auth.getSession",
            "api_key": self.api_key,
            "token": token,
        }
        params["api_sig"] = self.calculate_api_sig(params)
        params["format"] = "json"

        headers = {"User-Agent": self.user_agent}
        try:
            timeout = aiohttp.ClientTimeout(total=config.LASTFM_REQUEST_TIMEOUT)
            async with self.semaphore:
                async with self.session.get(
                    self.api_url, params=params, headers=headers, timeout=timeout
                ) as resp:
                    if resp.status >= 500 or resp.status == 429:
                        return False, None, "Last.fm временно недоступен. Попробуйте подтвердить привязку позже."
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        return False, None, "Некорректный ответ от Last.fm"

                    if "error" in data:
                        try:
                            code = int(data["error"])
                        except (TypeError, ValueError):
                            return False, None, "Некорректный ответ от Last.fm. Попробуйте ещё раз."
                        if code == 14:
                            return (
                                False,
                                None,
                                "Вы ещё не подтвердили доступ на сайте Last.fm.\n"
                                "Нажмите «Войти через Last.fm», разрешите доступ, затем нажмите «Подтвердить привязку».",
                            )
                        if code in (4, 15):
                            return (
                                False, None,
                                "Ссылка привязки недействительна, истекла или уже использована. Вызовите /auth ещё раз.",
                            )
                        return (
                            False,
                            None,
                            f"Ошибка Last.fm (код {code}). Попробуйте позже или вызовите /auth ещё раз.",
                        )

                    if resp.status != 200:
                        return False, None, f"Ошибка HTTP {resp.status} от Last.fm. Попробуйте позже."
                    session = data.get("session")
                    username = session.get("name") if isinstance(session, dict) else None
                    if isinstance(username, str) and username.strip():
                        return True, username, None

                    return False, None, "Не удалось получить имя пользователя из сессии"
        except (ValueError, aiohttp.ContentTypeError):
            return False, None, "Некорректный ответ от Last.fm. Попробуйте ещё раз."
        except Exception as e:
            logger.error("Exception in auth.getSession (%s)", type(e).__name__)
            return False, None, "Сетевая ошибка при обращении к Last.fm. Попробуйте подтвердить привязку позже."
