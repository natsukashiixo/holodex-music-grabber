"""One-off: re-fetch every known channel from the Holodex /channels/{id}
endpoint and overwrite name/org/sub_org with freshly, correctly-sanitized
values. Fixes the non-idempotent sub_org corruption baked into the DB before
sanitize_suborg was choke-pointed to a single call site.

Resumable: progress is checkpointed to
scripts/state/suborg_repair_progress.jsonl, so an interrupted run (rate
limited to 1 request per 2s, ~90 min for ~2665 channels) can pick back up
without re-spending API calls.

Usage:
    uv run python scripts/repair_channel_suborgs.py
    uv run python scripts/repair_channel_suborgs.py --only <channel_id>,<channel_id>
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import backup_db, resolve_settings
from src.holodex import HolodexClient
from src.path_utils import sanitize_suborg

STATE_DIR = Path(__file__).resolve().parent / "state"
PROGRESS_FILE = STATE_DIR / "suborg_repair_progress.jsonl"


def load_progress() -> dict:
    STATE_DIR.mkdir(exist_ok=True)
    done = {}
    if PROGRESS_FILE.exists():
        with PROGRESS_FILE.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    entry = json.loads(line)
                    done[entry["channel_id"]] = entry
    return done


def append_progress(entry: dict):
    with PROGRESS_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def is_ambiguous(raw_suborg: str, chosen_suborg: str) -> bool:
    if raw_suborg == chosen_suborg:
        return False
    if len(chosen_suborg) < 4:
        return True
    # Uppercase-letter-abbreviation prefixes (e.g. "TFToy's Factory") aren't
    # handled by the automatic rule - flag them for manual review.
    if raw_suborg[:2].isalpha() and raw_suborg[:2].isupper():
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Repair corrupted channel sub_org values")
    parser.add_argument("--only", help="Comma-separated channel_ids to (re)process regardless of progress log")
    args = parser.parse_args()

    settings = resolve_settings()
    db_path = settings["db_path"]
    api_key = settings["api_key"]
    if not api_key:
        raise SystemExit("Holodex API key required (config.toml [Keys] holodex_key)")

    backup_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT channel_id FROM channels ORDER BY channel_id")
    all_channel_ids = [row["channel_id"] for row in cur.fetchall()]

    only_ids = set(args.only.split(",")) if args.only else None
    progress = load_progress()

    client = HolodexClient(api_key=api_key)
    processed = 0
    ambiguous_count = 0
    skipped_errors = 0

    try:
        for channel_id in all_channel_ids:
            if only_ids is not None and channel_id not in only_ids:
                continue
            if only_ids is None and channel_id in progress:
                continue

            client.rate_limiter.wait()
            try:
                response = client.client.get(f"/channels/{channel_id}")
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                print(f"  [SKIP] {channel_id}: {e}")
                skipped_errors += 1
                append_progress({"channel_id": channel_id, "raw_suborg": None, "chosen_suborg": None, "ambiguous": False, "error": str(e)})
                continue

            raw_suborg = data.get("suborg") or "zzFALLBACK"
            chosen_suborg = sanitize_suborg(raw_suborg)
            name = data.get("name")
            org = data.get("org")
            ambiguous = is_ambiguous(raw_suborg, chosen_suborg)

            cur.execute(
                "UPDATE channels SET name = ?, org = ?, sub_org = ? WHERE channel_id = ?",
                (name, org, chosen_suborg, channel_id),
            )
            conn.commit()

            append_progress({
                "channel_id": channel_id,
                "raw_suborg": raw_suborg,
                "chosen_suborg": chosen_suborg,
                "ambiguous": ambiguous,
            })
            processed += 1
            if ambiguous:
                ambiguous_count += 1
                print(f"  [AMBIGUOUS] {channel_id}: {raw_suborg!r} -> {chosen_suborg!r}")

            if processed % 50 == 0:
                print(f"  ... {processed} channels processed")
    finally:
        client.close()
        conn.close()

    print(f"Done. Processed {processed}, ambiguous {ambiguous_count}, errors/skipped {skipped_errors}.")
    if ambiguous_count:
        print(f"Review ambiguous entries in {PROGRESS_FILE}, add corrections to")
        print("_SUBORG_MANUAL_OVERRIDES in src/path_utils.py, then re-run with:")
        print("  uv run python scripts/repair_channel_suborgs.py --only <id1>,<id2>,...")


if __name__ == "__main__":
    main()
