"""All git subprocess invocation lives here. No rumps/AppKit import, ever.

Every subprocess.run call passes an explicit timeout= and an arg-list only,
no shell string interpolation — see PITFALLS.md Performance Traps + CLAUDE.md
What NOT to Use.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from base_branch_watch.core.models import BranchStatus, RepoConfig, RepoStatus, StatusKind

GIT = shutil.which("git") or "/usr/bin/git"


@dataclass
class FetchResult:
    ok: bool
    error: str | None = None


def _run_git(
    repo_path: str, args: list[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GIT, "-C", repo_path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def detect_default_branch(repo_path: str, timeout: int = 10) -> str | None:
    """Resolve origin's default branch via `ls-remote --symref origin HEAD`."""
    try:
        result = _run_git(repo_path, ["ls-remote", "--symref", "origin", "HEAD"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("ref:") and line.endswith("HEAD"):
            ref = line.split()[1]
            if ref.startswith("refs/heads/"):
                return ref[len("refs/heads/") :]
            return ref
    return None


def fetch(repo_path: str, base: str, timeout: int = 15) -> FetchResult:
    """`git fetch origin <base> --quiet`. Never raises on nonzero exit."""
    try:
        result = _run_git(repo_path, ["fetch", "origin", base, "--quiet"], timeout)
    except subprocess.TimeoutExpired:
        return FetchResult(ok=False, error=f"fetch timed out after {timeout}s")
    except OSError as exc:
        return FetchResult(ok=False, error=str(exc))
    if result.returncode != 0:
        return FetchResult(ok=False, error=result.stderr.strip() or "fetch failed")
    return FetchResult(ok=True)


def current_branch(repo_path: str, timeout: int = 10) -> str | None:
    """`git rev-parse --abbrev-ref HEAD`."""
    try:
        result = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"], timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def behind_ahead(
    repo_path: str, left_ref: str, right_ref: str, timeout: int = 10
) -> tuple[int, int]:
    """(behind, ahead) of left_ref relative to right_ref via rev-list --left-right --count."""
    result = _run_git(
        repo_path,
        ["rev-list", "--left-right", "--count", f"{left_ref}...{right_ref}"],
        timeout,
    )
    if result.returncode != 0:
        return (0, 0)
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return (0, 0)
    ahead_str, behind_str = parts
    ahead = int(ahead_str) if ahead_str.isdigit() else 0
    behind = int(behind_str) if behind_str.isdigit() else 0
    return (behind, ahead)


def check_repo(repo: RepoConfig) -> RepoStatus:
    """Skeleton version: first base branch only, behind-count only. Never raises."""
    name = os.path.basename(repo.repo_path.rstrip("/"))

    if not os.path.isdir(os.path.join(repo.repo_path, ".git")):
        return RepoStatus(
            repo_path=repo.repo_path,
            name=name,
            current_branch=None,
            unpushed=0,
            branch_statuses=[],
            failure_reason="repo folder not found — was it moved or deleted?",
        )

    if not repo.base_branches:
        return RepoStatus(
            repo_path=repo.repo_path,
            name=name,
            current_branch=None,
            unpushed=0,
            branch_statuses=[],
            failure_reason="no base branches configured",
        )

    base = repo.base_branches[0]

    try:
        fetch_result = fetch(repo.repo_path, base)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return RepoStatus(
            repo_path=repo.repo_path,
            name=name,
            current_branch=None,
            unpushed=0,
            branch_statuses=[],
            failure_reason=str(exc),
        )

    if not fetch_result.ok:
        return RepoStatus(
            repo_path=repo.repo_path,
            name=name,
            current_branch=None,
            unpushed=0,
            branch_statuses=[],
            failure_reason="fetch failed — check network/SSH access",
        )

    branch = current_branch(repo.repo_path)

    try:
        behind, _ahead = behind_ahead(repo.repo_path, "HEAD", f"origin/{base}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return RepoStatus(
            repo_path=repo.repo_path,
            name=name,
            current_branch=branch,
            unpushed=0,
            branch_statuses=[],
            failure_reason=str(exc),
        )

    kind = StatusKind.UP_TO_DATE if behind == 0 else StatusKind.BEHIND
    branch_status = BranchStatus(base=base, behind=behind, ahead_of_base=0, kind=kind)

    return RepoStatus(
        repo_path=repo.repo_path,
        name=name,
        current_branch=branch,
        unpushed=0,
        branch_statuses=[branch_status],
        failure_reason=None,
    )
