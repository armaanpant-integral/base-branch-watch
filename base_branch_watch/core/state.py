"""Per-(repo,base) last-notified-SHA persistence - the app's own dedupe state.

State is app-owned, single-writer (ARCHITECTURE.md Key Data Flows point 2) -
the pre-push hook (a separate, one-shot process) never reads or writes this
file; only app/menubar.py's polling cycle does. Same atomic-write discipline
as core.config (temp file + os.replace, T-4-04 mitigation). What the stored
values mean (dedupe rules, reserved keys) lives in core.notify_policy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from base_branch_watch.core.config import config_dir

STATE_FILENAME = "state.json"

# {repo_path: {base: last_notified_sha}}
State = dict[str, dict[str, str]]


def _state_path() -> Path:
    return config_dir() / STATE_FILENAME


def load_state() -> State:
    """Load State from disk, returning {} if the file is missing or unreadable."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def save_state(state: State) -> None:
    """Atomically persist State: write to a temp file, then os.replace onto the target."""
    directory = config_dir()
    target = directory / STATE_FILENAME
    tmp_path = directory / f".{STATE_FILENAME}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target)
