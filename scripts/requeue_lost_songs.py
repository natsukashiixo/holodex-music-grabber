"""One-off: requeue songs that were successfully downloaded at some point in
this project's history (file_hash was set, error was NULL) but whose file no
longer exists anywhere in the library - confirmed via
reconcile_file_paths_by_hash.py's exhaustive content-hash search, then marked
deleted=1 by verify_existing_files() (653 rows).

Resets these rows to a fresh "never downloaded" state so the normal
`main.py --retry-failed` flow will re-attempt them via yt-dlp and correctly
(re-)classify each one based on what YouTube says today - re-downloaded if
still available, or properly marked members-only/geo-blocked/deleted-by-
uploader via the (now-fixed) DownloadResult classification if not.

Explicitly excludes rows deleted for other, non-lost reasons:
- error LIKE 'duplicate content of%' (canonical copy already exists elsewhere)
- error LIKE 'duration_out_of_bounds%' (intentionally purged mistagged content)

Does NOT invoke yt-dlp or touch the filesystem - only resets DB state. Run
`main.py --retry-failed` afterward to actually attempt re-downloads.

Usage:
    uv run python scripts/requeue_lost_songs.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings


def main():
    settings = resolve_settings()
    db_path = settings["db_path"]

    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT video_id FROM songs
        WHERE deleted = 1 AND file_hash IS NOT NULL
          AND (error IS NULL OR (
              error NOT LIKE 'duplicate content of%'
              AND error NOT LIKE 'duration_out_of_bounds%'
          ))
    """)
    rows = cur.fetchall()
    print(f"{len(rows)} songs will be requeued for retry")

    cur.executemany("""
        UPDATE songs
        SET file_hash = NULL, file_path = NULL, deleted = 0,
            members_only = 0, privated = 0, error = NULL
        WHERE video_id = ?
    """, [(row["video_id"],) for row in rows])
    conn.commit()
    conn.close()

    print(f"Done. {len(rows)} rows reset to a retryable state.")
    print("Run: uv run python main.py --retry-failed  (needs a Holodex API key configured)")


if __name__ == "__main__":
    main()
