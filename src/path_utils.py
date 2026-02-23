"""Path and filesystem helpers"""
import shutil
from pathlib import Path
from typing import Dict, List
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


def sanitize_suborg(sub_org: str) -> str:
    """Normalize sub_org for storage and paths (e.g. strip 2-char prefix like 'zz')."""
    return sub_org[2:] if len(sub_org) > 2 else sub_org

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




def merge_duplicate_suborg_folders(base_dir: Path) -> None:
    """Merge sub_org folders that collapse to the same name after sanitize_suborg.
    E.g. Org/EN_Vtuber/ and Org/Vtuber/ both become Org/Vtuber/ (content merged).
    """
    for org_dir in base_dir.iterdir():
        if not org_dir.is_dir():
            continue
        subdirs = [d for d in org_dir.iterdir() if d.is_dir()]
        by_canonical: Dict[str, List[Path]] = {}
        for d in subdirs:
            canonical = fs_sanitize(sanitize_suborg(d.name))
            by_canonical.setdefault(canonical, []).append(d)
        for canonical, dirs in by_canonical.items():
            to_merge = [d for d in dirs if d.name != canonical]
            if not to_merge:
                continue
            target = next((d for d in dirs if d.name == canonical), None) or (org_dir / canonical)
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
            for src in to_merge:
                if src.resolve() == target.resolve():
                    continue
                logger.info(f"Merging {src.relative_to(base_dir)} into {target.relative_to(base_dir)}")
                shutil.copytree(src, target, dirs_exist_ok=True)
                shutil.rmtree(src)
