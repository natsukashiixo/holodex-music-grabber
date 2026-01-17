"""Utility functions for video processing and file verification."""
import sqlite3
from pathlib import Path

from src.db import Database, Channel, Song, hash_file
from src.holodex import HolodexVideo
from src.downloader import MusicDownloader


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
            print(f"File missing, marking as deleted: {video_id}")
            db.mark_deleted(video_id)


def process_video(
    video: HolodexVideo,
    db: Database,
    downloader: MusicDownloader,
    skip_existing: bool = True
) -> bool:
    """
    Process a single video: download if needed, hash, deduplicate, store in DB.
    
    Returns:
        True if successfully processed, False otherwise
    """
    # Check if already in database
    if skip_existing and db.song_exists(video.video_id):
        existing = db.get_song(video.video_id)
        if existing and existing.file_path and check_file_exists(Path(existing.file_path)):
            print(f"Already exists: {video.title} ({video.video_id})")
            return True
    
    # Download the video
    print(f"Downloading: {video.title}")
    result = downloader.download(
        video_id=video.video_id,
        org=video.org,
        sub_org=video.sub_org,
        channel_name=video.channel_name,
        topic=video.topic,
        title=video.title
    )
    
    if not result.success:
        print(f"Download failed: {result.error}")
        return False
    
    if not result.file_path or not result.file_path.exists():
        print(f"Download completed but file not found: {video.title}")
        return False
    
    # Calculate file hash
    file_hash = hash_file(result.file_path)
    
    # Check for duplicates
    duplicate_video_id = db.hash_exists(file_hash)
    if duplicate_video_id and duplicate_video_id != video.video_id:
        print(f"Duplicate detected! Hash matches {duplicate_video_id}")
        print(f"  Current: {video.title} ({video.video_id})")
        existing = db.get_song(duplicate_video_id)
        if existing:
            print(f"  Existing: {existing.title} ({duplicate_video_id})")
        # Still add to DB but note it's a duplicate
        # You might want to delete the file here if you want strict deduplication
    
    # Update channel info
    channel = Channel(
        channel_id=video.channel_id,
        name=video.channel_name,
        org=video.org,
        sub_org=video.sub_org
    )
    db.upsert_channel(channel)
    
    # Add song to database
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
    
    print(f"✓ Processed: {video.title}")
    return True
