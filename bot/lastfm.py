import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import aiohttp

from bot.config import config

logger = logging.getLogger(__name__)

# Last.fm default placeholder star image hash
PLACEHOLDER_HASH = "2a96cbd8b46e442fc41c2b86b821562f"


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

    async def _download_cover(self, url: Optional[str]) -> Optional[bytes]:
        """Download album cover bytes from URL with timeout and placeholder detection."""
        if not url:
            return None

        # Verify against known Last.fm placeholder star hash
        if PLACEHOLDER_HASH in url:
            return None

        try:
            timeout = aiohttp.ClientTimeout(total=config.COVER_DOWNLOAD_TIMEOUT)
            headers = {"User-Agent": self.user_agent}
            async with self.session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if len(data) > 0:
                        return data
                else:
                    logger.debug("Cover download returned HTTP %s for %s", resp.status, url)
        except Exception as e:
            logger.debug("Failed to download cover %s: %s", url, e)

        return None

    async def get_user_now_playing(self, user_entry: Dict[str, Any]) -> Optional[TrackInfo]:
        """
        Fetch currently playing track for a single user.
        Respects semaphore and timeout. Catches API and network errors gracefully.
        """
        tg_id = user_entry["tg_id"]
        tg_name = user_entry["tg_name"]
        lastfm_user = user_entry["lastfm_user"]

        params = {
            "method": "user.getrecenttracks",
            "user": lastfm_user,
            "api_key": self.api_key,
            "format": "json",
            "limit": "1",
        }
        headers = {"User-Agent": self.user_agent}
        timeout = aiohttp.ClientTimeout(total=config.LASTFM_REQUEST_TIMEOUT)

        try:
            async with self.semaphore:
                async with self.session.get(
                    self.api_url, params=params, headers=headers, timeout=timeout
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "Last.fm getrecenttracks returned HTTP %s for user %s",
                            resp.status,
                            lastfm_user,
                        )
                        return None

                    data = await resp.json(content_type=None)
        except Exception as e:
            logger.warning("Error fetching recent tracks for %s: %s", lastfm_user, e)
            return None

        if not isinstance(data, dict) or "error" in data:
            if isinstance(data, dict) and "error" in data:
                logger.debug(
                    "Last.fm error code %s for user %s: %s",
                    data.get("error"),
                    lastfm_user,
                    data.get("message"),
                )
            return None

        # Parse recenttracks payload
        recenttracks = data.get("recenttracks")
        if not isinstance(recenttracks, dict):
            return None

        raw_track = recenttracks.get("track")

        # Edge case: If user has only 1 track in history, Last.fm returns a dict instead of list
        if isinstance(raw_track, dict):
            tracks = [raw_track]
        elif isinstance(raw_track, list):
            tracks = raw_track
        else:
            return None

        if not tracks:
            return None

        first_track = tracks[0]
        if not isinstance(first_track, dict):
            return None

        # Track is active ONLY if nowplaying == "true"
        attr = first_track.get("@attr")
        if not isinstance(attr, dict) or attr.get("nowplaying") != "true":
            return None

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

        tasks = [self.get_user_now_playing(member) for member in members]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        active_tracks: List[TrackInfo] = []
        for res in results:
            if isinstance(res, Exception):
                logger.warning("Gather task encountered exception: %s", res)
                continue
            if res is not None:
                active_tracks.append(res)

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
                    logger.error("Invalid response from auth.getToken: %s", data)
                    return None
        except Exception as e:
            logger.error("Exception in auth.getToken: %s", e)
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
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        return False, None, "Некорректный ответ от Last.fm"

                    if "error" in data:
                        code = data.get("error")
                        if code == 14:
                            return (
                                False,
                                None,
                                "Вы ещё не подтвердили доступ на сайте Last.fm.\n"
                                "Нажмите кнопку «🔗 Войти через Last.fm» выше, разрешите доступ в открывшемся окне, "
                                "а затем нажмите «✅ Проверить авторизацию».",
                            )
                        return (
                            False,
                            None,
                            f"Ошибка Last.fm ({code}): {data.get('message', 'Неизвестная ошибка')}",
                        )

                    session = data.get("session")
                    if isinstance(session, dict) and "name" in session:
                        return True, session["name"], None

                    return False, None, "Не удалось получить имя пользователя из сессии"
        except Exception as e:
            logger.error("Exception in auth.getSession: %s", e)
            return False, None, f"Сетевая ошибка: {e}"
