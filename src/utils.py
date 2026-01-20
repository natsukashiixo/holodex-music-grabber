"""Utility functions for video processing and file verification."""
import sqlite3
from pathlib import Path
import logging

from src.db import Database, Channel, Song, hash_file
from src.holodex import HolodexVideo, HolodexClient
from src.downloader import MusicDownloader
from src.logging_config import get_logger
from typing import Optional

logger = get_logger(__name__)

# TODO: verify that current upsert logic in process_video() can remain unchanged

def check_file_exists(file_path: Path) -> bool:
    """Check if file exists and is not deleted."""
    return file_path.exists() and file_path.is_file()


def verify_existing_files(db: Database, base_dir: Path):
    """Check existing files in database and mark as deleted if missing."""
    conn = sqlite3.connect(db.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT video_id, file_path FROM songs WHERE deleted = 0 AND file_path IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()
    
    for video_id, file_path in rows:
        if file_path and not check_file_exists(base_dir / file_path):
            logger.warning(f"File missing, marking as deleted: {video_id}")
            db.mark_deleted(video_id)


def process_video(
    video: HolodexVideo,
    db: Database,
    downloader: MusicDownloader,
    skip_existing: bool = True,
    client: Optional['HolodexClient'] = None  # type: ignore
) -> bool:
    """
    Process a single video: download if needed, hash, deduplicate, store in DB.
    Uses file_hash as source of truth - if NULL, video needs to be downloaded.
    
    Returns:
        True if successfully processed, False otherwise
    """
    # Check if already successfully processed (has file_hash)
    if skip_existing:
        existing = db.get_song(video.video_id)
        if existing and existing.file_hash:
            # Verify file still exists
            if existing.file_path and check_file_exists(downloader.base_output_dir / existing.file_path):
                logger.debug(f"Already exists: {video.title} ({video.video_id})")
                return True
            # File missing but hash exists - mark as deleted and retry
            logger.warning(f"File missing for {video.title}, will retry download")
            db.mark_deleted(video.video_id)
    
    # Get channel info from DB or query API if needed
    # Note: /videos endpoint doesn't include channel details (org/suborg), so we need to query separately
    db_channel = db.get_channel(video.channel_id)
    channel_name = db_channel.name if db_channel else video.channel_name or "Unknown"
    org = db_channel.org if db_channel else video.org
    sub_org = db_channel.sub_org if db_channel else video.sub_org
    
    # If we don't have org/suborg and have a client, try querying the channel endpoint
    if (org is None or sub_org is None) and client:
        try:
            holodex_channel = client.query_channel(video.channel_id)
            channel_name = holodex_channel.name or channel_name
            org = holodex_channel.org or org
            sub_org = holodex_channel.sub_org or sub_org
        except Exception as e:
            logger.debug(f"Could not query channel info for {video.channel_id}: {e}")
    
    # Update channel info in DB
    channel = Channel(
        channel_id=video.channel_id,
        name=channel_name,
        org=org,
        sub_org=sub_org
    )
    db.upsert_channel(channel)
    
    # Download the video
    logger.info(f"Downloading: {video.title}")
    result = downloader.download(
        video_id=video.video_id,
        org=org,
        sub_org=sub_org,
        channel_name=channel_name,
        topic=video.topic,
        title=video.title
    )
    
    # Always add/update song in DB, even on failure (with NULL file_hash)
    # This allows cron to pick up where it left off
    if not result.success:
        logger.error(f"Download failed: {result.error}")
        # Add to DB with NULL file_hash so we can retry later
        song = Song(
            video_id=video.video_id,
            channel_id=video.channel_id,
            title=video.title,
            topic=video.topic,
            available_at=video.available_at,
            file_hash=None,
            file_path=None
        )
        db.add_song(song)
        return False
    
    if not result.file_path or not result.file_path.exists():
        logger.error(f"Download completed but file not found: {video.title}")
        # Add to DB with NULL file_hash so we can retry later
        song = Song(
            video_id=video.video_id,
            channel_id=video.channel_id,
            title=video.title,
            topic=video.topic,
            available_at=video.available_at,
            file_hash=None,
            file_path=None
        )
        db.add_song(song)
        return False
    
    # Calculate file hash
    file_hash = hash_file(result.file_path)
    
    # Check for duplicates
    duplicate_video_id = db.hash_exists(file_hash)
    if duplicate_video_id and duplicate_video_id != video.video_id:
        logger.warning(f"Duplicate detected! Hash matches {duplicate_video_id}")
        logger.warning(f"  Current: {video.title} ({video.video_id})")
        existing = db.get_song(duplicate_video_id)
        if existing:
            logger.warning(f"  Existing: {existing.title} ({duplicate_video_id})")
        # Still add to DB but note it's a duplicate
        # You might want to delete the file here if you want strict deduplication
    
    # Add song to database with file_hash (marks as successfully processed)
    song = Song(
        video_id=video.video_id,
        channel_id=video.channel_id,
        title=video.title,
        topic=video.topic,
        available_at=video.available_at,
        file_hash=file_hash,
        file_path=str(result.file_path.relative_to(downloader.base_output_dir))
    )
    db.add_song(song)
    
    logger.info(f"✓ Processed: {video.title}")
    return True
