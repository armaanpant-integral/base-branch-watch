"""Daily-rotated event log. Pure, no rumps/AppKit import — stays hook-reusable like config.

Reproduces the prototype's daily-truncation + one-line-per-event logging
(base_branch_watch_app.py rotate_log_if_needed/log), routed through
core.config.config_dir() instead of a hardcoded personal path.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from base_branch_watch.core.config import config_dir

LOG_FILENAME = "base-branch-watch.log"
DAY_MARKER_FILENAME = ".base-branch-watch.logday"


def log_path() -> Path:
    return config_dir() / LOG_FILENAME


def _day_marker_path() -> Path:
    return config_dir() / DAY_MARKER_FILENAME


def rotate_if_needed(today: str | None = None) -> None:
    """Truncate the log at a day boundary, tracked via a sibling .logday marker.

    Calling this multiple times on the same day is a no-op after the first
    call — freshly-written content within the same day is never re-truncated.
    """
    if today is None:
        today = datetime.date.today().isoformat()

    marker_path = _day_marker_path()
    last_day = marker_path.read_text().strip() if marker_path.exists() else ""

    if today != last_day:
        log_path().write_text("")
        marker_path.write_text(today)


def append(line: str) -> None:
    """rotate_if_needed() then append line + "\\n" — one call per check-cycle event."""
    rotate_if_needed()
    with open(log_path(), "a") as f:
        f.write(line + "\n")
