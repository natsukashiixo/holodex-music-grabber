"""One-off: recompute members_only/privated/deleted for existing error rows
using the corrected DownloadResult classification logic. Pure local SQL, no
network calls, safe to re-run (idempotent).

Usage:
    uv run python scripts/backfill_error_flags.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings
from src.downloader import DownloadResult

# Our own synthetic error strings (not real yt-dlp error text) must never be
# reclassified by this script - they don't correspond to any DownloadResult
# category and re-running the classifier over them would incorrectly reset
# deleted/members_only back to 0.
SYNTHETIC_ERROR_PREFIXES = ("duplicate content of", "duration_out_of_bounds")


def main():
    settings = resolve_settings()
    db_path = settings["db_path"]
    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT video_id, error, members_only, privated, deleted FROM songs WHERE error IS NOT NULL")
    rows = cur.fetchall()

    before = {"members_only": 0, "privated": 0, "deleted": 0}
    after = {"members_only": 0, "privated": 0, "deleted": 0}
    updates = []
    skipped_synthetic = 0

    for row in rows:
        error = row["error"]
        if any(error.startswith(prefix) for prefix in SYNTHETIC_ERROR_PREFIXES):
            skipped_synthetic += 1
            continue

        result = DownloadResult(success=False, error=error)
        new_members_only = int(result.members_only)
        new_privated = int(result.privated)
        new_deleted = int(result.deleted)

        before["members_only"] += row["members_only"]
        before["privated"] += row["privated"]
        before["deleted"] += row["deleted"]
        after["members_only"] += new_members_only
        after["privated"] += new_privated
        after["deleted"] += new_deleted

        if (new_members_only, new_privated, new_deleted) != (row["members_only"], row["privated"], row["deleted"]):
            updates.append((new_members_only, new_privated, new_deleted, row["video_id"]))

    if updates:
        cur.executemany(
            "UPDATE songs SET members_only = ?, privated = ?, deleted = ? WHERE video_id = ?",
            updates,
        )
        conn.commit()

    conn.close()

    print(f"Total error rows examined: {len(rows)} (skipped {skipped_synthetic} synthetic-error rows)")
    print(f"Rows changed: {len(updates)}")
    print(f"members_only: {before['members_only']} -> {after['members_only']}")
    print(f"privated:     {before['privated']} -> {after['privated']}")
    print(f"deleted:      {before['deleted']} -> {after['deleted']}")


if __name__ == "__main__":
    main()
