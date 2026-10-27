"""The one place a RepoStatus / BranchStatus becomes words. No rumps/AppKit import, ever.

Menu rows, the digest notification, the poll log and click alerts all call these, so a
new StatusKind is described once. Callers add their own prefix (glyph, repo name,
`[OK]`/`[FAIL]`); this module owns only the phrase.
"""

from __future__ import annotations

from base_branch_watch.core.models import BranchStatus, RepoStatus, StatusKind


def base_summary(bs: BranchStatus, *, show_base: bool = True) -> str:
    """Phrase for one base branch; `show_base=False` for rows already labelled with the base."""
    base = f" ({bs.base})" if show_base else ""
    if bs.kind == StatusKind.CHECK_FAILED:
        return f"check failed - {bs.reason or 'unknown error'}"
    if bs.kind == StatusKind.DIVERGED:
        return f"diverged - {bs.behind} behind, {bs.ahead_of_base} ahead{base}"
    if bs.kind == StatusKind.CONFLICT_RISK:
        return f"conflict risk - {len(bs.conflict_paths)} file(s) overlap{base}"
    if bs.kind == StatusKind.BEHIND:
        return f"{bs.behind} behind{base}"
    return "up to date"


def repo_summary(status: RepoStatus) -> str:
    """Phrase for a repo: its worst base branch, plus the repo-level unpushed count."""
    if status.failure_reason is not None:
        return f"check failed - {status.failure_reason}"
    bs = status.worst_branch_status
    if bs is None:
        return "not checked yet"
    summary = base_summary(bs)
    if status.unpushed == 0:
        return summary
    # Unpushed commits only matter alongside a clean or merely-behind base.
    if bs.kind == StatusKind.UP_TO_DATE:
        return f"{status.unpushed} unpushed"
    if bs.kind == StatusKind.BEHIND:
        return f"{summary} · {status.unpushed} unpushed"
    return summary
