"""One-off: repair damage from the path-collision bug in the (now-fixed)
MusicDownloader.download() - two different videos sharing an identical
(post-sanitization) title under the same channel+topic used to collide on
the same output path, causing the second one retried to silently claim the
first's file as its own "successful" download, hash-match it, and delete it
via the strict-dedup logic.

Finds every `duplicate content of ...` row created live by process_video()
(as opposed to the ones dedupe_songs.py already produced, which are
legitimate and left untouched), verifies whether the referenced canonical
video's file is actually missing (confirming it's a real collision victim,
not a genuine duplicate), and resets BOTH rows in each confirmed pair to a
fresh retryable state - neither has verified real content anymore, so both
need an honest fresh download attempt now that the collision bug is fixed.

Usage:
    uv run python scripts/repair_collision_victims.py
"""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings

CANONICAL_RE = re.compile(r"^duplicate content of (\S+);")


def reset_row(cur, video_id: str):
    cur.execute("""
        UPDATE songs
        SET file_hash = NULL, file_path = NULL, deleted = 0,
            members_only = 0, privated = 0, error = NULL
        WHERE video_id = ?
    """, (video_id,))


def main():
    settings = resolve_settings()
    db_path = settings["db_path"]
    base_output_dir = settings["output_dir"]

    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT video_id, error FROM songs
        WHERE error LIKE 'duplicate content of%'
          AND error NOT LIKE '%by dedupe_songs.py%'
    """)
    rows = cur.fetchall()
    print(f"{len(rows)} live-created 'duplicate content of' rows to check")

    victims = 0
    not_victims = 0

    for row in rows:
        match = CANONICAL_RE.match(row["error"])
        if not match:
            continue
        canonical_id = match.group(1)

        canonical = cur.execute(
            "SELECT file_path FROM songs WHERE video_id = ?", (canonical_id,)
        ).fetchone()

        canonical_exists = bool(canonical and canonical["file_path"] and
                                 (base_output_dir / canonical["file_path"]).exists())
        if canonical_exists:
            # Canonical's file is fine - this really was a legitimate
            # duplicate, not a collision victim. Leave both rows alone.
            not_victims += 1
            continue

        victims += 1
        reset_row(cur, row["video_id"])
        reset_row(cur, canonical_id)
        print(f"  [RESET PAIR] {row['video_id']} <-> {canonical_id}")

    conn.commit()
    conn.close()

    print(f"Done. {victims} confirmed collision-victim pairs reset ({victims * 2} rows), {not_victims} were legitimate duplicates (untouched).")


if __name__ == "__main__":
    main()
