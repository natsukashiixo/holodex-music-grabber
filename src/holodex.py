"""Holodex API client for querying videos."""
import httpx
from typing import List, Dict, Optional, Iterator, Tuple, Any
from dataclasses import dataclass
import logging
import time

from src.db import Database, Song, Channel
from src.logging_config import get_logger
from src.path_utils import sanitize_suborg

logger = get_logger(__name__)

# Response cache TTL in seconds (5 minutes)
CACHE_TTL_SECONDS = 300

class RateLimiter:
    """Rate limiter to control API request frequency."""
    
    def __init__(self, rate_per_sec: float):
        """
        Initialize rate limiter.
        
        Args:
            rate_per_sec: Maximum number of requests per second
        """
        self.interval = 1.0 / rate_per_sec
        self.next_allowed = time.monotonic()
    
    def wait(self):
        """Wait if necessary to respect rate limit."""
        now = time.monotonic()
        if now < self.next_allowed:
            sleep_time = self.next_allowed - now
            time.sleep(sleep_time)
        self.next_allowed = max(self.next_allowed + self.interval, time.monotonic())


@dataclass
class HolodexVideo:
    """Video information from Holodex API."""
    video_id: str
    channel_id: str
    title: str
    topic: str  # "Music_Cover" or "Original_Song"
    available_at: str
    channel_name: Optional[str] = None
    org: Optional[str] = None
    sub_org: Optional[str] = None
    duration: Optional[int] = None # seconds

@dataclass
class HolodexChannel:
    """Channel information from Holodex API."""
    channel_id: str
    name: str
    english_name: Optional[str] = None # either not accessed correctly here, or not assigned properly in db
    org: Optional[str] = None
    sub_org: Optional[str] = None

    def __post_init__(self):
        if not self.sub_org:
            self.sub_org = "zzFALLBACK"    
        self.sub_org = sanitize_suborg(self.sub_org)


class HolodexClient:
    """Client for Holodex API."""
    
    BASE_URL = "https://holodex.net/api/v2"
    RATE_LIMIT_SECONDS = 2.0
    
    def __init__(self, api_key: Optional[str] = None, cache_ttl_seconds: float = CACHE_TTL_SECONDS):
        self.api_key = api_key
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[Tuple[str, ...], Tuple[float, Any]] = {}  # (key) -> (expiry_ts, value)
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            headers={"X-APIKEY": api_key} if api_key else {},
            timeout=30.0
        )
        # Initialize rate limiter (1 request per RATE_LIMIT_SECONDS)
        self.rate_limiter = RateLimiter(rate_per_sec=1.0 / self.RATE_LIMIT_SECONDS)
    
    def _cache_get(self, key: Tuple[str, ...]) -> Optional[Any]:
        now = time.monotonic()
        if key in self._cache:
            expiry, value = self._cache[key]
            if now < expiry:
                return value
            del self._cache[key]
        return None
    
    def _cache_set(self, key: Tuple[str, ...], value: Any) -> None:
        self._cache[key] = (time.monotonic() + self.cache_ttl, value)
    
    def query_videos(
        self,
        channel_id: Optional[str] = None,
        topic: Optional[str] = None,
        org: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        status: str = "past",
        from_date: Optional[str] = None
    ) -> List[HolodexVideo]:
        """
        Query videos from Holodex API.
        
        Args:
            channel_id: Filter by channel ID
            topic: Filter by topic (e.g., "Music_Cover", "Original_Song")
            org: Filter by organization
            limit: Maximum results (max 50)
            offset: Pagination offset
            status: Video status (default: "past")
            from_date: ISO8601 date string for minimum available_at
        
        Returns:
            List of HolodexVideo objects
        """
        params = {
            "status": status,
            "limit": min(limit, 50),
            "offset": offset,
        }
        
        if channel_id:
            params["channel_id"] = channel_id
        if topic:
            params["topic"] = topic
        if org:
            params["org"] = org
        if from_date:
            params["from"] = from_date
        
        cache_key = ("videos", str(params.get("topic")), str(params.get("limit")), str(params.get("offset")), str(params.get("from", "")))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        
        # Rate limit API call
        self.rate_limiter.wait()
        
        try:
            response = self.client.get("/videos", params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error querying videos: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error querying videos: {e}")
            raise
        
        videos = []
        for item in data:
            # Extract topic - API uses topic_id field, but may also have songs array
            topic_value = None
            
            # First check topic_id (primary field)
            if "topic_id" in item:
                topic_id = item["topic_id"]
                if topic_id in ["Music_Cover", "Original_Song"]:
                    topic_value = topic_id
            
            # Fallback to songs array if topic_id not found
            if not topic_value and "songs" in item:
                for song in item.get("songs", []):
                    if song.get("name") in ["Music_Cover", "Original_Song"]:
                        topic_value = song["name"]
                        break
            
            # Fallback to topic field (legacy)
            if not topic_value and "topic" in item:
                topic_value = item["topic"]
            
            # Only include if it's a music-related topic
            if topic_value in ["Music_Cover", "Original_Song"]:
                # Extract channel info from response (channel object is included when querying by topic)
                channel_info = item.get("channel", {})
                video = HolodexVideo(
                    video_id=item["id"],
                    channel_id=item.get("channel_id") or channel_info.get("id", ""),
                    title=item["title"],
                    topic=topic_value,
                    available_at=item["available_at"],
                    channel_name=channel_info.get("name"),
                    org=channel_info.get("org"),
                    sub_org=channel_info.get("suborg"),
                    duration=item.get("duration"),
                )
                videos.append(video)
        
        self._cache_set(cache_key, videos)
        return videos
    
    def get_all_music_videos(
        self,
        db: Optional['Database'] = None  # type: ignore
    ) -> Iterator[HolodexVideo]:
        """
        Get all music videos (covers and originals) since last check.
        Uses topic-based queries with timestamp filtering for efficiency.
        Yields videos one-by-one and queues initial partial song/channel rows to db
        so we do not hold the full result set in memory.
        
        Args:
            db: Database instance for latest timestamps and to queue initial partial rows
        
        Yields:
            HolodexVideo (deduplicated by video_id)
        """
        seen_video_ids: set = set()
        
        # Get latest timestamps and video_ids per topic from database
        latest_per_topic = {}
        if db:
            latest_per_topic = db.get_latest_available_at_per_topic()
        
        try:
            for topic in ["Music_Cover", "Original_Song"]:
                latest_info = latest_per_topic.get(topic) if latest_per_topic else None
                from_date = None
                stop_video_id = None
                
                if latest_info:
                    stop_video_id, from_date = latest_info
                    logger.debug(f"Querying {topic} from {from_date}, stopping at {stop_video_id}")
                else:
                    logger.debug(f"Querying all {topic} videos (no previous entries)")
                
                offset = 0
                while True:
                    videos = self.query_videos(
                        topic=topic,
                        limit=50,
                        offset=offset,
                        from_date=from_date
                    )
                    if not videos:
                        break
                    
                    logger.debug(f"Retrieved {len(videos)} {topic} videos (offset: {offset})")
                    
                    found_stop_video = False
                    # Queue partial rows for this page, then flush before yielding so process_video overwrites with full data
                    page_to_yield: List[HolodexVideo] = []
                    for video in videos:
                        if stop_video_id and video.video_id == stop_video_id:
                            found_stop_video = True
                            logger.debug(f"Reached stop video {stop_video_id} for {topic}")
                            break
                        
                        if video.video_id not in seen_video_ids:
                            seen_video_ids.add(video.video_id)
                            if db:
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
                                db.add_song(song, queue=True)
                                channel_name = video.channel_name or "Unknown"
                                channel = Channel(
                                    channel_id=video.channel_id,
                                    name=channel_name,
                                    org=video.org,
                                    sub_org=video.sub_org,
                                )
                                db.upsert_channel(channel, queue=True)
                            page_to_yield.append(video)
                    if db:
                        db.flush()
                    for v in page_to_yield:
                        yield v
                    if found_stop_video:
                        break
                    if len(videos) < 50:
                        break
                    offset += 50
        finally:
            if db:
                db.flush()
    
    def query_channel(self, channel_id: str) -> HolodexChannel:
        '''Queries channel endpoint using channel_id
        Returns HolodexChannel dataclass'''
        cache_key = ("channel", channel_id)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        
        # Rate limit API call
        self.rate_limiter.wait()
        
        try:
            response = self.client.get(f"/channels/{channel_id}")
            response.raise_for_status()
            data = response.json()
            channel = HolodexChannel(
                channel_id=data["id"],
                name=data["name"],
                english_name=data["english_name"],
                org=data["org"],
                sub_org=data["suborg"],
            )
            self._cache_set(cache_key, channel)
            return channel
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error querying channel {channel_id}: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error querying channel {channel_id}: {e}")
            raise

    def close(self):
        """Close the HTTP client."""
        self.client.close()
