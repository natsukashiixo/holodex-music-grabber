"""Regression test for the log-level bug: the root logger's own level used to
be set from `log_level` (main.py calls this with "INFO"), which silently
discarded every DEBUG record before it ever reached the file handler -
despite the file handler itself being explicitly set to DEBUG ("log
everything to file"). Confirmed live: an entire 3638-video --retry-failed run
produced zero DEBUG-level lines in the log file. The root logger must stay
at DEBUG unconditionally; only the console handler's verbosity should track
log_level.
"""
import logging
import logging.handlers

from src.logging_config import get_logger, setup_logging


def test_file_handler_receives_debug_records_even_with_info_log_level(tmp_path):
    setup_logging(log_dir=tmp_path, log_level="INFO", app_name="test-app")

    logger = get_logger("some.module")
    logger.debug("a debug-level message")

    log_file = tmp_path / "test-app.log"
    contents = log_file.read_text(encoding="utf-8")
    assert "a debug-level message" in contents


def test_console_handler_respects_requested_log_level(tmp_path):
    root = setup_logging(log_dir=tmp_path, log_level="WARNING", app_name="test-app2")

    console_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)
                        and not isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(console_handlers) == 1
    assert console_handlers[0].level == logging.WARNING

    file_handlers = [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(file_handlers) == 1
    assert file_handlers[0].level == logging.DEBUG
