"""Regression tests for DownloadResult's error classification.

The properties used to have an operator-precedence bug (`self.error and "x" or
"y" in self.error`) that made members_only/deleted true for *any* non-empty
error, regardless of content - confirmed against the live DB to have
misclassified ~3400 rows. These cases pin the corrected behavior.
"""
from src.downloader import DownloadResult, MusicDownloader


def make(error):
    return DownloadResult(success=False, error=error)


def test_no_error_is_all_false():
    result = DownloadResult(success=True)
    assert not result.members_only
    assert not result.privated
    assert not result.deleted
    assert not result.georestricted
    assert not result.age_restricted
    assert not result.uploader_unavailable


def test_unrelated_error_is_all_false():
    # This is the direct regression test for the precedence bug: a generic
    # error (bot-check, rate limit, timeout, HTTP 500) must not be classified
    # as members_only/deleted/etc just because *some* error is present.
    result = make("Sign in to confirm you're not a bot")
    assert not result.members_only
    assert not result.privated
    assert not result.deleted
    assert not result.georestricted

    for generic in [
        "HTTP Error 500: Internal Server Error",
        "urlopen error timed out",
        "content isn't available, try again later",
        # Deliberately not treated as permanent - yt-dlp's generic catch-all,
        # not safe to assume it always means permanent removal.
        "This video is unavailable",
    ]:
        r = make(generic)
        assert not r.members_only, generic
        assert not r.deleted, generic
        assert not r.age_restricted, generic
        assert not r.uploader_unavailable, generic


def test_members_only_variants():
    assert make("This video is available to this channel's members").members_only
    assert make("ERROR: [youtube] xyz: Join this channel to get access to members-only content like this video").members_only


def test_privated():
    # Both the old and the current (as of this yt-dlp version) phrasing must match.
    assert make("ERROR: Private video. Sign in if you've been granted access to this video").privated
    assert make(
        "ERROR: [youtube] zFYTczghuI4: Private video. If the owner of this video "
        "has granted you access, please sign in."
    ).privated
    assert not make("This video is available to this channel's members").privated


def test_age_restricted():
    assert make(
        "ERROR: [youtube] vOojH5qbzMM: Sign in to confirm your age. This video "
        "may be inappropriate for some users."
    ).age_restricted
    assert not make("Sign in to confirm you're not a bot").age_restricted


def test_uploader_unavailable():
    assert make("ERROR: [youtube] vGuGmkbAyf0: The uploader has not made this video available.").uploader_unavailable
    assert make(
        "ERROR: [youtube] ruV2AorIw9Q: The uploader has not made this video available in your country"
    ).uploader_unavailable
    assert not make("This video is unavailable").uploader_unavailable


def test_download_collision_disambiguates_instead_of_overwriting(tmp_path):
    # Regression test for the path-collision bug: output_path is built from
    # org/sub_org/channel/topic/title only, with no video_id - two different
    # videos sharing an identical title under the same channel+topic used to
    # collide, and the (now-removed) "if output_path.exists(): return success"
    # shortcut would silently claim a DIFFERENT video's file as this one's
    # own, which downstream strict-dedup logic could then delete. Confirmed
    # live in the DB: 85/85 videos affected by this had their canonical
    # sibling's file destroyed this way.
    base_dir = tmp_path / "Music"
    cache_dir = tmp_path / "cache"
    downloader = MusicDownloader(base_output_dir=base_dir, cache_dir=cache_dir, enforce_sleep=False)

    common_kwargs = dict(
        org="Org", sub_org="SubOrg", channel_name="Channel",
        channel_id="chanA", topic="Original_Song", title="Same Title",
    )

    # Video A already occupies the "natural" path for this
    # org/sub_org/channel/topic/title combination.
    existing_path = downloader._get_output_path(**common_kwargs)
    existing_path.write_bytes(b"video-a-content")

    # Video B shares the exact same title/channel/topic (a real collision).
    # Simulate its download already sitting in the cache so no network call
    # is needed for this test.
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "videoB.mp3").write_bytes(b"video-b-content")

    result = downloader.download(video_id="videoB", **common_kwargs)

    assert result.success
    assert result.file_path != existing_path
    assert result.file_path.read_bytes() == b"video-b-content"
    # Video A's file must be untouched, not overwritten or claimed by B.
    assert existing_path.read_bytes() == b"video-a-content"


def test_deleted_variants():
    assert make("Video unavailable. This video has been removed by the uploader").deleted
    assert make("Video unavailable. This video is not available").deleted
    assert not make("Sign in to confirm you're not a bot").deleted


def test_georestricted():
    error = (
        "Video unavailable. This video contains content from Sony Music "
        "Entertainment (Japan) Inc., who has blocked it in your country on "
        "copyright grounds"
    )
    result = make(error)
    assert result.georestricted
    # A georestricted error is not itself a members-only/deleted/private error.
    assert not result.members_only
    assert not result.deleted
    assert not result.privated
