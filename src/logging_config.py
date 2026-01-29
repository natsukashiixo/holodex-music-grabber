"""Logging configuration following Linux best practices."""
import logging
import logging.handlers
from pathlib import Path
from typing import Optional


def setup_logging(
    log_dir: Optional[Path] = None,
    log_level: str = "DEBUG",
    app_name: str = "holodex-music-grabber"
) -> logging.Logger:
    """
    Set up logging configuration.
    
    Logs are stored in:
    - ~/.local/share/<app_name>/logs/ (XDG Base Directory spec)
    - Falls back to logs/ in current directory if XDG dir not available
    
    Args:
        log_dir: Custom log directory (overrides default)
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        app_name: Application name for log directory
        
    Returns:
        Configured logger instance
    """
    # Determine log directory following Linux best practices
    if log_dir:
        log_path = Path(log_dir)
    else:
        # Try XDG Base Directory: ~/.local/share/<app>/logs/
        xdg_data_home = Path.home() / ".local" / "share" / app_name / "logs"
        # Use XDG if .local/share exists, otherwise fallback to logs/ in current directory
        if (Path.home() / ".local" / "share").exists():
            log_path = xdg_data_home
        else:
            log_path = Path("logs")
    
    # Create log directory if it doesn't exist
    log_path.mkdir(parents=True, exist_ok=True)
    
    # Log file path: <app_name>.log
    log_file = log_path / f"{app_name}.log"
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Format: timestamp, level, module, message
    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)-8s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler with rotation (10MB per file, keep 5 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)  # Log everything to file
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler (INFO and above)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Log the log file location
    logger.info(f"Logging to: {log_file}")
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)
