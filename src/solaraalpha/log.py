"""Logging setup. Call ``get_logger(__name__)`` in every module."""

from __future__ import annotations

import logging

from solaraalpha.config import settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        logging.basicConfig(level=settings.log_level.upper(), format=_FORMAT, datefmt="%H:%M:%S")
        _configured = True
    return logging.getLogger(name)
