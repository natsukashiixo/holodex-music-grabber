"""Path and filesystem helpers"""
from pathlib import Path
from typing import Dict, Optional
import unicodedata
import re

from src.logging_config import get_logger

logger = get_logger(__name__)

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

class PathTooLongError(Exception):
    pass


# Holodex's raw `suborg` field is genuinely prefixed with a 2-char sort code
# (e.g. "00Official", "1b2nd Generation", "vjVjidai Production") that should be
# stripped for display/storage. The transform itself (`s[2:]`) is correct; the
# bug that corrupted this project's data was calling it more than once on the
# same value from multiple uncoordinated code paths. This version is written to
# be a no-op on its own output: a successful strip always leaves the result
# starting with an uppercase letter or digit, which can never satisfy the
# lowercase-prefix branch below on a second pass. Call it as often as you like -
# it will never do more damage after the first correct application - but it
# should still only ever be invoked once per raw value, at the point that value
# is first parsed out of a Holodex API response.
_SUBORG_PREFIX_RE_ALPHA_LEAD = re.compile(r'^[0-9a-z]{2}(?=[A-Z0-9])')
_SUBORG_PREFIX_RE_DIGIT_LEAD = re.compile(r'^[0-9]{2}(?=[A-Z])')

_SUBORG_MANUAL_OVERRIDES: Dict[str, str] = {
    "avavex muchoo": "avex muchoo",
}

def sanitize_suborg(sub_org: str) -> str:
    """Strip Holodex's 2-char suborg sort-code prefix, if present. Idempotent."""
    if not sub_org:
        return sub_org
    if sub_org in _SUBORG_MANUAL_OVERRIDES:
        return _SUBORG_MANUAL_OVERRIDES[sub_org]
    if len(sub_org) <= 2:
        return sub_org
    prefix = sub_org[:2]
    if re.match(r'^[0-9]{2}$', prefix):
        if _SUBORG_PREFIX_RE_DIGIT_LEAD.match(sub_org):
            return sub_org[2:]
        return sub_org
    if re.match(r'^[0-9a-z]{2}$', prefix) and _SUBORG_PREFIX_RE_ALPHA_LEAD.match(sub_org):
        return sub_org[2:]
    return sub_org

def strip_emoji(s: str) -> str:
    return "".join(
        ch for ch in s
        if unicodedata.category(ch) not in {"So", "Cs", "Mn"}
    )

def fs_sanitize(name: str) -> str:
    if not name:
        return "unknown"

    name = strip_emoji(name) # looks kinda unclean but cba to move logic of strip_emoji() into this function

    name = unicodedata.normalize("NFC", name)
    name = _INVALID_CHARS_RE.sub("_", name)

    # strip trailing dots & spaces (Windows poison)
    name = name.rstrip(" .")

    # avoid reserved device names
    if name.lower() in _WINDOWS_RESERVED:
        name = f"_{name}"

    return name.lower() or "unknown"

def pathname_valid(path: Path):
    # 200 bytes could possibly be fine, but lets err on the side of caution
    return len(str(path).encode('utf-8')) <= 180

def filename_valid(path: Path) -> bool:
    return len(path.name.encode("utf-8")) <= 180

def pathlength_valid(path: Path) -> bool:
    return len(str(path)) <= 220

def make_safe_path(path: Path, fallback_stem: str) -> Path:
    # First try as-is
    if filename_valid(path) and pathlength_valid(path):
        return path

    safe = path.parent / f"{fallback_stem}{path.suffix}"

    if filename_valid(safe) and pathlength_valid(safe):
        return safe

    # Last resort: truncate stem to fit
    ext = path.suffix
    max_bytes = 180 - len(ext.encode("utf-8"))

    stem = fallback_stem.encode("utf-8")[:max_bytes]
    stem = stem.decode("utf-8", errors="ignore")

    final = path.parent / f"{stem}{ext}"

    if not pathlength_valid(final):
        raise PathTooLongError(final)

    return final




def build_relative_song_path(
    org: Optional[str],
    sub_org: Optional[str],
    channel_name: str,
    channel_id: str,
    topic: str,
    title: str,
) -> Path:
    """
    Build the canonical relative output path for a song: Org/Sub-org/Channel/Covers|Originals/title.mp3.
    Pure function, no filesystem side effects - shared by the live downloader and
    scripts/reorganize_music_files.py so the two can never diverge again.
    """
    parts = []

    if org:
        parts.append(fs_sanitize(org))
    if sub_org:
        parts.append(fs_sanitize(sanitize_suborg(sub_org)))

    # Channel folder always includes channel_id so same-name channels don't collide
    parts.append(fs_sanitize(f"{channel_name}_{channel_id}"))

    if topic == "Music_Cover":
        parts.append("Covers")
    elif topic == "Original_Song":
        parts.append("Originals")
    else:
        parts.append("Other")

    safe_title = fs_sanitize(title)
    return Path(*parts) / f"{safe_title}.mp3"
