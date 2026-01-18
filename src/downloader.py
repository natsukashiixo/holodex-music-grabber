"""Downloader module using yt-dlp."""
import subprocess
import json
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass
import logging

from src.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class DownloadResult:
    """Result of a download operation."""
    success: bool
    file_path: Optional[Path] = None
    error: Optional[str] = None


class MusicDownloader:
    """Downloader for music using yt-dlp."""
    
    def __init__(self, base_output_dir: Path = Path("Music")):
        self.base_output_dir = base_output_dir
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_output_path(
        self,
        org: Optional[str],
        sub_org: Optional[str],
        channel_name: str,
        topic: str,
        title: str
    ) -> Path:
        """
        Generate output path: Org/Sub-org/Channel/Covers|Originals/title.mp3
        
        Args:
            org: Organization name
            sub_org: Sub-organization name
            channel_name: Channel name
            topic: "Music_Cover" or "Original_Song"
            title: Video title
        
        Returns:
            Path object for the output file
        """
        # Sanitize names for filesystem
        def sanitize(name: str) -> str:
            if not name:
                return "Unknown"
            # Remove/replace invalid filesystem characters
            invalid_chars = '<>:"/\\|?*'
            for char in invalid_chars:
                name = name.replace(char, '_')
            return name.strip()
        
        parts = [self.base_output_dir]
        
        if org:
            parts.append(sanitize(org))
        if sub_org:
            parts.append(sanitize(sub_org))
        
        parts.append(sanitize(channel_name))
        
        # Covers or Originals folder
        if topic == "Music_Cover":
            parts.append("Covers")
        elif topic == "Original_Song":
            parts.append("Originals")
        else:
            parts.append("Other")
        
        output_dir = Path(*parts)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Sanitize title for filename
        safe_title = sanitize(title)
        # Limit filename length (filesystem limit)
        if len(safe_title) > 200:
            safe_title = safe_title[:200]
        
        return output_dir / f"{safe_title}.mp3"
    
    def download(
        self,
        video_id: str,
        org: Optional[str],
        sub_org: Optional[str],
        channel_name: str,
        topic: str,
        title: str
    ) -> DownloadResult:
        """
        Download a video as MP3 using yt-dlp.
        
        Args:
            video_id: YouTube video ID
            org: Organization name
            sub_org: Sub-organization name
            channel_name: Channel name
            topic: "Music_Cover" or "Original_Song"
            title: Video title
        
        Returns:
            DownloadResult with success status and file path
        """
        output_path = self._get_output_path(org, sub_org, channel_name, topic, title)
        
        # If file already exists, skip download
        if output_path.exists():
            return DownloadResult(success=True, file_path=output_path)
        
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Build yt-dlp command
        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format", "mp3",
            "--embed-metadata",
            "--parse-metadata", "playlist_index:%(track_number)s",
            "--add-metadata",
            "-o", str(output_path),
            url
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            return DownloadResult(success=True, file_path=output_path)
        except subprocess.CalledProcessError as e:
            return DownloadResult(
                success=False,
                error=f"yt-dlp error: {e.stderr}"
            )
        except FileNotFoundError:
            return DownloadResult(
                success=False,
                error="yt-dlp not found in PATH. Please install yt-dlp."
            )
