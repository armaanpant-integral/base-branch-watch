"""AppConfig load/save — atomic JSON persistence, no rumps/AppKit import.

Config lives under config_dir() (~/Library/Application Support/base-branch-watch
by default), resolved through a single function so there is exactly one place
that decides "where do my files live" (ARCHITECTURE.md Anti-Pattern 2).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from base_branch_watch.core.models import RepoConfig

DEFAULT_POLL_INTERVAL_SECONDS = 300
CONFIG_FILENAME = "config.json"


def config_dir() -> Path:
    """Resolve (and create) the app's config directory.

    Honors BBW_CONFIG_DIR env override for tests; defaults to
    ~/Library/Application Support/base-branch-watch.
    """
    override = os.environ.get("BBW_CONFIG_DIR")
    if override:
        path = Path(override)
    else:
        path = Path(os.path.expanduser("~/Library/Application Support/base-branch-watch"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class AppConfig:
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    repos: list[RepoConfig] = field(default_factory=list)


def _config_path() -> Path:
    return config_dir() / CONFIG_FILENAME


def load_config() -> AppConfig:
    """Load AppConfig from disk, returning defaults if the file is missing or unreadable."""
    path = _config_path()
    if not path.exists():
        return AppConfig()
    try:
        with open(path) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return AppConfig()

    repos = [
        RepoConfig(repo_path=r["repo_path"], base_branches=list(r.get("base_branches", [])))
        for r in raw.get("repos", [])
    ]
    return AppConfig(
        poll_interval_seconds=raw.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS),
        repos=repos,
    )


def parse_base_branches(raw: str) -> list[str]:
    """Split on ',', strip whitespace, drop empties, dedupe preserving first-seen order."""
    seen: list[str] = []
    for part in raw.split(","):
        stripped = part.strip()
        if stripped and stripped not in seen:
            seen.append(stripped)
    return seen


def add_repo(cfg: AppConfig, repo_path: str, base_branches: list[str]) -> AppConfig:
    """Return a new AppConfig with repo_path's entry replaced (not duplicated) and appended.

    Pure — does not perform I/O. Callers persist the result via save_config.
    """
    remaining = [r for r in cfg.repos if r.repo_path != repo_path]
    remaining.append(RepoConfig(repo_path=repo_path, base_branches=list(base_branches)))
    return AppConfig(poll_interval_seconds=cfg.poll_interval_seconds, repos=remaining)


def remove_repo(cfg: AppConfig, repo_path: str) -> AppConfig:
    """Return a new AppConfig without repo_path's entry. No-op if the path is absent.

    Pure — does not perform I/O. Callers persist the result via save_config.
    """
    remaining = [r for r in cfg.repos if r.repo_path != repo_path]
    return AppConfig(poll_interval_seconds=cfg.poll_interval_seconds, repos=remaining)


def save_config(cfg: AppConfig) -> None:
    """Atomically persist AppConfig: write to a temp file, then os.replace onto the target."""
    directory = config_dir()
    target = directory / CONFIG_FILENAME
    payload = {
        "poll_interval_seconds": cfg.poll_interval_seconds,
        "repos": [
            {"repo_path": r.repo_path, "base_branches": list(r.base_branches)} for r in cfg.repos
        ],
    }
    tmp_path = directory / f".{CONFIG_FILENAME}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target)
