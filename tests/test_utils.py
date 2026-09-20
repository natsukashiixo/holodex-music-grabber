"""Tests for process_video's pre-download duration gate.

The gate must reject a video before it's ever handed to yt-dlp when its
duration is clearly out of bounds (<5s glitch, >3600s likely a mistagged
livestream) - this is the bandwidth-protection guard for the migration.
Boundary values (5, 3600) and missing duration (None) must NOT be rejected.
"""
from unittest.mock import MagicMock

import pytest

from src.db import Database
from src.downloader import DownloadResult
from src.holodex import HolodexVideo
from src.utils import process_video


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def downloader(tmp_path):
    fake = MagicMock()
    fake.base_output_dir = tmp_path
    fake.download.return_value = DownloadResult(success=False, error="unrelated test failure")
    return fake


def make_video(video_id, duration):
    return HolodexVideo(
        video_id=video_id,
        channel_id="chan1",
        title="Some Song",
        topic="Music_Cover",
        available_at="2026-01-01T00:00:00Z",
        channel_name="Channel",
        org="Org",
        sub_org="SubOrg",
        duration=duration,
    )


@pytest.mark.parametrize("duration", [0, 1, 4, 3601, 3700, 7200])
def test_out_of_bounds_duration_skips_download(db, downloader, duration):
    video = make_video("v1", duration)

    result = process_video(video, db, downloader, skip_existing=True, client=None)

    assert result is False
    downloader.download.assert_not_called()

    song = db.get_song("v1")
    assert song.file_hash is None
    assert song.error.startswith("duration_out_of_bounds")


@pytest.mark.parametrize("duration", [None, 5, 3600, 30, 1200])
def test_in_bounds_duration_proceeds_to_download(db, downloader, duration):
    video = make_video("v1", duration)

    process_video(video, db, downloader, skip_existing=True, client=None)

    downloader.download.assert_called_once()

    song = db.get_song("v1")
    assert song.error == "unrelated test failure"
