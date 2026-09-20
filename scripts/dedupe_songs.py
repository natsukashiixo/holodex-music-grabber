"""One-off: clean up already-known duplicate files (same file_hash across
multiple non-deleted rows) that predate the strict-dedup fix in
process_video(). Keeps the earliest-available_at row per hash group as
canonical, deletes the other files from disk, and marks their rows deleted.

Run after reorganize_music_files.py so canonical-path tie-breaks reflect
corrected folder names. Idempotent - re-running finds no groups left once
complete.

Usage:
    uv run python scripts/dedupe_songs.py
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings


def main():
    settings = resolve_settings()
    db_path = settings["db_path"]
    base_output_dir = settings["output_dir"]

    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT file_hash
        FROM songs
        WHERE file_hash IS NOT NULL AND deleted = 0
        GROUP BY file_hash
        HAVING COUNT(*) > 1
    """)
    dupe_hashes = [row["file_hash"] for row in cur.fetchall()]
    print(f"{len(dupe_hashes)} file_hash groups have duplicates")

    removed_files = 0
    missing_files = 0
    today = date.today().isoformat()

    for file_hash in dupe_hashes:
        cur.execute("""
            SELECT video_id, file_path, available_at
            FROM songs
            WHERE file_hash = ? AND deleted = 0
            ORDER BY available_at ASC
        """, (file_hash,))
        group = cur.fetchall()
        canonical = group[0]
        duplicates = group[1:]

        for dupe in duplicates:
            if dupe["file_path"]:
                absolute = base_output_dir / dupe["file_path"]
                if absolute.exists():
                    absolute.unlink()
                    removed_files += 1
                else:
                    missing_files += 1

            cur.execute(
                "UPDATE songs SET deleted = 1, file_path = NULL, error = ? WHERE video_id = ?",
                (
                    f"duplicate content of {canonical['video_id']}; file removed by dedupe_songs.py on {today}",
                    dupe["video_id"],
                ),
            )
        conn.commit()

    conn.close()
    print(f"Done. {removed_files} duplicate files removed, {missing_files} were already missing.")


if __name__ == "__main__":
    main()
