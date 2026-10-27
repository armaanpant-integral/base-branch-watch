"""The app data directory and the JSON files in it. No rumps/AppKit import, ever.

One place decides "where do my files live" (ARCHITECTURE.md Anti-Pattern 2) and how a
JSON object gets on and off disk: atomic writes (temp file + os.replace, T-4-04), tolerant
reads. Disk problems never raise out of here: they come back as a result / None and, when
the caller passes `report` (normally `log.append`), as a `[FAIL]` line. This module knows
nothing about logging itself, so core.log can build on it without a cycle.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Report = Callable[[str], None]


def ignore(_line: str) -> None:
    """The default `report`: say nothing."""


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    error: str | None = None


def config_dir() -> Path:
    """Resolve (and create) the app's data directory.

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


def read_object(filename: str, *, quarantine: bool = False, report: Report = ignore) -> dict | None:
    """The JSON object stored in `filename`, or None if missing / unreadable / not an object.

    With `quarantine=True` a corrupt file is moved to `<filename>.corrupt` so the next
    save cannot overwrite the evidence. Only the app (the single writer) should ask for
    that; the one-shot pre-push hook reads with the default and mutates nothing. A file
    that merely cannot be read (permissions) is not corruption and is never moved.
    """
    try:
        path = config_dir() / filename
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        problem = f"{filename} is not valid JSON ({exc})"
    except OSError as exc:
        report(f"[FAIL] could not read {filename}: {exc}")
        return None
    else:
        if isinstance(raw, dict):
            return raw
        problem = f"{filename} is not a JSON object"
    report(f"[FAIL] {problem}")
    if quarantine:
        quarantine_file(filename, problem, report=report)
    return None


def quarantine_file(filename: str, reason: str, *, report: Report = ignore) -> None:
    """Move `filename` aside to `<filename>.corrupt` (the latest bad copy wins)."""
    try:
        path = config_dir() / filename
        aside = path.with_name(f"{filename}.corrupt")
        os.replace(path, aside)
    except OSError as exc:
        report(f"[FAIL] could not move {filename} aside ({reason}): {exc}")
        return
    report(f"[FAIL] {filename} moved to {aside.name} ({reason}); starting from defaults")


def write_object(filename: str, payload: dict, *, report: Report = ignore) -> WriteResult:
    """Atomically persist `payload`: write a temp file, then os.replace onto the target."""
    tmp_path: Path | None = None
    try:
        directory = config_dir()
        tmp_path = directory / f".{filename}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, directory / filename)
    except (OSError, TypeError, ValueError) as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        report(f"[FAIL] could not save {filename}: {exc}")
        return WriteResult(ok=False, error=str(exc))
    return WriteResult(ok=True)
