"""Tests for HIDDEN-channel handling in process_video.

Holodex reports some channels as channel_id "HIDDEN". Those videos are only
downloaded when their "Provided to YouTube by" label is allowlisted, into a
per-label folder; everything else is recorded as rejected without downloading,
and retry runs skip it.
"""
import pytest
import yt_dlp

from src.db import Database
from src.downloader import MusicDownloader
from src.holodex import HolodexVideo
from src.utils import HIDDEN_REJECTED_ERROR, process_video


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def downloader(tmp_path):
    return MusicDownloader(base_output_dir=tmp_path / "Music", cache_dir=tmp_path / "cache", enforce_sleep=False)


@pytest.fixture
def client():
    class NoCallsClient:
        def query_channel(self, channel_id):
            raise AssertionError("HIDDEN must not be queried as a channel")
    return NoCallsClient()


def make_hidden_video(video_id):
    return HolodexVideo(
        video_id=video_id,
        channel_id="HIDDEN",
        title="Solara",
        topic="Original_Song",
        available_at="2026-01-01T00:00:00.000Z",
        channel_name="?",
        org=None,
        sub_org="FALLBACK",
        duration=200,
    )


def test_allowlisted_label_downloads_into_label_folder(db, downloader, client, monkeypatch):
    monkeypatch.setattr(downloader, "fetch_provided_by", lambda vid: "ANYCOLOR, Inc.")
    downloader.cache_dir.mkdir(parents=True, exist_ok=True)
    (downloader.cache_dir / "h1.mp3").write_bytes(b"track")

    assert process_video(make_hidden_video("h1"), db, downloader, client=client)

    assert db.get_song("h1").file_path == "fallback/__hidden/anycolor, inc/Originals/solara.mp3"


def test_other_label_is_rejected_without_download(db, downloader, client, monkeypatch):
    monkeypatch.setattr(downloader, "fetch_provided_by", lambda vid: "DistroKid")
    monkeypatch.setattr(downloader, "download", lambda **kw: pytest.fail("must not download"))

    assert process_video(make_hidden_video("h2"), db, downloader, client=client)

    song = db.get_song("h2")
    assert song.file_path is None
    assert song.error == f"{HIDDEN_REJECTED_ERROR} (provided by DistroKid)"
    assert "h2" not in {s.video_id for s in db.get_songs_without_file_hash()}


def test_rejected_video_is_not_looked_up_again(db, downloader, client, monkeypatch):
    monkeypatch.setattr(downloader, "fetch_provided_by", lambda vid: None)
    process_video(make_hidden_video("h3"), db, downloader, client=client)
    monkeypatch.setattr(downloader, "fetch_provided_by", lambda vid: pytest.fail("looked up again"))

    assert process_video(make_hidden_video("h3"), db, downloader, client=client)


def test_lookup_failure_is_recorded_like_a_download_failure(db, downloader, client, monkeypatch):
    def unavailable(vid):
        raise yt_dlp.utils.DownloadError("ERROR: [youtube] h4: Video unavailable. This video has been removed by the uploader")
    monkeypatch.setattr(downloader, "fetch_provided_by", unavailable)

    assert not process_video(make_hidden_video("h4"), db, downloader, client=client)

    song = db.get_song("h4")
    assert song.deleted
    assert "Video unavailable" in song.error


def test_custom_allowlist_is_respected(db, downloader, client, monkeypatch):
    monkeypatch.setattr(downloader, "fetch_provided_by", lambda vid: "ANYCOLOR, Inc.")

    assert process_video(make_hidden_video("h5"), db, downloader, client=client,
                         hidden_provider_allowlist={"DistroKid"})

    assert db.get_song("h5").error.startswith(HIDDEN_REJECTED_ERROR)
