"""One-off: backfill songs.duration for existing rows via per-channel
paginated /videos queries (bulk, ~50 videos/request) rather than one request
per song - same cost class (~90 min for ~2665 channels) as
repair_channel_suborgs.py, not 52k individual requests. Metadata-only, no
bandwidth/yt-dlp cost. Resumable via a JSONL progress checkpoint.

Usage:
    uv run python scripts/backfill_duration.py
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings
from src.holodex import HolodexClient

STATE_DIR = Path(__file__).resolve().parent / "state"
PROGRESS_FILE = STATE_DIR / "duration_backfill_progress.jsonl"


def load_done_channel_ids() -> set:
    STATE_DIR.mkdir(exist_ok=True)
    done = set()
    if PROGRESS_FILE.exists():
        with PROGRESS_FILE.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(json.loads(line)["channel_id"])
    return done


def append_progress(channel_id: str, updated: int):
    with PROGRESS_FILE.open("a") as f:
        f.write(json.dumps({"channel_id": channel_id, "updated": updated}) + "\n")


def main():
    settings = resolve_settings()
    db_path = settings["db_path"]
    api_key = settings["api_key"]
    if not api_key:
        raise SystemExit("Holodex API key required (config.toml [Keys] holodex_key)")

    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT channel_id FROM songs WHERE duration IS NULL")
    channel_ids = [row["channel_id"] for row in cur.fetchall()]

    done = load_done_channel_ids()
    todo = [c for c in channel_ids if c not in done]
    print(f"{len(channel_ids)} channels have songs missing duration, {len(todo)} remaining to process")

    client = HolodexClient(api_key=api_key)
    total_updated = 0

    try:
        for i, channel_id in enumerate(todo, 1):
            channel_updated = 0
            for topic in ("Music_Cover", "Original_Song"):
                offset = 0
                while True:
                    videos = client.query_videos(channel_id=channel_id, topic=topic, limit=50, offset=offset)
                    if not videos:
                        break

                    updates = [(v.duration, v.video_id) for v in videos if v.duration is not None]
                    if updates:
                        changes_before = conn.total_changes
                        cur.executemany(
                            "UPDATE songs SET duration = ? WHERE video_id = ? AND duration IS NULL",
                            updates,
                        )
                        conn.commit()
                        # len(updates) counts API results, not rows actually
                        # changed in *our* songs table - use the connection's
                        # real change counter instead so progress numbers are
                        # trustworthy (a channel query can return many videos
                        # that aren't in our DB at all, or already have duration set).
                        channel_updated += conn.total_changes - changes_before

                    if len(videos) < 50:
                        break
                    offset += 50

            append_progress(channel_id, channel_updated)
            total_updated += channel_updated

            if i % 50 == 0:
                print(f"  ... {i}/{len(todo)} channels processed, {total_updated} durations backfilled so far")
    finally:
        client.close()
        conn.close()

    print(f"Done. {total_updated} song durations backfilled across {len(todo)} channels.")


if __name__ == "__main__":
    main()
