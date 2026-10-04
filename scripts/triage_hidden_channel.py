"""One-off: sort already-downloaded channel_id="HIDDEN" tracks by label.

Holodex reports channels it considers irrelevant as channel_id "HIDDEN";
those tracks all landed flat in <fallback>/__hidden/<topic>/. Each file's
embedded description carries YouTube's "Provided to YouTube by <label>" line
(written by yt-dlp's FFmpegMetadata). Allowlisted labels (config.toml
[Hidden] accepted_providers, else src.utils.DEFAULT_HIDDEN_PROVIDER_ALLOWLIST)
move to <...>/__hidden/<label>/<topic>/, the same layout process_video now
uses for new downloads. Everything else moves to <base>/_quarantine_hidden/
(same relative path) and its row is marked deleted with a
hidden_channel_rejected error, keeping file_hash (dedupe_songs.py convention),
so neither retry nor normal runs fetch it again.

DB-driven, idempotent: rows already in a label folder are left alone.

Usage:
    uv run python scripts/triage_hidden_channel.py --dry-run
    uv run python scripts/triage_hidden_channel.py
"""
import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import load_config
from scripts._common import backup_db, resolve_settings
from src.downloader import PROVIDED_BY_RE
from src.path_utils import fs_sanitize
from src.utils import DEFAULT_HIDDEN_PROVIDER_ALLOWLIST, HIDDEN_CHANNEL_ID, HIDDEN_REJECTED_ERROR

QUARANTINE_DIR = "_quarantine_hidden"


def provided_by(path: Path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
        capture_output=True, text=True,
    ).stdout
    tags = {k.lower(): v for k, v in json.loads(out or "{}").get("format", {}).get("tags", {}).items()}
    match = PROVIDED_BY_RE.search(tags.get("description") or tags.get("synopsis") or "")
    return match.group(1).strip() if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print planned moves without touching anything")
    args = parser.parse_args()

    settings = resolve_settings()
    db_path = settings["db_path"]
    base_output_dir = settings["output_dir"]
    allowlist = set((load_config() or {}).get("Hidden", {}).get("accepted_providers", DEFAULT_HIDDEN_PROVIDER_ALLOWLIST))

    if not args.dry_run:
        backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT video_id, file_path FROM songs WHERE channel_id = ? AND file_path IS NOT NULL", (HIDDEN_CHANNEL_ID,))
    rows = cur.fetchall()
    print(f"{len(rows)} HIDDEN-channel songs with a file_path; allowlist: {sorted(allowlist)}")

    stats = Counter()
    for row in rows:
        rel = Path(row["file_path"])
        src = base_output_dir / rel
        channel_dir = rel.parent.parent
        if not channel_dir.name.endswith(fs_sanitize(f"_{HIDDEN_CHANNEL_ID}")):
            stats["already sorted"] += 1
            continue
        if not src.exists():
            print(f"  [MISSING] {row['video_id']}: {src}")
            stats["missing"] += 1
            continue

        label = provided_by(src)
        if label in allowlist:
            dst_rel = channel_dir / fs_sanitize(label) / rel.parent.name / rel.name
            print(f"  [KEEP] {row['video_id']}: {rel.name} -> {dst_rel.parent}/")
            stats[f"kept: {label}"] += 1
            if args.dry_run:
                continue
            if (base_output_dir / dst_rel).exists():
                print(f"    [SKIP] target exists: {dst_rel}")
                stats["kept but target exists"] += 1
                continue
            (base_output_dir / dst_rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(base_output_dir / dst_rel))
            cur.execute("UPDATE songs SET file_path = ? WHERE video_id = ?", (str(dst_rel), row["video_id"]))
        else:
            stats["quarantined"] += 1
            if args.dry_run:
                continue
            dst = base_output_dir / QUARANTINE_DIR / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            cur.execute(
                "UPDATE songs SET deleted = 1, file_path = NULL, error = ? WHERE video_id = ?",
                (f"{HIDDEN_REJECTED_ERROR} (provided by {label or 'no label'})", row["video_id"]),
            )
        conn.commit()

    conn.close()
    print("Done." + (" (dry run)" if args.dry_run else ""))
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")


if __name__ == "__main__":
    main()
