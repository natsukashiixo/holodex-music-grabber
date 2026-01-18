"""Holodex API client for querying videos."""
import enum
import httpx
from typing import List, Dict, Optional
from dataclasses import dataclass

from src.db import Database


@dataclass
class HolodexVideo:
    """Video information from Holodex API."""
    video_id: str
    channel_id: str
    title: str
    topic: str  # "Music_Cover" or "Original_Song"
    available_at: str

@dataclass
class HolodexChannel:
    """Channel information from Holodex API."""
    channel_id: str
    name: str
    english_name: Optional[str] = None
    type: Optional[enum.Enum] = enum.Enum(str, ["vtuber", "subber"]) | None # use for filtering using conditional later for edge case
    org: Optional[str] = None
    sub_org: Optional[str] = None

class HolodexClient:
    """Client for Holodex API."""
    
    BASE_URL = "https://holodex.net/api/v2"
    RATE_LIMIT_SECONDS = 1
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            headers={"X-APIKEY": api_key} if api_key else {},
            timeout=30.0
        )
    
    def query_videos(
        self,
        channel_id: Optional[str] = None,
        topic: Optional[str] = None,
        org: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        status: str = "past",
        include: List[str] = None,
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
            include: Extra info to include (e.g., ["songs"])
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
        if include:
            params["include"] = ",".join(include)
        if from_date:
            params["from"] = from_date
        
        response = self.client.get("/videos", params=params)
        response.raise_for_status()
        data = response.json()
        
        videos = []
        for item in data:
            # Extract topic from songs if available
            topic_value = None
            if "songs" in item:
                for song in item.get("songs", []):
                    if song.get("name") in ["Music_Cover", "Original_Song"]:
                        topic_value = song["name"]
                        break
            
            # Fallback to topic field if songs not available
            if not topic_value and "topic" in item:
                topic_value = item["topic"]
            
            # Only include if it's a music-related topic
            if topic_value in ["Music_Cover", "Original_Song"]:
                video = HolodexVideo(
                    video_id=item["id"],
                    channel_id=item["channel_id"],
                    title=item["title"],
                    topic=topic_value,
                    available_at=item["available_at"],
                )
                videos.append(video)
        
        return videos
    
    def get_all_music_videos(
        self,
        db: Optional['Database'] = None  # type: ignore
    ) -> List[HolodexVideo]:
        """
        Get all music videos (covers and originals) since last check.
        Uses topic-based queries with timestamp filtering for efficiency.
        
        Args:
            db: Database instance to get latest timestamps per topic
        
        Returns:
            List of all music videos (deduplicated by video_id)
        """
        all_videos = []
        seen_video_ids = set()
        
        # Get latest timestamps and video_ids per topic from database
        latest_per_topic = {}
        if db:
            latest_per_topic = db.get_latest_available_at_per_topic()
        
        # Query each topic separately
        for topic in ["Music_Cover", "Original_Song"]:
            latest_info = latest_per_topic.get(topic) if latest_per_topic else None
            from_date = None
            stop_video_id = None
            
            if latest_info:
                stop_video_id, from_date = latest_info
            
            offset = 0
            while True:
                videos = self.query_videos(
                    topic=topic,
                    limit=50,
                    offset=offset,
                    include=["songs"],
                    from_date=from_date
                )
                if not videos:
                    break
                
                # Process videos and check if we've reached the stop point
                found_stop_video = False
                for video in videos:
                    # If we encounter the video_id we used for the latest timestamp, stop
                    if stop_video_id and video.video_id == stop_video_id:
                        found_stop_video = True
                        break
                    
                    # Deduplicate by video_id
                    if video.video_id not in seen_video_ids:
                        seen_video_ids.add(video.video_id)
                        all_videos.append(video)
                
                # Break out of outer loop if we hit the stop video_id
                if found_stop_video:
                    break
                
                # Continue pagination if we haven't reached the stop point
                if len(videos) < 50:
                    break
                offset += 50
        
        return all_videos
    
    def query_channel(self, channel_id: str) -> HolodexChannel:
        '''Queries channel endpoint using channel_id
        Returns HolodexChannel dataclass'''
        response = self.client.get(f"/channels/{channel_id}")
        response.raise_for_status()
        data = response.json()
        return HolodexChannel(
            channel_id=data["id"],
            name=data["name"],
            english_name=data["english_name"],
            type=data["type"],
            org=data["org"],
            sub_org=data["suborg"],
        )

    def close(self):
        """Close the HTTP client."""
        self.client.close()
