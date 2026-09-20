"""Tests for sanitize_suborg (regression coverage for the non-idempotent,
uncoordinated-strip corruption confirmed live in the DB) and fs_sanitize."""
import pytest

from src.path_utils import fs_sanitize, sanitize_suborg


@pytest.mark.parametrize("raw, expected", [
    # Genuinely raw values with a real 2-char sort-code prefix - should strip.
    ("00Official", "Official"),
    ("08Ex-SEEDs", "Ex-SEEDs"),
    ("1c1st Generation", "1st Generation"),
    ("1b2nd Generation", "2nd Generation"),
    ("vjVjidai Production", "Vjidai Production"),
    ("10Mixstgirls", "Mixstgirls"),
    ("zzFALLBACK", "FALLBACK"),
    # Values that were corrupted historically by the blind/repeated strip -
    # must NOT be touched further now that the rule is idempotent and
    # content-aware.
    ("1st Generation", "1st Generation"),
    ("2nd Generation", "2nd Generation"),
    ("Generation", "Generation"),
    ("Official", "Official"),
    # Digit-run false positive: "20" looks like a 2-digit prefix but the
    # following char is also a digit, so this must be left alone.
    ("2024 Debut Group", "2024 Debut Group"),
    # Short strings are never touched.
    ("zz", "zz"),
    ("a", "a"),
    ("", ""),
    (None, None),
    # Manual override for a known false negative.
    ("avavex muchoo", "avex muchoo"),
])
def test_sanitize_suborg(raw, expected):
    assert sanitize_suborg(raw) == expected


@pytest.mark.parametrize("raw", [
    "00Official", "1c1st Generation", "1st Generation", "Generation",
    "2024 Debut Group", "zzFALLBACK", "vjVjidai Production",
])
def test_sanitize_suborg_is_idempotent(raw):
    once = sanitize_suborg(raw)
    twice = sanitize_suborg(once)
    assert once == twice


def test_fs_sanitize_strips_invalid_chars():
    assert fs_sanitize('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_fs_sanitize_reserved_windows_name():
    assert fs_sanitize("con") == "_con"
    assert fs_sanitize("COM1") == "_com1"


def test_fs_sanitize_strips_trailing_dots_and_spaces():
    assert fs_sanitize("Title. ") == "title"


def test_fs_sanitize_empty_is_unknown():
    assert fs_sanitize("") == "unknown"
