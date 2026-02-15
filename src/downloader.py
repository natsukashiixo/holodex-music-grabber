"""Downloader module using yt-dlp."""
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

import yt_dlp

from src.logging_config import get_logger
from src.utils import fs_sanitize

logger = get_logger(__name__)

# TODO: log if video is membersonly and store in db. if no file hash + true then skip
# TODO: log if video is privated/deleted. if no file hash + true then skip
# TODO: download into cache folder then move to target?
# TODO: leverage yt-dlp built in concurrency
# TODO: use yt-dlp to grab captions (separate function)
# TODO: implement SABR+PO_Token

FAKE_MULTILINE_COMMENT = """
WARNING: [youtube] mUudSg8Cs4I: Some web_safari client https formats have been skipped as they are missing a url. YouTube is forcing SABR streaming for this client. See  https://github.com/yt-dlp/yt-dlp/issues/12482  for more details"""

@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[Path] = None
    error: Optional[str] = None

    @property
    def members_only(self) -> bool:
        return bool(
            self.error
            and "This video is available to this channel's members" in self.error
        )

    @property
    def privated(self) -> bool:
        return bool(
            self.error
            and "Private video. Sign in if you've been granted access" in self.error
        )

    @property
    def deleted(self) -> bool:
        return bool(
            self.error
            and "Video unavailable. This video has been removed by the uploader" in self.error
        )

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
        channel_id: str,
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
        
        parts = [self.base_output_dir]
        
        if org:
            parts.append(fs_sanitize(org))
        if sub_org:
            parts.append(fs_sanitize(sub_org))
        
        parts.append(fs_sanitize(f"{channel_name}_{channel_id}"))
        
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
        safe_title = fs_sanitize(title)
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
        channel_id : str,
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
        
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(output_path),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"},
                {"key": "EmbedMetadata"},
            ],
            "parse_metadata": ["playlist_index:%(track_number)s"],
        }
        
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return DownloadResult(success=True, file_path=output_path)
        except yt_dlp.utils.DownloadError as e:
            return DownloadResult(success=False, error=f"yt-dlp error: {e}")
        except Exception as e:
            return DownloadResult(success=False, error=f"yt-dlp error: {e}")
