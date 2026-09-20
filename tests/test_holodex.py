"""Regression test for HolodexClient.query_videos's response cache.

The cache key used to omit channel_id/org, so two different channels queried
with the same topic/limit/offset/from_date within the cache TTL would
silently return each other's results. Harmless for the original bulk
ingestion pattern (which never passes channel_id), but broke
scripts/backfill_duration.py's per-channel queries badly (~366/52724 rows
backfilled instead of the vast majority) once channel-scoped querying became
a real usage pattern.
"""
from src.holodex import HolodexClient


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _video_item(video_id, channel_id):
    return {
        "id": video_id,
        "channel_id": channel_id,
        "title": "Song",
        "topic_id": "Music_Cover",
        "available_at": "2026-01-01T00:00:00Z",
        "channel": {"id": channel_id, "name": "Chan", "org": "Org", "suborg": "zzSub"},
        "duration": 120,
    }


def test_query_videos_cache_is_scoped_by_channel_id():
    client = HolodexClient(api_key="test-key")
    client.rate_limiter.wait = lambda: None  # no need to actually sleep in tests

    responses = {
        "chanA": _FakeResponse([_video_item("vidA", "chanA")]),
        "chanB": _FakeResponse([_video_item("vidB", "chanB")]),
    }
    client.client.get = lambda path, params=None: responses[params["channel_id"]]

    videos_a = client.query_videos(channel_id="chanA", topic="Music_Cover", limit=50, offset=0)
    videos_b = client.query_videos(channel_id="chanB", topic="Music_Cover", limit=50, offset=0)

    assert [v.video_id for v in videos_a] == ["vidA"]
    assert [v.video_id for v in videos_b] == ["vidB"]

    client.close()
