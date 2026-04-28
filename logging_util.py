"""File-based logging.

Writes to `logs/google_workspace_mcp.log` inside the project dir with log
rotation at 5MB x 3 backups. Does NOT write to stdout because this is a stdio
MCP — stdout is reserved for the protocol.

Usage anywhere:
    from logging_util import log
    log.info("doing the thing", extra={"tool": "gmail_send_email"})
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config

_PROJECT_DIR = Path(__file__).resolve().parent
_LOG_DIR = _PROJECT_DIR / "logs"
_LOG_FILE = _LOG_DIR / "google_workspace_mcp.log"


def _build_logger() -> logging.Logger:
    _LOG_DIR.mkdir(exist_ok=True)

    logger = logging.getLogger("google_workspace_mcp")
    logger.setLevel(getattr(logging, str(config.get("log_level", "INFO")).upper(), logging.INFO))

    # Avoid double-adding handlers on reload.
    if logger.handlers:
        return logger

    handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    logger.addHandler(handler)

    # Silence noisy Google discovery cache warning.
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

    return logger


log: logging.Logger = _build_logger()
