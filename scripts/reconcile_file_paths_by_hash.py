"""One-off: reconcile songs.file_path for rows whose recorded path no longer
resolves anywhere expected, by hashing the actual files still present under
base_output_dir and matching them against songs.file_hash. Physical files in
this collection have been independently reorganized/renamed multiple times
across the project's history (blind-suborg-strip corruption, the "Yuni
Create" mis-nesting, and at least one further unlogged manual rename found
live) without the DB ever being updated - guessing one more path
transformation at a time doesn't scale, so this matches by content instead.

Only ever assigns a recovered path to the row that dedupe_songs.py will later
treat as canonical for that file_hash (earliest available_at among deleted=0
rows sharing the hash). This avoids linking two DB rows to the same physical
file, which would make dedupe_songs.py delete a file out from under whichever
row it decides is canonical.

Read-only against the filesystem except for the final file_path UPDATEs - no
files are moved or deleted by this script. Run before dedupe_songs.py.

Usage:
    uv run python scripts/reconcile_file_paths_by_hash.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings
from src.db import hash_file


def main():
    settings = resolve_settings()
    db_path = settings["db_path"]
    base_output_dir = settings["output_dir"].resolve()

    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT video_id, file_hash, file_path, available_at
        FROM songs
        WHERE deleted = 0 AND file_hash IS NOT NULL
        ORDER BY file_hash, available_at ASC
    """)
    all_rows = cur.fetchall()

    groups = {}
    for row in all_rows:
        groups.setdefault(row["file_hash"], []).append(row)

    known_good_paths = set()
    canonical_needs_fixing = {}  # file_hash -> canonical row (needs a real path)

    for file_hash, rows in groups.items():
        canonical = rows[0]  # earliest available_at - matches dedupe_songs.py's tie-break
        canonical_path = base_output_dir / canonical["file_path"] if canonical["file_path"] else None
        if canonical_path and canonical_path.exists():
            known_good_paths.add(canonical_path.resolve())
        else:
            canonical_needs_fixing[file_hash] = canonical

        # Any other row in the group with a still-resolving path is already
        # correctly accounted for (dedupe_songs.py will clean it up later if
        # it's a real duplicate) - no need to ever hash it.
        for row in rows[1:]:
            if row["file_path"]:
                p = base_output_dir / row["file_path"]
                if p.exists():
                    known_good_paths.add(p.resolve())

    print(f"{len(groups)} distinct file_hash groups, {len(canonical_needs_fixing)} canonical rows need a real path")

    wanted_hashes = set(canonical_needs_fixing.keys())
    found_for_hash = {}

    scanned = 0
    hashed = 0
    for path in base_output_dir.rglob("*.mp3"):
        if not wanted_hashes:
            break
        if not path.is_file():
            continue
        scanned += 1
        if path.resolve() in known_good_paths:
            continue

        file_hash = hash_file(path)
        hashed += 1
        if file_hash in wanted_hashes:
            found_for_hash[file_hash] = path
            wanted_hashes.discard(file_hash)

        if scanned % 5000 == 0:
            print(f"  ... scanned {scanned} files, hashed {hashed}, still looking for {len(wanted_hashes)}")

    fixed = 0
    for file_hash, row in canonical_needs_fixing.items():
        found_path = found_for_hash.get(file_hash)
        if not found_path:
            continue
        new_file_path = str(found_path.resolve().relative_to(base_output_dir))
        cur.execute(
            "UPDATE songs SET file_path = ? WHERE video_id = ?",
            (new_file_path, row["video_id"]),
        )
        fixed += 1
    conn.commit()
    conn.close()

    print(
        f"Done. scanned={scanned} hashed={hashed} fixed={fixed} "
        f"still_unresolved={len(canonical_needs_fixing) - fixed}"
    )


if __name__ == "__main__":
    main()
