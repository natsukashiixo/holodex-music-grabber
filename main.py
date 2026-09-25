"""Main entry point for holodex-music-grabber."""
import os
import tomllib
from pathlib import Path
from typing import Optional, Dict, Any
import logging
import time
from datetime import timedelta
import argparse

from src.db import Database
from src.holodex import HolodexClient
from src.downloader import MusicDownloader
from src.utils import check_file_exists, verify_existing_files, process_video, song_to_holodex_video, calc_eta
from src.logging_config import setup_logging, get_logger

logger = get_logger(__name__)

def load_config(config_path: Path = Path("config.toml")) -> Optional[Dict[str, Any]]:
    """
    Load configuration from TOML file.
    
    Args:
        config_path: Path to config file
        
    Returns:
        Dictionary with config values, or None if file doesn't exist
    """
    if not config_path.exists():
        return None
    
    with open(config_path, "rb") as f:
        return tomllib.load(f)

def path_type(path_str: str) -> Path:
    """Argparse type that expands and resolves paths."""
    return Path(path_str).expanduser().resolve()

def expand_path(path_str: str) -> Path:
    """
    Expand path string, handling ~ and relative paths.
    
    Args:
        path_str: Path string (may contain ~)
        
    Returns:
        Expanded Path object
    """
    return Path(path_str).expanduser().resolve()


def main():
    """Main function."""
    
    # Set up logging first
    setup_logging(log_level="INFO")
    
    # Load config file
    config = load_config()

    # Set defaults from config or fallback values
    default_output_dir = Path("Music")
    default_db_path = "music.db"
    default_cache_dir = Path("/tmp/holodex-music-grabber-cache")
    default_cache_dir.mkdir(parents=True, exist_ok=True)
    if config:
        if "Paths" in config:
            paths = config["Paths"]
            if "download_folder" in paths:
                default_output_dir = expand_path(paths["download_folder"])
            if "database_path" in paths:
                db_path_str = paths["database_path"]
                db_path_expanded = expand_path(db_path_str)
                if db_path_expanded.is_dir() or db_path_str.endswith("/"):
                    default_db_path = str(db_path_expanded / "music.db")
                else:
                    default_db_path = str(db_path_expanded)
            if "cache_folder" in paths and paths["cache_folder"]:
                default_cache_dir = expand_path(paths["cache_folder"])
                default_cache_dir.mkdir(parents=True, exist_ok=True)

    parser = argparse.ArgumentParser(
        description="Download VTuber music from Holodex using yt-dlp"
    )
    parser.add_argument(
        "--api-key",
        help="Holodex API key. Mandatory if needing to call Holodex API aka for anything but --verify-files and --retry-failed",
        default=os.getenv("HOLODEX_API_KEY")
    )
    parser.add_argument(
        "--output-dir",
        type=path_type,
        default=default_output_dir,
        help=f"Base output directory (default: {default_output_dir})"
    )
    parser.add_argument(
        "--db-path",
        default=default_db_path,
        help=f"Database file path (default: {default_db_path})"
    )
    parser.add_argument(
        "--verify-files",
        action="store_true",
        help="Verify existing files and mark missing ones as deleted"
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_false",
        default=True,
        help="Don't skip videos that are already in database (default: skips videos that already exists in db)"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Only process rows where file_hash IS NULL (retry failed/unfinished downloads); no API fetch"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N videos this run. Combined with --retry-failed, the retry queue is a "
             "stable ORDER BY, so the same N videos are picked every run as long as none of them "
             "succeed - useful for spot-checking a small batch (e.g. a new PO-token setup) before "
             "committing to a long full run."
    )
    args = parser.parse_args()

    hd_api_key = args.api_key or (config and config.get('Keys', {}).get('holodex_key'))

    if not hd_api_key:
        if any([args.retry_failed, args.verify_files]):
            raise Exception('Holodex key not set as argument, in config or in environment variable. Pls fix')

    # Initialize components
    db = Database(args.db_path)
    client = HolodexClient(api_key=hd_api_key)
    downloader = MusicDownloader(
        base_output_dir=args.output_dir,
        cache_dir=default_cache_dir
    )

    try:
        if args.verify_files:
            logger.info("Verifying existing files...")
            verify_existing_files(db, downloader.base_output_dir)

        if args.retry_failed:
            songs = db.get_songs_without_file_hash(exclude_unavailable=True)
            videos = [song_to_holodex_video(s, db) for s in songs]
            logger.info(f"Retrying {len(videos)} videos with no file_hash (excluding members-only/privated/deleted)")
        else:
            logger.info("Fetching videos from Holodex...")
            videos = client.get_all_music_videos(db=db)

        videos = list(videos) # cast into list
        if args.limit is not None:
            videos = videos[:args.limit]
        total_vid_count = len(videos) # idk just feels better having this as a constant instead of calling it every time its needed
        success_count = 0
        fail_count = 0
        total_time = 0.0
        for i, video in enumerate(videos, 1):
            start = time.time()
            if i == 1 and not args.retry_failed:
                logger.info("Processing videos (streaming from API)...")
            logger.info(f"[{i}/{total_vid_count}] Processing: {video.title}")
            if process_video(video, db, downloader, skip_existing=args.no_skip_existing, client=client):
                success_count += 1
            else:
                fail_count += 1
            end = time.time()
            ttc = end - start #ttc = time to complete :⁾
            total_time += ttc
            rate, eta = calc_eta(seconds=total_time, items_left=(total_vid_count-i), items_processed=i)
            logger.info("=" * 60)
            logger.info(f"ETA INFO: Processing speed = {rate}/s")
            logger.info(f"ETA INFO: Estimated time to completion = {timedelta(seconds=eta)}")
            logger.info("=" * 60)
        
        logger.info("=" * 60)
        logger.info(f"Finished processing {total_vid_count} in {timedelta(seconds=total_time)}")
        logger.info(f"Completed: {success_count} successful, {fail_count} failed")
        logger.info("=" * 60)
        
    finally:
        client.close()


if __name__ == "__main__":
    main()
