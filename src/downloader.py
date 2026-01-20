"""Downloader module using yt-dlp."""
import subprocess
import json
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass
import logging

from src.logging_config import get_logger

logger = get_logger(__name__)

# TODO: add channel ID to folder name
# TODO: if suborg is greater than 2 characters, strip them otherwise pass
# TODO: set up js runtime for yt-dlp
# TODO: log if video is membersonly and store in db. if no file hash + true then skip
# TODO: log if video is privated/deleted. if no file hash + true then skip
# TODO: download into cache folder then move to target?
# TODO: use yt-dlp to grab captions for starters, use youtube data api if its unreliable

# TODO: cloudflare solver failing as well? need to set up a sandboxed yt account?
FAKE_MULTILINE_COMMENT = """2026-01-19 21:52:09 [ERROR   ] src.utils: Download failed: yt-dlp error: WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is enabled by default; to use another runtime add  --js-runtimes RUNTIME[:PATH]  to your command/config. YouTube extraction without a JS runtime has been deprecated, and some formats may be missing. See  https://github.com/yt-dlp/yt-dlp/wiki/EJS  for details on installing one
WARNING: [youtube] mUudSg8Cs4I: Some web_safari client https formats have been skipped as they are missing a url. YouTube is forcing SABR streaming for this client. See  https://github.com/yt-dlp/yt-dlp/issues/12482  for more details
WARNING: [youtube] mUudSg8Cs4I: Signature solving failed: Some formats may be missing. Ensure you have a supported JavaScript runtime and challenge solver script distribution installed. Review any warnings presented before this message. For more details, refer to  https://github.com/yt-dlp/yt-dlp/wiki/EJS
WARNING: [youtube] mUudSg8Cs4I: n challenge solving failed: Some formats may be missing. Ensure you have a supported JavaScript runtime and challenge solver script distribution installed. Review any warnings presented before this message. For more details, refer to  https://github.com/yt-dlp/yt-dlp/wiki/EJS
WARNING: [youtube] mUudSg8Cs4I: Some web client https formats have been skipped as they are missing a url. YouTube is forcing SABR streaming for this client. See  https://github.com/yt-dlp/yt-dlp/issues/12482  for more details
ERROR: The downloaded file is empty"""

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
