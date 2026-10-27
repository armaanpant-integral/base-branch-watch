"""The `gh` seam: run a gh command, get back an outcome. No rumps/AppKit import, ever.

`GhClient` is the interface; `SubprocessGhClient` is the real adapter. Tests use an
in-memory adapter (tests/core/fake_gh.py). Every subprocess call passes an explicit
timeout= and an arg-list only, no shell string interpolation - mirrors
core/git_ops.py's discipline (see CLAUDE.md "What NOT to Use").

`gh` has no `-C <path>` flag (unlike git), so `cwd=repo_path` is used instead
(RESEARCH.md Pitfall 1, live-verified). There is no fallback install path: `gh`
has no canonical location, and "not on PATH" is itself the NOT_INSTALLED signal.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GhOutcome:
    returncode: int
    stdout: str
    stderr: str
    error: str | None = None
    """Set when gh never produced an exit status (timeout, OSError): the full message."""

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0


class GhClient(Protocol):
    @property
    def installed(self) -> bool: ...

    def run(self, repo_path: str | None, args: list[str], timeout: int) -> GhOutcome: ...


class SubprocessGhClient:
    @property
    def installed(self) -> bool:
        # Resolved per call so installing gh while the app runs is picked up without a restart.
        return shutil.which("gh") is not None

    def run(self, repo_path: str | None, args: list[str], timeout: int) -> GhOutcome:
        """Never raises: timeout and OSError come back as an outcome with `error` set."""
        binary = shutil.which("gh")
        if binary is None:
            return GhOutcome(returncode=-1, stdout="", stderr="", error="gh is not installed")
        try:
            result = subprocess.run(
                [binary, *args],
                cwd=repo_path,  # NOT "-C repo_path" -- gh has no such flag
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            command = " ".join(["gh", *args[:2]])
            return GhOutcome(
                returncode=-1, stdout="", stderr="", error=f"{command} timed out after {timeout}s"
            )
        except OSError as exc:
            return GhOutcome(returncode=-1, stdout="", stderr="", error=str(exc))
        return GhOutcome(result.returncode, result.stdout, result.stderr)
