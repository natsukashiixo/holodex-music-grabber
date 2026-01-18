"""Main entry point for holodex-music-grabber."""
import os
import tomllib
from pathlib import Path
from typing import Optional, Dict, Any
import logging

from src.db import Database
from src.holodex import HolodexClient
from src.downloader import MusicDownloader
from src.utils import check_file_exists, verify_existing_files, process_video
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
    import argparse
    
    # Set up logging first
    setup_logging(log_level="INFO")
    
    # Load config file
    config = load_config()
    
    # Set defaults from config or fallback values
    default_output_dir = Path("Music")
    default_db_path = "music.db"
    
    if config and "Paths" in config:
        paths = config["Paths"]
        if "download_folder" in paths:
            default_output_dir = expand_path(paths["download_folder"])
        if "database_path" in paths:
            db_path_str = paths["database_path"]
            # If it's a directory, append music.db, otherwise use as-is
            db_path_expanded = expand_path(db_path_str)
            if db_path_expanded.is_dir() or db_path_str.endswith("/"):
                default_db_path = str(db_path_expanded / "music.db")
            else:
                default_db_path = str(db_path_expanded)
    
    parser = argparse.ArgumentParser(
        description="Download VTuber music from Holodex using yt-dlp"
    )
    parser.add_argument(
        "--api-key",
        help="Holodex API key (optional but recommended)",
        default=os.getenv("HOLODEX_API_KEY")
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
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
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip videos that are already in database (default: True)"
    )
    
    args = parser.parse_args()
    
    # Initialize components
    db = Database(args.db_path)
    client = HolodexClient(api_key=args.api_key)
    downloader = MusicDownloader(base_output_dir=args.output_dir)
    
    try:
        # Verify existing files if requested
        if args.verify_files:
            logger.info("Verifying existing files...")
            verify_existing_files(db, downloader.base_output_dir)
        
        # Get all music videos
        logger.info("Fetching videos from Holodex...")
        videos = client.get_all_music_videos(db=db)
        
        logger.info(f"Found {len(videos)} music videos")
        
        # Process each video
        success_count = 0
        fail_count = 0
        
        for i, video in enumerate(videos, 1):
            logger.info(f"[{i}/{len(videos)}] Processing: {video.title}")
            if process_video(video, db, downloader, skip_existing=args.skip_existing, client=client):
                success_count += 1
            else:
                fail_count += 1
        
        logger.info("=" * 60)
        logger.info(f"Completed: {success_count} successful, {fail_count} failed")
        logger.info("=" * 60)
        
    finally:
        client.close()


if __name__ == "__main__":
    main()
