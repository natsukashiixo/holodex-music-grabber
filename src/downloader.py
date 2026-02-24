"""Downloader module using yt-dlp."""
import shutil
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

import yt_dlp

from src.logging_config import get_logger
from src.path_utils import fs_sanitize, make_safe_path

logger = get_logger(__name__)

# TODO: 2026-02-16 12:02:25 [ERROR   ] src.utils: Download failed: ERROR: [youtube] F-3M_aotvcE: Video unavailable. This video contains content from Sony Music Entertainment (Japan) Inc., who has blocked it in your country on copyright grounds
# TODO: implement total count + current download nr in the --retry thing


@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[Path] = None
    error: Optional[str] = None

    @property
    def members_only(self) -> bool:
        return bool(
            self.error
            and "This video is available to this channel's members" or "members-only content like this video" in self.error
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
            and "Video unavailable. This video has been removed by the uploader" or "Video unavailable. This video is not available" in self.error
        )
    
    @property
    def georestricted(self) -> bool:
        return bool(
            self.error
            and "who has blocked it in your country on copyright grounds" in self.error
        )

class MusicDownloader:
    """Downloader for music using yt-dlp.
    Uses a single yt-dlp instance (session reuse); downloads to cache then moves to target.
    Concurrency: one download at a time; yt-dlp can do parallel fetches internally if needed."""

    def __init__(
        self,
        base_output_dir: Path,
        cache_dir: Path = Path("cache"),
        po_token: Optional[str] = None,
        enforce_sleep: bool = True, # to help with rate limiting, defaulting to true for the time being
    ):
        self.base_output_dir = base_output_dir
        self.cache_dir = cache_dir
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Build opts once; download to cache then move so outtmpl is fixed per session
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(self.cache_dir / "%(id)s.%(ext)s"),
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"},
                {"key": "FFmpegMetadata"},
            ],
            "parse_metadata": ["playlist_index:%(track_number)s"],
            #"cookiesfrombrowser": ('firefox',),
            #"verbose": True,
            "remote-components": "ejs:github"
        }
        if po_token and po_token.strip():
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["default", "mweb"],
                    "po_token": [f"mweb.gvs+{po_token.strip()}"],
                }
            }
            logger.debug("Using YouTube PO token for GVS (SABR)")
        else:
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": ["default", "mweb"],
                }
            }
        if enforce_sleep:
            opts['max_sleep_interval'] = 20.0
            opts['sleep_interval'] = 10.0
            opts['sleep_interval_requests'] = 0.75
            opts['sleep_interval_subtitles'] = 5.0
        self._ydl = yt_dlp.YoutubeDL(opts)
    
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
        
        # Channel folder always includes channel_id so same-name channels don't collide
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
        output_path = self._get_output_path(org, sub_org, channel_name, channel_id, topic, title)
        safe_output_path = make_safe_path(output_path, video_id)
        safe_output_path.parent.mkdir(parents=True, exist_ok=True)

        # If file already exists, skip download
        if output_path.exists():
            return DownloadResult(success=True, file_path=output_path)

        url = f"https://www.youtube.com/watch?v={video_id}"
        # first check if cache file exists
        cache_file = self.cache_dir / f"{video_id}.mp3"
        if cache_file.exists():
            shutil.move(str(cache_file), str(safe_output_path))
            return DownloadResult(success=True, file_path=safe_output_path)

        try:
            self._ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            return DownloadResult(success=False, error=str(e))
        except Exception as e:
            return DownloadResult(success=False, error=f"yt-dlp error: {e}")
        
        if not cache_file.exists():
            # Fallback: any video_id.* in cache (e.g. different ext before postprocessor)
            candidates = list(self.cache_dir.glob(f"{video_id}.*"))
            cache_file = candidates[0] if candidates else None
        if not cache_file or not cache_file.exists():
            return DownloadResult(success=False, error="Download completed but cache file not found")

        shutil.move(str(cache_file), str(safe_output_path))
        return DownloadResult(success=True, file_path=safe_output_path)
