"""One-off: move already-downloaded files to match corrected org/sub_org
folder names, and update songs.file_path to match. Driven entirely by DB rows
(songs joined to channels) - never scans the music directory directly.

Run only after repair_channel_suborgs.py has fully completed, since this
script trusts channels.sub_org as already-correct ground truth. Idempotent /
resumable: rows already at their canonical path are a no-op on re-run.

Some historical rows' file_path doesn't resolve under base_output_dir at all -
confirmed live to be because a past run had `download_folder` accidentally
pointed at `<base>/Yuni Create/` for a large batch (126GB / ~26k files spanning
many unrelated orgs, not actually Yuni Create's own content), nesting those
files one directory level deeper than their recorded file_path expects.
FALLBACK_SOURCE_PREFIXES lists extra locations to check for the real source
file before giving up - if found there, it's migrated through the exact same
corrected-destination logic as everything else, dropping the spurious prefix.

Usage:
    uv run python scripts/reorganize_music_files.py
"""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings
from src.db import hash_file
from src.path_utils import build_relative_song_path, make_safe_path

FALLBACK_SOURCE_PREFIXES = ["Yuni Create"]


def cleanup_empty_dirs(start: Path, stop_at: Path):
    """Remove `start` and any now-empty ancestors, stopping before `stop_at`."""
    current = start
    while current != stop_at and stop_at in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def main():
    settings = resolve_settings()
    db_path = settings["db_path"]
    base_output_dir = settings["output_dir"]

    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT s.video_id, s.channel_id, s.topic, s.title, s.file_path,
               c.name AS channel_name, c.org, c.sub_org
        FROM songs s
        JOIN channels c ON c.channel_id = s.channel_id
        WHERE s.file_path IS NOT NULL
    """)
    rows = cur.fetchall()
    print(f"{len(rows)} songs with a file_path to check")

    moved = 0
    skipped_hash_dupe = 0
    collisions_renamed = 0
    unchanged = 0
    missing_source = 0
    recovered_from_fallback = 0

    for row in rows:
        old_relative = Path(row["file_path"])
        old_absolute = base_output_dir / old_relative

        new_relative = build_relative_song_path(
            org=row["org"],
            sub_org=row["sub_org"],
            channel_name=row["channel_name"] or "Unknown",
            channel_id=row["channel_id"],
            topic=row["topic"],
            title=row["title"],
        )
        new_relative = make_safe_path(new_relative, row["video_id"])
        new_absolute = base_output_dir / new_relative

        if new_absolute == old_absolute and old_absolute.exists():
            unchanged += 1
            continue

        if not old_absolute.exists():
            for prefix in FALLBACK_SOURCE_PREFIXES:
                candidate = base_output_dir / prefix / old_relative
                if candidate.exists():
                    old_absolute = candidate
                    recovered_from_fallback += 1
                    break

        if not old_absolute.exists():
            print(f"  [MISSING SOURCE] {row['video_id']}: {old_absolute} does not exist, skipping")
            missing_source += 1
            continue

        if new_absolute == old_absolute:
            unchanged += 1
            continue

        if new_absolute.exists():
            if hash_file(old_absolute) == hash_file(new_absolute):
                print(f"  [HASH DUPE] {row['video_id']}: {old_absolute} duplicates {new_absolute}, leaving for dedupe_songs.py")
                skipped_hash_dupe += 1
                continue
            new_absolute = new_absolute.parent / f"{new_absolute.stem}_{row['video_id']}{new_absolute.suffix}"
            print(f"  [COLLISION] {row['video_id']}: different content at target path, renamed to {new_absolute.name}")
            collisions_renamed += 1

        new_absolute.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_absolute), str(new_absolute))
        cur.execute(
            "UPDATE songs SET file_path = ? WHERE video_id = ?",
            (str(new_absolute.relative_to(base_output_dir)), row["video_id"]),
        )
        conn.commit()
        cleanup_empty_dirs(old_absolute.parent, base_output_dir)
        moved += 1

        if moved % 500 == 0:
            print(f"  ... {moved} files moved so far")

    conn.close()

    print(
        f"Done. moved={moved} unchanged={unchanged} hash_dupe_skipped={skipped_hash_dupe} "
        f"collisions_renamed={collisions_renamed} missing_source={missing_source} "
        f"recovered_from_fallback={recovered_from_fallback}"
    )


if __name__ == "__main__":
    main()
