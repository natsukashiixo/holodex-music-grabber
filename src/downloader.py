"""Downloader module using yt-dlp."""
import shutil
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

import yt_dlp

from src.logging_config import get_logger
from src.path_utils import build_relative_song_path, make_safe_path

logger = get_logger(__name__)

# TODO: implement total count + current download nr in the --retry thing


def _error_matches(error: Optional[str], *phrases: str) -> bool:
    """True if `error` is set and contains any of `phrases`. Shared by DownloadResult
    properties and scripts/backfill_error_flags.py so classification can never drift
    between the two."""
    if not error:
        return False
    return any(phrase in error for phrase in phrases)


@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[Path] = None
    error: Optional[str] = None

    @property
    def members_only(self) -> bool:
        return _error_matches(
            self.error,
            "This video is available to this channel's members",
            "members-only content like this video",
        )

    @property
    def privated(self) -> bool:
        return _error_matches(
            self.error,
            "Private video. Sign in if you've been granted access",
        )

    @property
    def deleted(self) -> bool:
        return _error_matches(
            self.error,
            "Video unavailable. This video has been removed by the uploader",
            "Video unavailable. This video is not available",
        )

    @property
    def georestricted(self) -> bool:
        return _error_matches(
            self.error,
            "who has blocked it in your country on copyright grounds",
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
        relative_path = build_relative_song_path(
            org=org,
            sub_org=sub_org,
            channel_name=channel_name,
            channel_id=channel_id,
            topic=topic,
            title=title,
        )
        output_path = self.base_output_dir / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path
    
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
