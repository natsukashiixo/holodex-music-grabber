"""Tests for src.db.Database against a throwaway sqlite file."""
import pytest

from src.db import Channel, Database, Song


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def make_song(video_id, **overrides):
    defaults = dict(
        video_id=video_id,
        channel_id="chan1",
        title="Some Song",
        topic="Music_Cover",
        available_at="2026-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return Song(**defaults)


def test_hash_exists_ignores_deleted_rows(db):
    db.upsert_channel(Channel(channel_id="chan1", name="Channel"))
    db.add_song(make_song("v1", file_hash="abc123"))

    assert db.hash_exists("abc123") == "v1"

    db.mark_deleted("v1")
    assert db.hash_exists("abc123") is None


def test_hash_exists_returns_none_for_unknown_hash(db):
    assert db.hash_exists("does-not-exist") is None


def test_get_songs_without_file_hash_excludes_unavailable_categories(db):
    db.upsert_channel(Channel(channel_id="chan1", name="Channel"))
    db.add_song(make_song("members", members_only=True, error="This video is available to this channel's members"))
    db.add_song(make_song("privated", privated=True, error="Private video. Sign in if you've been granted access"))
    db.add_song(make_song("deleted", deleted=True, error="Video unavailable. This video has been removed by the uploader"))
    db.add_song(make_song(
        "georestricted",
        error="Video unavailable. ... who has blocked it in your country on copyright grounds",
    ))
    db.add_song(make_song("duration_gate", error="duration_out_of_bounds (3)"))
    db.add_song(make_song("generic_retryable", error="Sign in to confirm you're not a bot"))

    retryable = {s.video_id for s in db.get_songs_without_file_hash(exclude_unavailable=True)}

    assert retryable == {"generic_retryable"}


def test_get_songs_without_file_hash_no_exclusion(db):
    db.upsert_channel(Channel(channel_id="chan1", name="Channel"))
    db.add_song(make_song("members", members_only=True, error="members only"))
    db.add_song(make_song("generic", error="some transient error"))

    all_failed = {s.video_id for s in db.get_songs_without_file_hash(exclude_unavailable=False)}
    assert all_failed == {"members", "generic"}


def test_get_songs_without_file_hash_order_is_deterministic_on_ties(db):
    # Regression/determinism check for main.py's --limit flag: picking "the
    # first N" from the retry queue must return the same N videos every run
    # (as long as none of them succeed in between), even when multiple rows
    # share the exact same available_at timestamp.
    db.upsert_channel(Channel(channel_id="chan1", name="Channel"))
    same_timestamp = "2026-01-01T00:00:00Z"
    for video_id in ["c", "a", "b"]:
        db.add_song(make_song(video_id, available_at=same_timestamp))

    order_1 = [s.video_id for s in db.get_songs_without_file_hash(exclude_unavailable=False)]
    order_2 = [s.video_id for s in db.get_songs_without_file_hash(exclude_unavailable=False)]

    assert order_1 == order_2 == ["a", "b", "c"]


def test_upsert_channel_does_not_null_out_existing_org_on_partial_update(db):
    db.upsert_channel(Channel(channel_id="chan1", name="Channel", org="OrgName", sub_org="SubOrg"))

    # Simulate the bulk ingestion path re-upserting the same channel with
    # org/sub_org unknown (None) - this must NOT wipe the previously-known values.
    db.upsert_channel(Channel(channel_id="chan1", name="Channel (renamed)", org=None, sub_org=None))

    channel = db.get_channel("chan1")
    assert channel.name == "Channel (renamed)"
    assert channel.org == "OrgName"
    assert channel.sub_org == "SubOrg"


def test_upsert_channel_updates_org_when_provided(db):
    db.upsert_channel(Channel(channel_id="chan1", name="Channel", org=None, sub_org=None))
    db.upsert_channel(Channel(channel_id="chan1", name="Channel", org="OrgName", sub_org="SubOrg"))

    channel = db.get_channel("chan1")
    assert channel.org == "OrgName"
    assert channel.sub_org == "SubOrg"


def test_english_name_column_is_dropped_from_legacy_db(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE channels (
            channel_id TEXT PRIMARY KEY,
            name TEXT,
            english_name TEXT,
            org TEXT,
            sub_org TEXT
        )
    """)
    conn.commit()
    conn.close()

    Database(db_path)  # should self-heal by dropping the column

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(channels)").fetchall()}
    conn.close()
    assert "english_name" not in columns
