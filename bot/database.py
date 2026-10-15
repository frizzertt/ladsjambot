import logging
from typing import Any, Dict, List, Optional
import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database manager for storing users and group memberships."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Establish database connection and initialize schema."""
        import os

        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.commit()
        await self._init_tables()
        logger.info("Database connected successfully at %s", self.db_path)

    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    async def _init_tables(self) -> None:
        """Create necessary tables and indices if they do not exist."""
        assert self._conn is not None
        async with self._conn.cursor() as cursor:
            # 1. users: tg_id (PK), tg_name, lastfm_user
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    tg_id INTEGER PRIMARY KEY,
                    tg_name TEXT NOT NULL,
                    lastfm_user TEXT NOT NULL
                );
                """
            )
            # 2. group_members: chat_id, tg_id (PK: chat_id + tg_id)
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS group_members (
                    chat_id INTEGER NOT NULL,
                    tg_id INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, tg_id)
                );
                """
            )
            # Index for fast lookup of group members
            await cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_group_members_chat_id
                ON group_members (chat_id);
                """
            )
            await self._conn.commit()

    async def register_user(self, tg_id: int, tg_name: str, lastfm_user: str) -> None:
        """
        Register or update a user's Telegram ID, name, and Last.fm username.
        """
        assert self._conn is not None
        query = """
            INSERT INTO users (tg_id, tg_name, lastfm_user)
            VALUES (?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                tg_name = excluded.tg_name,
                lastfm_user = excluded.lastfm_user;
        """
        await self._conn.execute(query, (tg_id, tg_name, lastfm_user))
        await self._conn.commit()
        logger.info("Registered user %s (ID %s) with Last.fm: %s", tg_name, tg_id, lastfm_user)

    async def get_user(self, tg_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve user info by Telegram ID."""
        assert self._conn is not None
        query = "SELECT tg_id, tg_name, lastfm_user FROM users WHERE tg_id = ?;"
        async with self._conn.execute(query, (tg_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def is_user_registered(self, tg_id: int) -> bool:
        """Check if user is registered in the database."""
        assert self._conn is not None
        query = "SELECT 1 FROM users WHERE tg_id = ? LIMIT 1;"
        async with self._conn.execute(query, (tg_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None

    async def add_group_member_if_registered(
        self, chat_id: int, tg_id: int, tg_name: Optional[str] = None
    ) -> bool:
        """
        Link a user to the group only if they are registered in the 'users' table.
        Optionally update tg_name if it changed.
        Returns True if the user is registered and linked, False otherwise.
        """
        assert self._conn is not None
        # Insert into group_members only if tg_id exists in users table
        query = """
            INSERT OR IGNORE INTO group_members (chat_id, tg_id)
            SELECT ?, tg_id FROM users WHERE tg_id = ?;
        """
        await self._conn.execute(query, (chat_id, tg_id))

        if tg_name:
            await self._conn.execute(
                "UPDATE users SET tg_name = ? WHERE tg_id = ? AND tg_name != ?;",
                (tg_name, tg_id, tg_name),
            )

        await self._conn.commit()
        return await self.is_user_registered(tg_id)

    async def get_group_members(self, chat_id: int) -> List[Dict[str, Any]]:
        """
        Fetch all registered users who belong to the specified group chat.
        Returns list of dicts with keys: tg_id, tg_name, lastfm_user.
        """
        assert self._conn is not None
        query = """
            SELECT u.tg_id, u.tg_name, u.lastfm_user
            FROM group_members gm
            JOIN users u ON gm.tg_id = u.tg_id
            WHERE gm.chat_id = ?;
        """
        async with self._conn.execute(query, (chat_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
