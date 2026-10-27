"""AppConfig load/save — JSON persistence via core.store, no rumps/AppKit import.

Where the file lives and how it is written is core.store's job (it owns
config_dir()); this module owns what the config means.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from base_branch_watch.core import log, store
from base_branch_watch.core.models import RepoConfig
from base_branch_watch.core.store import WriteResult
from base_branch_watch.core.store import config_dir as config_dir  # re-export: existing callers

DEFAULT_POLL_INTERVAL_SECONDS = 300
# Floor exists so a mistyped or too-low interval entered via the "Set
# Interval..." menu prompt can't hammer git fetch or the gh API with an
# overly tight polling loop.
MIN_POLL_INTERVAL_SECONDS = 30
CONFIG_FILENAME = "config.json"


@dataclass
class AppConfig:
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    repos: list[RepoConfig] = field(default_factory=list)


def load_config(*, quarantine: bool = False) -> AppConfig:
    """Load AppConfig from disk, returning defaults if the file is missing or unusable.

    `quarantine=True` (the app, the single writer) moves a corrupt file aside so the next
    save cannot silently erase the watched repos; the pre-push hook reads without it.
    """
    raw = store.read_object(CONFIG_FILENAME, quarantine=quarantine, report=log.append)
    if raw is None:
        return AppConfig()

    try:
        repos = [
            RepoConfig(repo_path=r["repo_path"], base_branches=list(r.get("base_branches", [])))
            for r in raw.get("repos", [])
            if isinstance(r, dict) and "repo_path" in r
        ]
        poll_interval = raw.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    except (AttributeError, TypeError, KeyError):
        if quarantine:
            store.quarantine_file(CONFIG_FILENAME, "unexpected shape", report=log.append)
        return AppConfig()
    return AppConfig(poll_interval_seconds=poll_interval, repos=repos)


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


def set_poll_interval(cfg: AppConfig, seconds: int | str) -> AppConfig:
    """Return a new AppConfig with poll_interval_seconds set, clamped to a 30s floor.

    Coerces seconds to int (accepts a numeric string from UI input). Pure —
    does not perform I/O or mutate cfg. Callers persist via save_config.
    Raises ValueError if seconds is not coercible to int (caller's job to
    catch and surface as a user-facing alert, not a crash).
    """
    value = int(seconds)
    clamped = max(value, MIN_POLL_INTERVAL_SECONDS)
    return AppConfig(poll_interval_seconds=clamped, repos=list(cfg.repos))


def save_config(cfg: AppConfig) -> WriteResult:
    """Atomically persist AppConfig. Never raises; the result says whether it was saved."""
    payload = {
        "poll_interval_seconds": cfg.poll_interval_seconds,
        "repos": [
            {"repo_path": r.repo_path, "base_branches": list(r.base_branches)} for r in cfg.repos
        ],
    }
    return store.write_object(CONFIG_FILENAME, payload, report=log.append)
