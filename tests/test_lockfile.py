"""Tests for the single-instance flock-based lock.

Linux flock() is scoped to the *open file description*, not the process, so
two separate open() calls on the same path within a single test process
correctly exercise real cross-process-style contention.
"""
import pytest

from src.lockfile import AlreadyRunningError, acquire_lock


def test_second_acquire_on_same_path_raises(tmp_path):
    lock_path = tmp_path / "test.lock"

    first = acquire_lock(lock_path)
    try:
        with pytest.raises(AlreadyRunningError):
            acquire_lock(lock_path)
    finally:
        first.close()


def test_lock_is_released_after_close(tmp_path):
    lock_path = tmp_path / "test.lock"

    first = acquire_lock(lock_path)
    first.close()

    second = acquire_lock(lock_path)
    second.close()


def test_creates_parent_directories(tmp_path):
    lock_path = tmp_path / "nested" / "dir" / "test.lock"

    handle = acquire_lock(lock_path)
    try:
        assert lock_path.exists()
    finally:
        handle.close()
