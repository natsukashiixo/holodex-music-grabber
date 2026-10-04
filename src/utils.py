"""Utility functions for video processing and file verification."""
import shutil
import sqlite3
from pathlib import Path
import logging

from src.db import Database, Channel, Song, hash_file
from src.holodex import HolodexVideo, HolodexClient
from src.downloader import DownloadResult, MusicDownloader
from src.logging_config import get_logger
from src.path_utils import make_safe_path, reupload_suffix
from typing import Collection, Optional

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

# Two different videos colliding on the same output path already share
# channel + (sanitized) title; matching durations on top of that make it a
# likely re-upload. Byte-level dedup can never catch these - each upload is a
# separate lossy encode and yt-dlp tags each file with its own video URL/date.
REUPLOAD_DURATION_TOLERANCE_SECONDS = 1

# Holodex returns channel_id "HIDDEN" for channels it considers irrelevant
# (per the Holodex devs) - in practice mostly YouTube auto-generated art
# tracks ("Provided to YouTube by <label>"), but a few labels carry tracks that
# do belong in this collection. Those are kept, grouped by label under the
# HIDDEN channel folder; everything else is skipped before download.
HIDDEN_CHANNEL_ID = "HIDDEN"
HIDDEN_REJECTED_ERROR = "hidden_channel_rejected"
DEFAULT_HIDDEN_PROVIDER_ALLOWLIST = frozenset({
    "Sony Music Entertainment (Japan) Inc.",
    "Sony Music Labels Inc.",
    "ANYCOLOR, Inc.",
})


def is_likely_reupload(duration_a: Optional[int], duration_b: Optional[int]) -> bool:
    if duration_a is None or duration_b is None:
        return False
    return abs(duration_a - duration_b) <= REUPLOAD_DURATION_TOLERANCE_SECONDS


def reupload_path(path: Path, available_at: str, video_id: str) -> Path:
    """`path` with a _possible_reupYYMMDD suffix added to its stem, kept within filename limits."""
    suffix = reupload_suffix(available_at)
    return make_safe_path(path.with_name(f"{path.stem}{suffix}{path.suffix}"), f"{video_id}{suffix}")


def make_collision_resolver(video: HolodexVideo, db: Database, base_output_dir: Path, collision: dict):
    """
    Build the collision_resolver passed to MusicDownloader.download(). Decides
    the new download's path when its natural path is taken, and records what
    happened in `collision` so process_video can log it (and, if needed, swap
    names with the existing file) only once the download has succeeded.

    Within a likely re-upload pair the older upload keeps the clean name and
    the newer one gets _possible_reupYYMMDD. If the new video is the *older*
    one (retry runs go newest-first), it downloads to a _<video_id> path and
    process_video swaps the two files afterwards.
    """
    def resolve(occupied_path: Path) -> Path:
        fallback = occupied_path.with_name(f"{occupied_path.stem}_{video.video_id}{occupied_path.suffix}")
        owner = db.get_song_by_file_path(str(occupied_path.relative_to(base_output_dir)))

        if owner is None or owner.video_id == video.video_id:
            collision["message"] = f"Path collision for {video.video_id}: existing file is not tracked in DB, using {fallback.name}"
            collision["level"] = logging.WARNING
            return fallback

        if not is_likely_reupload(video.duration, owner.duration):
            collision["message"] = (
                f"Path collision for {video.video_id} with {owner.video_id}: same title, different content "
                f"(durations {video.duration}s vs {owner.duration}s), using {fallback.name}"
            )
            return fallback

        if video.available_at >= owner.available_at:
            target = reupload_path(occupied_path, video.available_at, video.video_id)
            collision["message"] = (
                f"Likely re-upload of {owner.video_id} ({owner.available_at[:10]}, "
                f"Δdur={abs(video.duration - owner.duration)}s): {video.title} -> {target.name}"
            )
            return target

        collision["swap_with"] = (owner, occupied_path)
        return fallback

    return resolve


def swap_with_reupload(owner: Song, clean_path: Path, new_path: Path, db: Database, base_output_dir: Path) -> Path:
    """
    The just-downloaded video is older than `owner`, which holds the clean name:
    relabel owner's file as the re-upload and move the new file onto the clean
    name. Returns the new file's final path.
    """
    if not clean_path.exists():
        return new_path
    owner_target = reupload_path(clean_path, owner.available_at, owner.video_id)
    if owner_target.exists():
        owner_target = owner_target.with_name(f"{owner_target.stem}_{owner.video_id}{owner_target.suffix}")
    shutil.move(str(clean_path), str(owner_target))
    db.update_song_file_path(owner.video_id, str(owner_target.relative_to(base_output_dir)))
    shutil.move(str(new_path), str(clean_path))
    return clean_path

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
    client: Optional['HolodexClient'] = None,  # type: ignore
    hidden_provider_allowlist: Collection[str] = DEFAULT_HIDDEN_PROVIDER_ALLOWLIST,
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
        # Checked first: rejected historic rows keep their file_hash, and must
        # not hit the "file missing - re-download" branch below.
        if existing.error and existing.error.startswith(HIDDEN_REJECTED_ERROR):
            logger.debug(f"Skipping (HIDDEN channel, label not allowlisted): {video.title} ({video.video_id})")
            return True
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
    
    # If we don't have org/suborg and have a client, try querying the channel endpoint.
    # This only fires while org/sub_org are still NULL - once a channel has both set,
    # they're frozen forever and never re-checked against Holodex again. That's
    # intentional (see writeup.md, 2026-09-26): orgs/suborgs churn over time
    # (graduations, restructuring) and it's not worth tracking - channel_id is the
    # durable key, not this.
    # "HIDDEN" isn't a real channel - querying it only wastes an API call.
    if (org is None or sub_org is None) and client and video.channel_id != HIDDEN_CHANNEL_ID:
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

    channel_subfolder = None
    result = None
    if video.channel_id == HIDDEN_CHANNEL_ID:
        try:
            provided_by = downloader.fetch_provided_by(video.video_id)
        except Exception as e:
            # Same error text a download would have produced, so the failure
            # branch below still classifies deleted/privated/members-only.
            result = DownloadResult(success=False, error=f"HIDDEN-channel label lookup failed: {e}")
        else:
            if provided_by not in hidden_provider_allowlist:
                logger.info(
                    f"Skipping HIDDEN-channel video provided by {provided_by or 'no label'} "
                    f"(not allowlisted): {video.title} ({video.video_id})"
                )
                db.add_song(Song(
                    video_id=video.video_id,
                    channel_id=video.channel_id,
                    title=video.title,
                    topic=video.topic,
                    available_at=video.available_at,
                    duration=video.duration,
                    error=f"{HIDDEN_REJECTED_ERROR} (provided by {provided_by or 'no label'})",
                ))
                return True
            channel_subfolder = provided_by

    # Download the video
    collision: dict = {}
    if result is None:
        logger.info(f"Downloading: {video.title}")
        result = downloader.download(
            video_id=video.video_id,
            org=org,
            sub_org=sub_org,
            channel_name=channel_name,
            channel_id=video.channel_id,
            topic=video.topic,
            title=video.title,
            collision_resolver=make_collision_resolver(video, db, downloader.base_output_dir, collision),
            channel_subfolder=channel_subfolder,
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

    if "message" in collision:
        logger.log(collision.get("level", logging.INFO), collision["message"])

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

    if "swap_with" in collision:
        owner, clean_path = collision["swap_with"]
        result.file_path = swap_with_reupload(owner, clean_path, result.file_path, db, downloader.base_output_dir)
        logger.info(
            f"Likely re-upload pair: {owner.video_id} ({owner.available_at[:10]}) re-uploads this older video "
            f"{video.video_id} ({video.available_at[:10]}); relabelled it and took the original name: {result.file_path.name}"
        )

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
