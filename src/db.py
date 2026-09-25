"""Database module for tracking songs, channels, orgs, and file hashes."""

import sqlite3
import threading
import hashlib
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass
from contextlib import contextmanager


# =========================
# Models
# =========================

@dataclass
class Channel:
    channel_id: str
    name: str
    org: Optional[str] = None
    sub_org: Optional[str] = None


@dataclass
class Song:
    video_id: str
    channel_id: str
    title: str
    topic: str
    available_at: str
    file_hash: Optional[str] = None
    file_path: Optional[str] = None
    deleted: bool = False
    members_only: bool = False
    privated: bool = False
    error: Optional[str] = None
    duration: Optional[int] = None


# =========================
# Database
# =========================

class Database:
    """SQLite database for tracking music downloads."""

    def __init__(self, db_path: str = "music.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._song_queue: List[Song] = []
        self._channel_queue: List[Channel] = []
        self._init_db()

    # ---------- Connection handling ----------

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(
                self.db_path,
                timeout=30,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def cursor(self):
        conn = self._get_connection()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ---------- Schema ----------

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        with self.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id TEXT PRIMARY KEY,
                    name TEXT,
                    org TEXT,
                    sub_org TEXT
                )
            """)

            # english_name was speculative (added from reading the Holodex API docs)
            # and never ended up used; drop it if an older DB still has it.
            existing_columns = {row["name"] for row in cur.execute("PRAGMA table_info(channels)").fetchall()}
            if "english_name" in existing_columns:
                cur.execute("ALTER TABLE channels DROP COLUMN english_name")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS songs (
                    video_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    file_hash TEXT,
                    file_path TEXT,
                    deleted INTEGER DEFAULT 0,
                    members_only INTEGER DEFAULT 0,
                    privated INTEGER DEFAULT 0,
                    error TEXT,
                    duration INTEGER,
                    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
                )
            """)

            cur.execute("CREATE INDEX IF NOT EXISTS idx_file_hash ON songs(file_hash)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_channel_id ON songs(channel_id)")

    # ---------- Channels ----------

    def upsert_channel(self, channel: Channel, queue: bool = False):
        if queue:
            self._channel_queue.append(channel)
            return

        with self.cursor() as cur:
            cur.execute("""
                INSERT INTO channels (channel_id, name, org, sub_org)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    name = excluded.name,
                    org = COALESCE(excluded.org, channels.org),
                    sub_org = COALESCE(excluded.sub_org, channels.sub_org)
            """, (channel.channel_id, channel.name, channel.org, channel.sub_org))

    def get_channel(self, channel_id: str) -> Optional[Channel]:
        with self.cursor() as cur:
            cur.execute("""
                SELECT channel_id, name, org, sub_org
                FROM channels
                WHERE channel_id = ?
            """, (channel_id,))
            row = cur.fetchone()
            return Channel(**row) if row else None

    # ---------- Songs ----------

    def song_exists(self, video_id: str) -> bool:
        with self.cursor() as cur:
            cur.execute("SELECT 1 FROM songs WHERE video_id = ?", (video_id,))
            return cur.fetchone() is not None

    def hash_exists(self, file_hash: str) -> Optional[str]:
        with self.cursor() as cur:
            cur.execute("""
                SELECT video_id
                FROM songs
                WHERE file_hash = ? AND deleted = 0
                LIMIT 1
            """, (file_hash,))
            row = cur.fetchone()
            return row["video_id"] if row else None

    def add_song(self, song: Song, queue: bool = False):
        if queue:
            self._song_queue.append(song)
            return
        self._insert_song(song)

    def _insert_song(self, song: Song):
        with self.cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO songs (
                    video_id, channel_id, title, topic, available_at,
                    file_hash, file_path, deleted,
                    members_only, privated, error, duration
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                song.video_id,
                song.channel_id,
                song.title,
                song.topic,
                song.available_at,
                song.file_hash,
                song.file_path,
                int(song.deleted),
                int(song.members_only),
                int(song.privated),
                song.error,
                song.duration,
            ))

    def flush(self):
        if not self._channel_queue and not self._song_queue:
            return

        with self.cursor() as cur:
            if self._channel_queue:
                cur.executemany("""
                    INSERT INTO channels (channel_id, name, org, sub_org)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        name = excluded.name,
                        org = COALESCE(excluded.org, channels.org),
                        sub_org = COALESCE(excluded.sub_org, channels.sub_org)
                """, [
                    (c.channel_id, c.name, c.org, c.sub_org)
                    for c in self._channel_queue
                ])
                self._channel_queue.clear()

            if self._song_queue:
                cur.executemany("""
                    INSERT OR REPLACE INTO songs (
                        video_id, channel_id, title, topic, available_at,
                        file_hash, file_path, deleted,
                        members_only, privated, error, duration
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (
                        s.video_id, s.channel_id, s.title, s.topic, s.available_at,
                        s.file_hash, s.file_path,
                        int(s.deleted), int(s.members_only), int(s.privated),
                        s.error, s.duration
                    )
                    for s in self._song_queue
                ])
                self._song_queue.clear()

    def mark_deleted(self, video_id: str):
        with self.cursor() as cur:
            cur.execute(
                "UPDATE songs SET deleted = 1 WHERE video_id = ?",
                (video_id,),
            )

    def get_song(self, video_id: str) -> Optional[Song]:
        with self.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM songs
                WHERE video_id = ?
            """, (video_id,))
            row = cur.fetchone()
            return Song(**row) if row else None

    def get_latest_available_at_per_topic(self) -> Dict[str, Optional[tuple[str, str]]]:
        with self.cursor() as cur:
            cur.execute("""
                SELECT topic, video_id, available_at
                FROM songs
                WHERE deleted = 0
                AND (topic, available_at) IN (
                    SELECT topic, MAX(available_at)
                    FROM songs
                    WHERE deleted = 0
                    GROUP BY topic
                )
            """)
            rows = cur.fetchall()

        result = {r["topic"]: (r["video_id"], r["available_at"]) for r in rows}
        for topic in ("Music_Cover", "Original_Song"):
            result.setdefault(topic, None)
        return result

    def get_songs_without_file_hash(self, exclude_unavailable: bool = True) -> List[Song]:
        where = "file_hash IS NULL"
        if exclude_unavailable:
            where += """
                AND (members_only = 0 OR members_only IS NULL)
                AND (privated = 0 OR privated IS NULL)
                AND (deleted = 0 OR deleted IS NULL)
                AND (error IS NULL OR (
                    error NOT LIKE '%blocked it in your country on copyright grounds%'
                    AND error NOT LIKE 'duration_out_of_bounds%'
                    AND error NOT LIKE '%Sign in to confirm your age%'
                    AND error NOT LIKE '%The uploader has not made this video available%'
                ))
            """

        with self.cursor() as cur:
            cur.execute(f"""
                SELECT *
                FROM songs
                WHERE {where}
                ORDER BY available_at DESC, video_id ASC
            """)
            return [Song(**row) for row in cur.fetchall()]


# =========================
# Utils
# =========================

def hash_file(file_path: Path) -> str:
    sha = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()
