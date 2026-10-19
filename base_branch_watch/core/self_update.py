"""Is the app's own source checkout behind its origin's default branch?

Pure plumbing, no rumps/AppKit (same split as core.git_ops). Only meaningful
for the editable/source install INSTALL.md describes: the package then lives
inside a git checkout, so "an update" is just new commits on origin.
Everything here never raises -- a failed check is "nothing to report".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from base_branch_watch.core import git_ops

# Check once at launch, then at most once per day while the app keeps running.
UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60

# Commit subjects shown in the update alert; `behind` still counts them all.
MAX_SUBJECTS = 5

_FALLBACK_BASE = "main"


@dataclass
class UpdateInfo:
    base: str
    behind: int
    remote_sha: str
    subjects: list[str]


def checkout_root(module_file: str | Path = __file__) -> str | None:
    """Root of the git checkout this package runs from, or None when it is
    not a source checkout (e.g. a non-editable `pip install .`)."""
    root = Path(module_file).resolve().parents[2]
    return str(root) if (root / ".git").exists() else None


def check_for_update(repo_root: str) -> UpdateInfo | None:
    """Fetch origin's default branch and report how far the checkout is
    behind it. None when up to date, when the checkout is not sitting on the
    default branch (a dev checkout on a feature branch), or on any failure."""
    base = git_ops.detect_default_branch(repo_root) or _FALLBACK_BASE
    if git_ops.current_branch(repo_root) != base:
        return None
    if not git_ops.fetch_with_retry(repo_root, base).ok:
        return None

    origin_ref = f"origin/{base}"
    remote_sha = git_ops.resolve_ref(repo_root, origin_ref)
    behind, _ahead = git_ops.behind_ahead(repo_root, "HEAD", origin_ref)
    if remote_sha is None or behind <= 0:
        return None

    mb = git_ops.merge_base(repo_root, "HEAD", origin_ref)
    commits = git_ops.incoming_commits(repo_root, mb, origin_ref) if mb else []
    return UpdateInfo(
        base=base,
        # Non-merge count, so it matches the listed subjects ("...and N more");
        # falls back to the raw count if the commit list could not be read.
        behind=len(commits) or behind,
        remote_sha=remote_sha,
        subjects=[commit.subject for commit in commits[:MAX_SUBJECTS]],
    )
