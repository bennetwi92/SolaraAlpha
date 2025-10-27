"""Centralized logging configuration for SolaraAlpha.

Provides structured JSON logging with rotating file handlers for all components.
"""

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs logs as JSON with standard fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string.

        Args:
            record: Log record to format

        Returns:
            JSON-formatted log string
        """
        log_data: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
        }

        # Add context if available (passed via extra={} in log calls)
        if hasattr(record, "context"):
            log_data["context"] = record.context

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_logging(
    log_dir: str = "logs",
    log_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 7,
) -> None:
    """Configure centralized logging for SolaraAlpha.

    Sets up structured JSON logging with rotating file handlers.
    Daily rotation with 7-day retention by default.

    Args:
        log_dir: Directory to store log files
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        max_bytes: Maximum bytes per log file before rotation
        backup_count: Number of backup files to keep

    Example:
        >>> setup_logging(log_level="DEBUG")  # For development
        >>> setup_logging(log_level="INFO")   # For production
    """
    # Create log directory if it doesn't exist
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))

    # Remove existing handlers to avoid duplicates
    root_logger.handlers = []

    # Create rotating file handler
    log_file = os.path.join(log_dir, "app.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(file_handler)

    # Create console handler for immediate feedback (non-JSON for readability)
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # Log initial setup message
    root_logger.info(
        "Logging system initialized",
        extra={"context": {"log_dir": log_dir, "level": log_level}},
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a specific component.

    Args:
        name: Logger name (typically __name__ from calling module)

    Returns:
        Logger instance configured with centralized settings

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Operation completed", extra={"context": {"count": 42}})
    """
    return logging.getLogger(name)
