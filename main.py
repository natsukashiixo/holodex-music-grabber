"""Main entry point for holodex-music-grabber."""
import os
from pathlib import Path

from src.db import Database
from src.holodex import HolodexClient
from src.downloader import MusicDownloader
from src.utils import check_file_exists, verify_existing_files, process_video


def main():
    """Main function."""
    import argparse
    
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
        default=Path("Music"),
        help="Base output directory (default: Music)"
    )
    parser.add_argument(
        "--db-path",
        default="music.db",
        help="Database file path (default: music.db)"
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
            print("Verifying existing files...")
            verify_existing_files(db, downloader.base_output_dir)
        
        # Get all music videos
        print("Fetching videos from Holodex...")
        videos = client.get_all_music_videos(db=db)
        
        print(f"Found {len(videos)} music videos")
        
        # Process each video
        success_count = 0
        fail_count = 0
        
        for i, video in enumerate(videos, 1):
            print(f"\n[{i}/{len(videos)}] Processing: {video.title}")
            if process_video(video, db, downloader, skip_existing=args.skip_existing):
                success_count += 1
            else:
                fail_count += 1
        
        print(f"\n{'='*60}")
        print(f"Completed: {success_count} successful, {fail_count} failed")
        print(f"{'='*60}")
        
    finally:
        client.close()


if __name__ == "__main__":
    main()
