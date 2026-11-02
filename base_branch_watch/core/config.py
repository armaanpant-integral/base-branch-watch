"""AppConfig load/save — JSON persistence via core.store, no rumps/AppKit import.

Where the file lives and how it is written is core.store's job (it owns
config_dir()); this module owns what the config means.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from base_branch_watch.core import log, store
from base_branch_watch.core.models import (
    DEFAULT_MERGE_TREE_TIMEOUT_SECONDS,
    RepoConfig,
    is_valid_merge_tree_timeout,
)
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
    timeout_fallbacks: Mapping[str, object] = field(default_factory=dict)
    """repo_path -> the raw, invalid merge_tree_timeout_seconds the user wrote, for repos
    whose value was replaced by the default at load time. A read-only caller like the
    pre-push hook uses it to say so; save_config writes the raw value back unchanged so a
    save never erases the user's text (only an explicit edit to the repo's timeout does)."""
    skipped_entries: list[object] = field(default_factory=list)
    """Raw repo entries that could not be loaded (non-dict, missing/non-str/unhashable
    repo_path, or a duplicate repo_path). Written back unchanged on save so a save never
    silently erases a user's entry."""
    extra_keys: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    """repo_path -> keys on that entry this app does not know, kept across saves."""


def _parse_merge_tree_timeout(repo_path: str, entry: dict, report: Callable[[str], None]) -> int:
    """Validated per-repo merge-tree timeout. Absent -> default; anything but an int in
    1..MAX (bool, str, float, out of range) falls back to the default and is reported."""
    if "merge_tree_timeout_seconds" not in entry:
        return DEFAULT_MERGE_TREE_TIMEOUT_SECONDS
    raw = entry["merge_tree_timeout_seconds"]
    if is_valid_merge_tree_timeout(raw):
        return raw
    report(
        f"config: ignoring invalid merge_tree_timeout_seconds={_clip(raw)} for {repo_path}; "
        f"using default {DEFAULT_MERGE_TREE_TIMEOUT_SECONDS}"
    )
    return DEFAULT_MERGE_TREE_TIMEOUT_SECONDS


_KNOWN_ENTRY_KEYS = frozenset({"repo_path", "base_branches", "merge_tree_timeout_seconds"})
_MAX_LOGGED_RAW_CHARS = 100


def _clip(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _MAX_LOGGED_RAW_CHARS else text[:_MAX_LOGGED_RAW_CHARS] + "..."


def load_config(*, quarantine: bool = False) -> AppConfig:
    """Load AppConfig from disk, returning defaults if the file is missing or unusable.

    `quarantine=True` (the app, the single writer) moves a corrupt file aside so the next
    save cannot silently erase the watched repos, and logs why. The pre-push hook reads
    without it and touches nothing in the app dir, the log included.
    """
    report = log.append if quarantine else store.ignore
    raw = store.read_object(CONFIG_FILENAME, quarantine=quarantine, report=report)
    if raw is None:
        return AppConfig()

    fallbacks: dict[str, object] = {}
    skipped: list[object] = []
    extras: dict[str, dict[str, object]] = {}
    seen_paths: set[str] = set()

    try:
        repos = []
        for entry in raw.get("repos", []):
            if not isinstance(entry, dict):
                skipped.append(entry)
                report(f"config: skipping non-object repo entry {_clip(entry)}; kept as written")
                continue
            repo_path = entry.get("repo_path")
            if not isinstance(repo_path, str):  # also guards unhashable paths
                skipped.append(entry)
                report(
                    f"config: skipping repo entry without a string repo_path "
                    f"{_clip(entry)}; kept as written"
                )
                continue
            if repo_path in seen_paths:
                skipped.append(entry)
                report(
                    f"config: skipping duplicate repo_path {repo_path}; "
                    "first entry wins, duplicate kept as written"
                )
                continue
            seen_paths.add(repo_path)
            unknown = {k: v for k, v in entry.items() if k not in _KNOWN_ENTRY_KEYS}
            if unknown:
                extras[repo_path] = unknown

            def note_fallback(message: str, path: str = repo_path, entry: dict = entry) -> None:
                fallbacks[path] = entry["merge_tree_timeout_seconds"]
                report(message)

            repos.append(
                RepoConfig(
                    repo_path=repo_path,
                    base_branches=list(entry.get("base_branches", [])),
                    merge_tree_timeout_seconds=_parse_merge_tree_timeout(
                        repo_path, entry, note_fallback
                    ),
                )
            )
        poll_interval = raw.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    except (AttributeError, TypeError, KeyError, ValueError):
        if quarantine:
            store.quarantine_file(CONFIG_FILENAME, "unexpected shape", report=report)
        return AppConfig()
    return AppConfig(
        poll_interval_seconds=poll_interval,
        repos=repos,
        timeout_fallbacks=fallbacks,
        skipped_entries=skipped,
        extra_keys=extras,
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
    existing = next((r for r in cfg.repos if r.repo_path == repo_path), None)
    timeout = (
        existing.merge_tree_timeout_seconds
        if existing is not None
        else DEFAULT_MERGE_TREE_TIMEOUT_SECONDS
    )
    remaining = [r for r in cfg.repos if r.repo_path != repo_path]
    remaining.append(
        RepoConfig(
            repo_path=repo_path,
            base_branches=list(base_branches),
            merge_tree_timeout_seconds=timeout,
        )
    )
    return AppConfig(
        poll_interval_seconds=cfg.poll_interval_seconds,
        repos=remaining,
        timeout_fallbacks=dict(cfg.timeout_fallbacks),
        skipped_entries=list(cfg.skipped_entries),
        extra_keys=dict(cfg.extra_keys),
    )


def remove_repo(cfg: AppConfig, repo_path: str) -> AppConfig:
    """Return a new AppConfig without repo_path's entry. No-op if the path is absent.

    Pure — does not perform I/O. Callers persist the result via save_config.
    """
    remaining = [r for r in cfg.repos if r.repo_path != repo_path]
    fallbacks = {p: v for p, v in cfg.timeout_fallbacks.items() if p != repo_path}
    extras = {p: v for p, v in cfg.extra_keys.items() if p != repo_path}
    # A duplicate of the removed path would otherwise resurrect it on the next load.
    skipped = [
        e
        for e in cfg.skipped_entries
        if not (isinstance(e, dict) and e.get("repo_path") == repo_path)
    ]
    return AppConfig(
        poll_interval_seconds=cfg.poll_interval_seconds,
        repos=remaining,
        timeout_fallbacks=fallbacks,
        skipped_entries=skipped,
        extra_keys=extras,
    )


def set_poll_interval(cfg: AppConfig, seconds: int | str) -> AppConfig:
    """Return a new AppConfig with poll_interval_seconds set, clamped to a 30s floor.

    Coerces seconds to int (accepts a numeric string from UI input). Pure —
    does not perform I/O or mutate cfg. Callers persist via save_config.
    Raises ValueError if seconds is not coercible to int (caller's job to
    catch and surface as a user-facing alert, not a crash).
    """
    value = int(seconds)
    clamped = max(value, MIN_POLL_INTERVAL_SECONDS)
    return AppConfig(
        poll_interval_seconds=clamped,
        repos=list(cfg.repos),
        timeout_fallbacks=dict(cfg.timeout_fallbacks),
        skipped_entries=list(cfg.skipped_entries),
        extra_keys=dict(cfg.extra_keys),
    )


def _repo_payload(
    repo: RepoConfig, fallbacks: Mapping[str, object], extras: Mapping[str, Mapping[str, object]]
) -> dict:
    entry: dict = dict(extras.get(repo.repo_path, {}))
    entry |= {"repo_path": repo.repo_path, "base_branches": list(repo.base_branches)}
    if repo.merge_tree_timeout_seconds != DEFAULT_MERGE_TREE_TIMEOUT_SECONDS:
        entry["merge_tree_timeout_seconds"] = repo.merge_tree_timeout_seconds
    elif repo.repo_path in fallbacks:
        # Invalid on load and not explicitly edited since: keep the user's raw text.
        entry["merge_tree_timeout_seconds"] = fallbacks[repo.repo_path]
    return entry


def save_config(cfg: AppConfig) -> WriteResult:
    """Atomically persist AppConfig. Never raises; the result says whether it was saved."""
    payload = {
        "poll_interval_seconds": cfg.poll_interval_seconds,
        "repos": [_repo_payload(r, cfg.timeout_fallbacks, cfg.extra_keys) for r in cfg.repos]
        + list(cfg.skipped_entries),
    }
    return store.write_object(CONFIG_FILENAME, payload, report=log.append)
