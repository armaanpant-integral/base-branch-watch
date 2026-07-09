"""Per-(repo,base) last-notified-SHA persistence — the app's own dedupe state.

State is app-owned, single-writer (ARCHITECTURE.md Key Data Flows point 2) —
the pre-push hook (a separate, one-shot process) never reads or writes this
file; only app/menubar.py's polling cycle does. Same atomic-write discipline
as core.config (temp file + os.replace, T-4-04 mitigation).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from base_branch_watch.core import git_ops
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


def base_head_sha(repo_path: str, base: str, timeout: int = 10) -> str | None:
    """Resolve `origin/<base>`'s current SHA — the value dedupe decisions compare against."""
    return git_ops.resolve_ref(repo_path, f"origin/{base}", timeout)


def should_notify(state: State, repo_path: str, base: str, current_sha: str) -> bool:
    """True unless current_sha already matches the last-notified SHA for this (repo, base)."""
    last = state.get(repo_path, {}).get(base)
    return last != current_sha


def mark_notified(state: State, repo_path: str, base: str, current_sha: str) -> State:
    """Record current_sha as the last-notified SHA for this (repo, base). Returns state."""
    repo_state = state.setdefault(repo_path, {})
    repo_state[base] = current_sha
    return state
