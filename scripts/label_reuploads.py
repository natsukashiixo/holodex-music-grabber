"""One-off: relabel past path collisions that are likely re-uploads.

Before re-upload labelling existed, a second video colliding on the same
output path (same channel + same sanitized title) always got an opaque
`_<video_id>` suffix - whichever one happened to be downloaded second, which
in newest-first retry runs was often the *original*. This applies the same
rule process_video now uses (src.utils.is_likely_reupload: durations within
1s): the oldest upload of the group gets the clean name and every newer one
gets `_possible_reupYYMMDD` (its own upload date). Collisions whose durations
differ or are unknown, or whose clean-path file isn't tracked in the DB, are
left alone.

Driven entirely by DB rows - never scans the music directory directly.
Idempotent: relabelled files no longer end in `_<video_id>.mp3`, so a re-run
finds nothing left to do.

Usage:
    uv run python scripts/label_reuploads.py --dry-run
    uv run python scripts/label_reuploads.py
"""
import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings
from src.utils import is_likely_reupload, reupload_path

REUP_STEM_RE = re.compile(r"_possible_reup\d{6}\.mp3$")


def plan_groups(rows: list) -> dict:
    """Map each clean path to its likely re-upload group (owner first, then suffixed siblings)."""
    by_path = {row["file_path"]: row for row in rows}
    groups = defaultdict(list)
    for row in rows:
        suffix = f"_{row['video_id']}.mp3"
        if not row["file_path"].endswith(suffix):
            continue
        clean = row["file_path"][: -len(suffix)] + ".mp3"
        if REUP_STEM_RE.search(clean):
            # Already labelled: a same-day re-upload named _possible_reupYYMMDD_<video_id>
            continue
        owner = by_path.get(clean)
        if owner is None or not is_likely_reupload(row["duration"], owner["duration"]):
            continue
        if not groups[clean]:
            groups[clean].append(owner)
        groups[clean].append(row)
    return groups


def target_paths(clean: Path, members: list, base_output_dir: Path) -> dict:
    """video_id -> target relative path: oldest keeps `clean`, the rest get _possible_reupYYMMDD.
    Length limits are applied to the absolute path, same as at download time."""
    ordered = sorted(members, key=lambda r: (r["available_at"], r["video_id"]))
    targets = {ordered[0]["video_id"]: clean}
    taken = {clean}
    for row in ordered[1:]:
        target = reupload_path(base_output_dir / clean, row["available_at"], row["video_id"]).relative_to(base_output_dir)
        if target in taken:
            target = target.with_name(f"{target.stem}_{row['video_id']}{target.suffix}")
        targets[row["video_id"]] = target
        taken.add(target)
    return targets


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print planned renames without touching anything")
    args = parser.parse_args()

    settings = resolve_settings()
    db_path = settings["db_path"]
    base_output_dir = settings["output_dir"]

    if not args.dry_run:
        backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT video_id, file_path, available_at, duration
        FROM songs
        WHERE file_path IS NOT NULL
    """)
    groups = plan_groups(cur.fetchall())
    print(f"{len(groups)} collision groups look like re-uploads")

    renamed = 0
    skipped = 0
    for clean_str, members in groups.items():
        clean = Path(clean_str)
        current = {row["video_id"]: Path(row["file_path"]) for row in members}
        targets = target_paths(clean, members, base_output_dir)
        moves = {vid: (current[vid], targets[vid]) for vid in current if current[vid] != targets[vid]}
        if not moves:
            continue

        missing = [str(src) for src, _ in moves.values() if not (base_output_dir / src).exists()]
        group_paths = set(current.values())
        blocked = [str(dst) for _, dst in moves.values()
                   if dst not in group_paths and (base_output_dir / dst).exists()]
        if missing or blocked:
            print(f"  [SKIP] {clean_str}: missing={missing} blocked={blocked}")
            skipped += 1
            continue

        for vid, (src, dst) in moves.items():
            print(f"  {vid}: {src.name} -> {dst.name}")
        if args.dry_run:
            renamed += len(moves)
            continue

        # Two-phase via temp names, since group members can swap places.
        temps = {}
        for vid, (src, _) in moves.items():
            temp = base_output_dir / src.with_name(f"{src.name}.relabel-{vid}.tmp")
            (base_output_dir / src).rename(temp)
            temps[vid] = temp
        for vid, (_, dst) in moves.items():
            temps[vid].rename(base_output_dir / dst)
            cur.execute("UPDATE songs SET file_path = ? WHERE video_id = ?", (str(dst), vid))
        conn.commit()
        renamed += len(moves)

    conn.close()
    verb = "would rename" if args.dry_run else "renamed"
    print(f"Done. {verb}={renamed} files, skipped_groups={skipped}")


if __name__ == "__main__":
    main()
