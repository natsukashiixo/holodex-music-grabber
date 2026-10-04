"""Tests for re-upload labelling on path collisions.

Channels re-upload songs under the exact same title; both videos map to the
same output path. Within a likely re-upload pair (same path, durations within
1s) the older upload keeps the clean name and the newer one gets
_possible_reupYYMMDD - regardless of which one happens to be downloaded first.
"""
from pathlib import Path

import pytest

from src.db import Channel, Database, Song, hash_file
from src.downloader import DownloadResult, MusicDownloader
from src.holodex import HolodexVideo
from src.utils import process_video

OLD_DATE = "2024-11-12T10:00:00.000Z"
NEW_DATE = "2026-10-02T10:00:00.000Z"


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def downloader(tmp_path):
    return MusicDownloader(base_output_dir=tmp_path / "Music", cache_dir=tmp_path / "cache", enforce_sleep=False)


def make_video(video_id, available_at, duration):
    return HolodexVideo(
        video_id=video_id,
        channel_id="chan1",
        title="ClimBinge (Remix)",
        topic="Original_Song",
        available_at=available_at,
        channel_name="Channel",
        org="Org",
        sub_org="SubOrg",
        duration=duration,
    )


def clean_path_for(downloader, video) -> Path:
    return downloader._get_output_path(
        video.org, video.sub_org, video.channel_name, video.channel_id, video.topic, video.title
    )


def seed_existing(db, downloader, video, content: bytes) -> Path:
    """Put `video` in the DB as already downloaded at the clean path."""
    path = clean_path_for(downloader, video)
    path.write_bytes(content)
    db.upsert_channel(Channel(channel_id=video.channel_id, name=video.channel_name, org=video.org, sub_org=video.sub_org))
    db.add_song(Song(
        video_id=video.video_id, channel_id=video.channel_id, title=video.title, topic=video.topic,
        available_at=video.available_at, file_hash=hash_file(path),
        file_path=str(path.relative_to(downloader.base_output_dir)), duration=video.duration,
    ))
    return path


def seed_cache(downloader, video_id, content: bytes):
    downloader.cache_dir.mkdir(parents=True, exist_ok=True)
    (downloader.cache_dir / f"{video_id}.mp3").write_bytes(content)


def test_newer_reupload_gets_possible_reup_suffix(db, downloader):
    old = make_video("old", OLD_DATE, 166)
    new = make_video("new", NEW_DATE, 167)
    clean = seed_existing(db, downloader, old, b"old-encode")
    seed_cache(downloader, "new", b"new-encode")

    assert process_video(new, db, downloader, client=None)

    assert clean.read_bytes() == b"old-encode"
    reup = clean.with_name(f"{clean.stem}_possible_reup261002.mp3")
    assert reup.read_bytes() == b"new-encode"
    assert db.get_song("new").file_path == str(reup.relative_to(downloader.base_output_dir))
    assert db.get_song("old").file_path == str(clean.relative_to(downloader.base_output_dir))


def test_older_original_arriving_second_takes_clean_name(db, downloader):
    old = make_video("old", OLD_DATE, 166)
    new = make_video("new", NEW_DATE, 166)
    clean = seed_existing(db, downloader, new, b"new-encode")
    seed_cache(downloader, "old", b"old-encode")

    assert process_video(old, db, downloader, client=None)

    assert clean.read_bytes() == b"old-encode"
    reup = clean.with_name(f"{clean.stem}_possible_reup261002.mp3")
    assert reup.read_bytes() == b"new-encode"
    assert db.get_song("old").file_path == str(clean.relative_to(downloader.base_output_dir))
    assert db.get_song("new").file_path == str(reup.relative_to(downloader.base_output_dir))
    assert not list(clean.parent.glob("*_old.mp3"))


def test_different_durations_fall_back_to_video_id_suffix(db, downloader):
    first = make_video("first", OLD_DATE, 166)
    other = make_video("other", NEW_DATE, 240)
    clean = seed_existing(db, downloader, first, b"first-encode")
    seed_cache(downloader, "other", b"other-encode")

    assert process_video(other, db, downloader, client=None)

    assert clean.read_bytes() == b"first-encode"
    assert clean.with_name(f"{clean.stem}_other.mp3").read_bytes() == b"other-encode"


def test_failed_download_leaves_existing_reupload_untouched(db, downloader, monkeypatch):
    old = make_video("old", OLD_DATE, 166)
    new = make_video("new", NEW_DATE, 166)
    clean = seed_existing(db, downloader, new, b"new-encode")

    def failing_download(**kwargs):
        kwargs["collision_resolver"](clean)  # the collision is resolved before the fetch
        return DownloadResult(success=False, error="HTTP Error 403")

    monkeypatch.setattr(downloader, "download", failing_download)

    assert not process_video(old, db, downloader, client=None)

    assert clean.read_bytes() == b"new-encode"
    assert db.get_song("new").file_path == str(clean.relative_to(downloader.base_output_dir))
    assert db.get_song("old").file_path is None
