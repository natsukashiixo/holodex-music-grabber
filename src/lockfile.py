"""Single-instance lock via flock - auto-releases if the holding process
dies for any reason (crash, SIGKILL), unlike a bare PID-file existence
check, which can leave a stale lock behind forever after a crash."""
import fcntl
from pathlib import Path
from typing import IO


class AlreadyRunningError(Exception):
    """Another instance already holds the lock."""


def acquire_lock(lock_path: Path) -> IO:
    """Acquire an exclusive, non-blocking lock on `lock_path`. Keep the
    returned handle referenced for the process's lifetime - the lock
    releases automatically when it's closed or the process exits/crashes.
    Raises AlreadyRunningError if another process already holds it."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        fh.close()
        raise AlreadyRunningError(f"Another instance already holds the lock at {lock_path}") from e
    return fh
