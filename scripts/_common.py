"""Shared helpers for one-off maintenance scripts."""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import load_config, expand_path  # noqa: E402


def backup_db(db_path: str) -> Path:
    """WAL-checkpoint and copy the DB to a timestamped backup file.
    Always call this before any one-off script mutates the database."""
    checkpoint_conn = sqlite3.connect(db_path)
    checkpoint_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    checkpoint_conn.close()

    src = Path(db_path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = src.with_name(f"{src.name}.pre-repair-{timestamp}.bak")

    source_conn = sqlite3.connect(db_path)
    backup_conn = sqlite3.connect(backup_path)
    source_conn.backup(backup_conn)
    backup_conn.close()
    source_conn.close()

    print(f"Backed up {db_path} -> {backup_path}")
    return backup_path


def resolve_settings(db_path_arg: str = None, output_dir_arg: str = None) -> dict:
    """Resolve db_path/output_dir/api_key the same way main.py does
    (config.toml defaults, optional explicit overrides)."""
    config = load_config()
    default_output_dir = Path("Music")
    default_db_path = "music.db"
    api_key = None

    if config:
        paths = config.get("Paths", {})
        if paths.get("download_folder"):
            default_output_dir = expand_path(paths["download_folder"])
        if paths.get("database_path"):
            db_path_str = paths["database_path"]
            db_path_expanded = expand_path(db_path_str)
            if db_path_expanded.is_dir() or db_path_str.endswith("/"):
                default_db_path = str(db_path_expanded / "music.db")
            else:
                default_db_path = str(db_path_expanded)
        api_key = config.get("Keys", {}).get("holodex_key")

    return {
        "db_path": db_path_arg or default_db_path,
        "output_dir": expand_path(output_dir_arg) if output_dir_arg else default_output_dir,
        "api_key": api_key,
    }
