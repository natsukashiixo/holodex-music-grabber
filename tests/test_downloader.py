"""Regression tests for DownloadResult's error classification.

The properties used to have an operator-precedence bug (`self.error and "x" or
"y" in self.error`) that made members_only/deleted true for *any* non-empty
error, regardless of content - confirmed against the live DB to have
misclassified ~3400 rows. These cases pin the corrected behavior.
"""
from src.downloader import DownloadResult


def make(error):
    return DownloadResult(success=False, error=error)


def test_no_error_is_all_false():
    result = DownloadResult(success=True)
    assert not result.members_only
    assert not result.privated
    assert not result.deleted
    assert not result.georestricted


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
    ]:
        r = make(generic)
        assert not r.members_only, generic
        assert not r.deleted, generic


def test_members_only_variants():
    assert make("This video is available to this channel's members").members_only
    assert make("ERROR: [youtube] xyz: Join this channel to get access to members-only content like this video").members_only


def test_privated():
    assert make("ERROR: Private video. Sign in if you've been granted access to this video").privated
    assert not make("This video is available to this channel's members").privated


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
