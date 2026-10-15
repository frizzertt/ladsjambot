"""Data layer for listening statistics: fetches from Last.fm and aggregates."""
import asyncio
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bot.config import config
from bot.lastfm import PLACEHOLDER_HASH, LastFMClient

MAX_PAGES = 15  # 200 scrobbles per page
AVG_TRACK_MINUTES = 3.6

PERIODS: Dict[str, Tuple[str, str]] = {
    "7d": ("7day", "7 дней"),
    "1m": ("1month", "месяц"),
    "3m": ("3month", "3 месяца"),
    "6m": ("6month", "полгода"),
    "12m": ("12month", "год"),
    "all": ("overall", "всё время"),
}


@dataclass
class ListeningStats:
    name: str
    days: int
    total: int
    unique_artists: int
    unique_tracks: int
    active_days: int
    minutes: int
    streak: int
    per_day: List[Tuple[str, int]]
    per_hour: List[int]
    top_artists: List[Tuple[str, str, int]]
    top_tracks: List[Tuple[str, str, int]]
    best_day: Tuple[str, int]
    truncated: bool = False


def _tz() -> timezone:
    return timezone(timedelta(hours=config.TZ_OFFSET_HOURS))


def _artist_of(t: Dict[str, Any]) -> str:
    a = t.get("artist")
    if isinstance(a, dict):
        return a.get("#text") or a.get("name") or "?"
    return a or "?"


async def fetch_scrobbles(
    client: LastFMClient, user: str, since_ts: int
) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
    """Return (scrobbles newest-first, truncated). None if the API is unreachable/errored."""
    first = await client.call(
        "user.getrecenttracks", user=user, limit=200, page=1, **{"from": since_ts}
    )
    if not first or "error" in first or "recenttracks" not in first:
        return None, False

    rt = first["recenttracks"]
    attr = rt.get("@attr", {}) if isinstance(rt, dict) else {}
    total_pages = int(attr.get("totalPages", 1) or 1)
    pages = [rt.get("track", [])]

    last_page = min(total_pages, MAX_PAGES)
    if last_page > 1:
        rest = await asyncio.gather(
            *[
                client.call(
                    "user.getrecenttracks", user=user, limit=200, page=p, **{"from": since_ts}
                )
                for p in range(2, last_page + 1)
            ]
        )
        for r in rest:
            if r and isinstance(r.get("recenttracks"), dict):
                pages.append(r["recenttracks"].get("track", []))

    out: List[Dict[str, Any]] = []
    for chunk in pages:
        if isinstance(chunk, dict):
            chunk = [chunk]
        for t in chunk:
            if not isinstance(t, dict):
                continue
            date = t.get("date")
            if not isinstance(date, dict) or "uts" not in date:  # skip "now playing"
                continue
            out.append(
                {"ts": int(date["uts"]), "artist": _artist_of(t), "track": t.get("name", "?")}
            )
    return out, total_pages > MAX_PAGES


def build_stats(name: str, days: int, scrobbles: List[Dict[str, Any]], truncated: bool = False) -> ListeningStats:
    tz = _tz()
    today = datetime.now(tz).date()
    day_keys = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    by_day: Counter = Counter()
    by_hour = [0] * 24
    artists: Counter = Counter()
    tracks: Counter = Counter()

    for s in scrobbles:
        dt = datetime.fromtimestamp(s["ts"], tz)
        by_day[dt.date()] += 1
        by_hour[dt.hour] += 1
        artists[s["artist"]] += 1
        tracks[(s["artist"], s["track"])] += 1

    per_day = [(d.strftime("%d.%m"), by_day.get(d, 0)) for d in day_keys]
    best = max(per_day, key=lambda x: x[1]) if per_day else ("-", 0)

    # Current streak of consecutive active days ending today (or yesterday)
    streak = 0
    cursor = today if by_day.get(today) else today - timedelta(days=1)
    while by_day.get(cursor):
        streak += 1
        cursor -= timedelta(days=1)

    return ListeningStats(
        name=name,
        days=days,
        total=len(scrobbles),
        unique_artists=len(artists),
        unique_tracks=len(tracks),
        active_days=sum(1 for d in day_keys if by_day.get(d)),
        minutes=int(len(scrobbles) * AVG_TRACK_MINUTES),
        streak=streak,
        per_day=per_day,
        per_hour=by_hour,
        top_artists=[(a, "", c) for a, c in artists.most_common(5)],
        top_tracks=[(t, a, c) for (a, t), c in tracks.most_common(5)],
        best_day=best,
        truncated=truncated,
    )


async def fetch_top(
    client: LastFMClient, user: str, kind: str, period: str, limit: int
) -> Optional[List[Dict[str, Any]]]:
    """kind: albums | artists | tracks. Returns list of normalized dicts."""
    data = await client.call(f"user.gettop{kind}", user=user, period=period, limit=limit)
    if not data or "error" in data:
        return None
    root = data.get(f"top{kind}", {})
    items = root.get(kind[:-1], [])
    if isinstance(items, dict):
        items = [items]

    result = []
    for it in items:
        artist = it.get("artist")
        artist = artist.get("name", "") if isinstance(artist, dict) else (artist or "")
        cover = None
        imgs = it.get("image", [])
        if isinstance(imgs, list):
            for img in reversed(imgs):
                u = (img.get("#text") or "").strip() if isinstance(img, dict) else ""
                if u and PLACEHOLDER_HASH not in u:
                    cover = u
                    break
        result.append(
            {
                "name": it.get("name", "?"),
                "artist": artist,
                "plays": int(it.get("playcount", 0) or 0),
                "cover": cover,
            }
        )
    return result


async def count_scrobbles(client: LastFMClient, user: str, since_ts: int) -> Optional[int]:
    data = await client.call("user.getrecenttracks", user=user, limit=1, **{"from": since_ts})
    if not data or "error" in data:
        return None
    try:
        return int(data["recenttracks"]["@attr"]["total"])
    except (KeyError, TypeError, ValueError):
        return None


def compatibility(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> Tuple[int, List[Tuple[str, int, int]]]:
    """Cosine similarity (on sqrt play counts) of two top-artist lists + shared artists."""
    va = {x["name"].lower(): (x["name"], math.sqrt(x["plays"]), x["plays"]) for x in a}
    vb = {x["name"].lower(): (x["name"], math.sqrt(x["plays"]), x["plays"]) for x in b}
    dot = sum(va[k][1] * vb[k][1] for k in va.keys() & vb.keys())
    na = math.sqrt(sum(v[1] ** 2 for v in va.values()))
    nb = math.sqrt(sum(v[1] ** 2 for v in vb.values()))
    score = int(round(100 * dot / (na * nb))) if na and nb else 0
    shared = sorted(
        ((va[k][0], va[k][2], vb[k][2]) for k in va.keys() & vb.keys()),
        key=lambda x: x[1] + x[2],
        reverse=True,
    )
    return score, shared


def since_days(days: int) -> int:
    """Unix timestamp of local midnight, `days` calendar days back (including today)."""
    tz = _tz()
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((start - timedelta(days=days - 1)).timestamp())
