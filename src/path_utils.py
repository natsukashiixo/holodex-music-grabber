"""Path and filesystem helpers (no dependency on holodex or utils to avoid circular imports)."""
import shutil
from pathlib import Path
from typing import Dict, List

from src.logging_config import get_logger

logger = get_logger(__name__)


def sanitize_suborg(sub_org: str) -> str:
    """Normalize sub_org for storage and paths (e.g. strip 2-char prefix like 'EN_')."""
    return sub_org[2:] if len(sub_org) > 2 else sub_org


def fs_sanitize(name: str) -> str:
    """Sanitize for filesystem (invalid chars, strip)."""
    if not name:
        return "Unknown"
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name.strip()


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
