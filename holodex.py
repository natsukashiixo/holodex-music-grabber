"""Holodex API client for querying videos."""
import httpx
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class HolodexVideo:
    """Video information from Holodex API."""
    video_id: str
    channel_id: str
    channel_name: str
    title: str
    topic: str  # "Music_Cover" or "Original_Song"
    available_at: str
    org: Optional[str] = None
    sub_org: Optional[str] = None


class HolodexClient:
    """Client for Holodex API."""
    
    BASE_URL = "https://holodex.net/api/v2"
    
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
        include: List[str] = None
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
                    channel_id=item["channel"]["id"],
                    channel_name=item["channel"]["name"],
                    title=item["title"],
                    topic=topic_value,
                    available_at=item["available_at"],
                    org=item.get("channel", {}).get("org"),
                    sub_org=item.get("channel", {}).get("suborg")
                )
                videos.append(video)
        
        return videos
    
    def get_all_music_videos(
        self,
        channel_ids: Optional[List[str]] = None,
        org: Optional[str] = None
    ) -> List[HolodexVideo]:
        """
        Get all music videos (covers and originals) from specified channels or org.
        
        Args:
            channel_ids: List of channel IDs to query (None = all channels)
            org: Organization filter
        
        Returns:
            List of all music videos (deduplicated by video_id)
        """
        all_videos = []
        seen_video_ids = set()
        
        if channel_ids:
            # Query each channel individually
            for channel_id in channel_ids:
                for topic in ["Music_Cover", "Original_Song"]:
                    offset = 0
                    while True:
                        videos = self.query_videos(
                            channel_id=channel_id,
                            topic=topic,
                            limit=50,
                            offset=offset,
                            include=["songs"]
                        )
                        if not videos:
                            break
                        # Deduplicate by video_id
                        for video in videos:
                            if video.video_id not in seen_video_ids:
                                seen_video_ids.add(video.video_id)
                                all_videos.append(video)
                        if len(videos) < 50:
                            break
                        offset += 50
        else:
            # Query by org or all
            for topic in ["Music_Cover", "Original_Song"]:
                offset = 0
                while True:
                    videos = self.query_videos(
                        topic=topic,
                        org=org,
                        limit=50,
                        offset=offset,
                        include=["songs"]
                    )
                    if not videos:
                        break
                    # Deduplicate by video_id
                    for video in videos:
                        if video.video_id not in seen_video_ids:
                            seen_video_ids.add(video.video_id)
                            all_videos.append(video)
                    if len(videos) < 50:
                        break
                    offset += 50
        
        return all_videos
    
    def close(self):
        """Close the HTTP client."""
        self.client.close()
