"""One-off: retroactively clean up already-downloaded songs whose duration
falls outside the same hard bounds process_video() now enforces before every
new download (src.utils.DURATION_HARD_MIN_SECONDS/DURATION_HARD_MAX_SECONDS).
These predate the duration gate and are almost entirely multi-hour livestream
VODs mistagged as Music_Cover/Original_Song (confirmed live: 556 files >3600s,
~3.78M seconds / ~1051 hours combined) plus a couple of near-zero-length
announcement clips.

Deletes the file from disk and marks the row deleted=1/file_path=NULL,
keeping file_hash (consistent with dedupe_songs.py's convention) so
hash_exists() bookkeeping stays correct. Idempotent - re-running finds
nothing left once complete.

Usage:
    uv run python scripts/cleanup_out_of_bounds_songs.py
"""
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings
from src.utils import DURATION_HARD_MAX_SECONDS, DURATION_HARD_MIN_SECONDS


def main():
    settings = resolve_settings()
    db_path = settings["db_path"]
    base_output_dir = settings["output_dir"]

    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT video_id, title, duration, file_path
        FROM songs
        WHERE deleted = 0
          AND file_path IS NOT NULL AND file_path != ''
          AND duration IS NOT NULL
          AND (duration < ? OR duration > ?)
    """, (DURATION_HARD_MIN_SECONDS, DURATION_HARD_MAX_SECONDS))
    rows = cur.fetchall()
    print(f"{len(rows)} downloaded songs are outside the {DURATION_HARD_MIN_SECONDS}-{DURATION_HARD_MAX_SECONDS}s bounds")

    removed_files = 0
    missing_files = 0
    total_seconds_reclaimed = 0
    today = date.today().isoformat()

    for row in rows:
        absolute = base_output_dir / row["file_path"]
        if absolute.exists():
            absolute.unlink()
            removed_files += 1
            total_seconds_reclaimed += row["duration"]
        else:
            missing_files += 1

        cur.execute("""
            UPDATE songs
            SET deleted = 1, file_path = NULL, error = ?
            WHERE video_id = ?
        """, (
            f"duration_out_of_bounds ({row['duration']}s); file removed by cleanup_out_of_bounds_songs.py on {today}",
            row["video_id"],
        ))
        conn.commit()

    conn.close()
    print(
        f"Done. {removed_files} files removed ({total_seconds_reclaimed}s / "
        f"{total_seconds_reclaimed / 3600:.1f}h of mistagged content), {missing_files} were already missing."
    )


if __name__ == "__main__":
    main()
