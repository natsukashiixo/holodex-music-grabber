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

# Duration gate for process_video(). Hard bounds are checked *before* handing a
# video to yt-dlp, since that's the expensive/bandwidth-costing step: <5s is
# almost certainly a data glitch, >3600s (1hr) is almost certainly a mistagged
# livestream/zatsudan rather than a song. Soft bounds are informational only
# (logged, not persisted) for content that downloads fine but is unusual enough
# to be worth a manual glance later (e.g. long medleys).
DURATION_HARD_MIN_SECONDS = 5
DURATION_HARD_MAX_SECONDS = 3600
DURATION_SOFT_MIN_SECONDS = 30
DURATION_SOFT_MAX_SECONDS = 1200

def song_to_holodex_video(song: Song, db: Database) -> HolodexVideo:
    """Build a HolodexVideo from a DB Song (e.g. for --retry-failed). Fills channel name/org/sub_org from DB."""
    ch = db.get_channel(song.channel_id)
    return HolodexVideo(
        video_id=song.video_id,
        channel_id=song.channel_id,
        title=song.title,
        topic=song.topic,
        available_at=song.available_at,
        channel_name=ch.name if ch else None,
        org=ch.org if ch else None,
        sub_org=ch.sub_org if ch else None,
        duration=song.duration,
    )


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
    existing = db.get_song(video.video_id) if skip_existing else None
    if skip_existing and existing:
        if existing.file_hash:
            # Verify file still exists
            if existing.file_path and check_file_exists(downloader.base_output_dir / existing.file_path):
                logger.debug(f"Already exists: {video.title} ({video.video_id})")
                return True
            # File missing but hash exists - mark as deleted and retry
            logger.warning(f"File missing for {video.title}, will retry download")
            db.mark_deleted(video.video_id)
        elif existing.members_only or existing.privated or existing.deleted:
            # Already known unavailable; don't retry
            logger.debug(f"Skipping (members-only/privated/deleted): {video.title} ({video.video_id})")
            return True
    
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

    # Pre-download hard duration gate - never hand an out-of-bounds video to yt-dlp
    if video.duration is not None and (
        video.duration < DURATION_HARD_MIN_SECONDS or video.duration > DURATION_HARD_MAX_SECONDS
    ):
        logger.warning(f"Skipping download, duration {video.duration}s out of bounds: {video.title} ({video.video_id})")
        song = Song(
            video_id=video.video_id,
            channel_id=video.channel_id,
            title=video.title,
            topic=video.topic,
            available_at=video.available_at,
            file_hash=None,
            file_path=None,
            duration=video.duration,
            error=f"duration_out_of_bounds ({video.duration}s)",
        )
        db.add_song(song)
        return False

    # Download the video
    logger.info(f"Downloading: {video.title}")
    result = downloader.download(
        video_id=video.video_id,
        org=org,
        sub_org=sub_org,
        channel_name=channel_name,
        channel_id=video.channel_id,
        topic=video.topic,
        title=video.title
    )
    
    # Always add/update song in DB, even on failure (with NULL file_hash)
    # This allows cron to pick up where it left off; store members_only/privated/deleted so we skip retries
    if not result.success:
        logger.error(f"Download failed: {result.error}")
        if result.members_only:
            logger.info(f"  -> members-only: {video.video_id}")
        if result.privated:
            logger.info(f"  -> privated: {video.video_id}")
        if result.deleted:
            logger.info(f"  -> deleted: {video.video_id}")
        song = Song(
            video_id=video.video_id,
            channel_id=video.channel_id,
            title=video.title,
            topic=video.topic,
            available_at=video.available_at,
            file_hash=None,
            file_path=None,
            members_only=result.members_only,
            privated=result.privated,
            deleted=result.deleted,
            error=result.error,
            duration=video.duration,
        )
        db.add_song(song)
        return False

    if not result.file_path or not result.file_path.exists():
        logger.error(f"Download completed but file not found: {video.title}")
        song = Song(
            video_id=video.video_id,
            channel_id=video.channel_id,
            title=video.title,
            topic=video.topic,
            available_at=video.available_at,
            file_hash=None,
            file_path=None,
            duration=video.duration,
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
        # Strict dedup: remove the newly-downloaded duplicate file, keep the canonical
        # row's file_hash/deleted=0 so hash_exists() keeps resolving to it.
        result.file_path.unlink(missing_ok=True)
        song = Song(
            video_id=video.video_id,
            channel_id=video.channel_id,
            title=video.title,
            topic=video.topic,
            available_at=video.available_at,
            file_hash=file_hash,
            file_path=None,
            deleted=True,
            error=f"duplicate content of {duplicate_video_id}; file removed",
            duration=video.duration,
        )
        db.add_song(song)
        logger.info(f"✓ Processed (duplicate, file removed): {video.title}")
        return True

    if video.duration is not None and (
        (DURATION_HARD_MIN_SECONDS <= video.duration < DURATION_SOFT_MIN_SECONDS)
        or (DURATION_SOFT_MAX_SECONDS < video.duration <= DURATION_HARD_MAX_SECONDS)
    ):
        logger.warning(f"Unusual duration ({video.duration}s), worth a glance: {video.title} ({video.video_id})")

    # Add song to database with file_hash (marks as successfully processed)
    song = Song(
        video_id=video.video_id,
        channel_id=video.channel_id,
        title=video.title,
        topic=video.topic,
        available_at=video.available_at,
        file_hash=file_hash,
        file_path=str(result.file_path.relative_to(downloader.base_output_dir)),
        duration=video.duration,
    )
    db.add_song(song)

    logger.info(f"✓ Processed: {video.title}")
    return True

def calc_eta(seconds: float, items_left: int, items_processed: int) -> tuple[float, float]:
    if items_processed == 0 or seconds == 0:
        return float('inf')  # Can't estimate if nothing has been processed
    
    rate = items_processed / seconds  # items per second
    eta = items_left / rate  # remaining time in seconds
    return rate, eta
