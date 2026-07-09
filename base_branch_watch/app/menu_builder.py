"""Pure statuses -> list[MenuItemSpec] builder + title_for(). No rumps import.

Full three-tier vocabulary (01-UI-SPEC.md): behind / unpushed / behind+unpushed /
diverged / check-failed / not-checked, rendered as a flat clickable row for a
single configured base branch, or a submenu (parent + one child per base) once
a repo has two or more base branches. `unpushed` is a repo-level axis (computed
once by core.git_ops, never per base) and is never duplicated into child rows.
"""

from __future__ import annotations

from base_branch_watch.core.models import (
    BranchStatus,
    MenuItemSpec,
    RepoStatus,
    Severity,
    StatusKind,
)

EMPTY_STATE_TITLE = "No repos watched — click Add Repo… below"

_SEVERITY_GLYPH = {
    Severity.OK: "🟢",
    Severity.ATTENTION: "🟡",
    Severity.BLOCKING: "🔴",
}


def _check_failed_reason(status: RepoStatus, bs: BranchStatus) -> str:
    """Repo-level failure_reason (missing dir, no bases configured) takes
    precedence; otherwise fall back to this specific base's own reason
    (per-base fetch failure)."""
    return status.failure_reason or bs.reason or "unknown error"


def _single_base_row(status: RepoStatus) -> MenuItemSpec:
    bs = status.branch_statuses[0]
    unpushed = status.unpushed

    if bs.kind == StatusKind.CHECK_FAILED:
        reason = _check_failed_reason(status, bs)
        return MenuItemSpec(
            title=f"🔴 {status.name}: check failed — {reason}",
            callback_key=status.repo_path,
        )

    if bs.kind == StatusKind.DIVERGED:
        return MenuItemSpec(
            title=(
                f"🔴 {status.name}: diverged — {bs.behind} behind, "
                f"{bs.ahead_of_base} ahead ({bs.base})"
            ),
            callback_key=status.repo_path,
        )

    if bs.kind == StatusKind.BEHIND:
        if unpushed > 0:
            title = f"🟡 {status.name}: {bs.behind} behind ({bs.base}) · {unpushed} unpushed"
        else:
            title = f"🟡 {status.name}: {bs.behind} behind ({bs.base})"
        return MenuItemSpec(title=title, callback_key=status.repo_path)

    # UP_TO_DATE at the branch level - unpushed alone still needs attention.
    if unpushed > 0:
        return MenuItemSpec(
            title=f"🟡 {status.name}: {unpushed} unpushed", callback_key=status.repo_path
        )
    return MenuItemSpec(title=f"🟢 {status.name}", callback_key=status.repo_path)


def _child_row(status: RepoStatus, bs: BranchStatus) -> MenuItemSpec:
    """Same vocabulary as the single-base table, minus repo name and unpushed
    (unpushed is a repo-level axis, never duplicated per base)."""
    if bs.kind == StatusKind.CHECK_FAILED:
        reason = bs.reason or "unknown error"
        title = f"🔴 {bs.base}: check failed — {reason}"
    elif bs.kind == StatusKind.DIVERGED:
        title = f"🔴 {bs.base}: diverged — {bs.behind} behind, {bs.ahead_of_base} ahead"
    elif bs.kind == StatusKind.BEHIND:
        title = f"🟡 {bs.base}: {bs.behind} behind"
    else:
        title = f"🟢 {bs.base}"
    return MenuItemSpec(title=title, callback_key=f"{status.repo_path}::{bs.base}")


def _multi_base_row(status: RepoStatus) -> MenuItemSpec:
    glyph = _SEVERITY_GLYPH[status.severity]
    title = f"{glyph} {status.name} ({len(status.branch_statuses)} base branches)"
    if status.unpushed > 0:
        title += f" · {status.unpushed} unpushed"
    children = [_child_row(status, bs) for bs in status.branch_statuses]
    # Parent has NO callback - a native NSMenu submenu parent only expands;
    # attaching a callback would conflict with that.
    return MenuItemSpec(title=title, callback_key=None, children=children)


def _row_for(status: RepoStatus) -> MenuItemSpec:
    if not status.branch_statuses:
        if status.failure_reason is not None:
            return MenuItemSpec(
                title=f"🔴 {status.name}: check failed — {status.failure_reason}",
                callback_key=status.repo_path,
            )
        return MenuItemSpec(title=f"… {status.name}", callback_key=None)

    if len(status.branch_statuses) == 1:
        return _single_base_row(status)

    return _multi_base_row(status)


def build(statuses: list[RepoStatus], has_repos: bool) -> list[MenuItemSpec]:
    if not has_repos:
        return [MenuItemSpec(title=EMPTY_STATE_TITLE, callback_key=None)]
    return [_row_for(status) for status in statuses]


def title_for(statuses: list[RepoStatus]) -> str:
    if not statuses:
        return _SEVERITY_GLYPH[Severity.OK]

    worst = max((s.severity for s in statuses), default=Severity.OK)
    count = sum(1 for s in statuses if s.severity >= worst and worst != Severity.OK)

    if worst == Severity.OK:
        return _SEVERITY_GLYPH[Severity.OK]
    return f"{_SEVERITY_GLYPH[worst]} {count}"
