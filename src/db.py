"""Database module for tracking songs, channels, orgs, and file hashes."""
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class Channel:
    """Channel information."""
    channel_id: str
    name: str
    org: Optional[str] = None
    sub_org: Optional[str] = None


@dataclass
class Song:
    """Song information."""
    video_id: str
    channel_id: str
    title: str
    topic: str  # "Music_Cover" or "Original_Song"
    available_at: str
    file_hash: Optional[str] = None
    file_path: Optional[str] = None
    deleted: bool = False


class Database:
    """SQLite database for tracking music downloads."""
    
    def __init__(self, db_path: str = "music.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema."""
        # Create parent directory if it doesn't exist
        db_path_obj = Path(self.db_path)
        if db_path_obj.parent != db_path_obj:  # Not root directory
            db_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Channels table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                name TEXT,
                english_name TEXT,
                org TEXT,
                sub_org TEXT
            )
        """)
        
        # Songs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                video_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                available_at TEXT NOT NULL,
                file_hash TEXT,
                file_path TEXT,
                deleted INTEGER DEFAULT 0,
                FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
            )
        """)
        
        # File hash index for fast deduplication lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_hash ON songs(file_hash)
        """)
        
        # Channel index
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_channel_id ON songs(channel_id)
        """)
        
        conn.commit()
        conn.close()
    
    def upsert_channel(self, channel: Channel):
        """Insert or update channel information."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO channels (channel_id, name, org, sub_org)
            VALUES (?, ?, ?, ?)
        """, (channel.channel_id, channel.name, channel.org, channel.sub_org))
        conn.commit()
        conn.close()
    
    def get_channel(self, channel_id: str) -> Optional[Channel]:
        """Get channel by ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT channel_id, name, org, sub_org
            FROM channels
            WHERE channel_id = ?
        """, (channel_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return Channel(*row)
        return None

    def song_exists(self, video_id: str) -> bool:
        """Check if song already exists in database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM songs WHERE video_id = ?", (video_id,))
        exists = cursor.fetchone() is not None
        conn.close()
        return exists
    
    def hash_exists(self, file_hash: str) -> Optional[str]:
        """Check if file hash already exists. Returns video_id if found."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT video_id FROM songs
            WHERE file_hash = ? AND deleted = 0
            LIMIT 1
        """, (file_hash,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    
    def add_song(self, song: Song):
        """Add song to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO songs 
            (video_id, channel_id, title, topic, available_at, file_hash, file_path, deleted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            song.video_id,
            song.channel_id,
            song.title,
            song.topic,
            song.available_at,
            song.file_hash,
            song.file_path,
            1 if song.deleted else 0
        ))
        conn.commit()
        conn.close()
    
    def mark_deleted(self, video_id: str):
        """Mark a song as deleted."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE songs SET deleted = 1
            WHERE video_id = ?
        """, (video_id,))
        conn.commit()
        conn.close()
    
    def get_song(self, video_id: str) -> Optional[Song]:
        """Get song by video ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT video_id, channel_id, title, topic, available_at, 
                   file_hash, file_path, deleted
            FROM songs
            WHERE video_id = ?
        """, (video_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return Song(
                video_id=row[0],
                channel_id=row[1],
                title=row[2],
                topic=row[3],
                available_at=row[4],
                file_hash=row[5],
                file_path=row[6],
                deleted=bool(row[7])
            )
        return None
    
    def get_latest_available_at_per_topic(self) -> Dict[str, Optional[tuple[str, str]]]:
        """
        Get the latest available_at timestamp and video_id for each topic.
        
        Returns:
            Dictionary mapping topic to tuple of (video_id, available_at timestamp),
            or None if no entries exist for that topic
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        # Get one video_id with the maximum available_at for each topic
        # Use MIN(video_id) to deterministically pick one if multiple have same timestamp
        cursor.execute("""
            SELECT topic, MIN(video_id) as video_id, MAX(available_at) as available_at
            FROM songs
            WHERE deleted = 0
            AND (topic, available_at) IN (
                SELECT topic, MAX(available_at)
                FROM songs
                WHERE deleted = 0
                GROUP BY topic
            )
            GROUP BY topic
        """)
        rows = cursor.fetchall()
        conn.close()
        
        result = {}
        for topic, video_id, available_at in rows:
            result[topic] = (video_id, available_at)
        
        # Ensure both topics are in the result
        for topic in ["Music_Cover", "Original_Song"]:
            if topic not in result:
                result[topic] = None
        
        return result


def hash_file(file_path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            sha256.update(chunk)
    return sha256.hexdigest()
