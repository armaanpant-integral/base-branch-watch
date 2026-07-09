"""Pure statuses -> list[MenuItemSpec] builder + title_for(). No rumps import.

Skeleton scope (01-UI-SPEC.md): single-base-branch flat rows only for
UP_TO_DATE / BEHIND / CHECK_FAILED / NOT_CHECKED. Multi-base submenus,
UNPUSHED/DIVERGED/CONFLICT_RISK tiers land in Plan 03.
"""

from __future__ import annotations

from base_branch_watch.core.models import MenuItemSpec, RepoStatus, Severity, StatusKind

EMPTY_STATE_TITLE = "No repos watched — click Add Repo… below"

_SEVERITY_GLYPH = {
    Severity.OK: "🟢",
    Severity.ATTENTION: "🟡",
    Severity.BLOCKING: "🔴",
}


def _row_for(status: RepoStatus) -> MenuItemSpec:
    kind = status.worst_kind

    if kind == StatusKind.NOT_CHECKED:
        return MenuItemSpec(title=f"… {status.name}", callback_key=None)

    if kind == StatusKind.CHECK_FAILED:
        return MenuItemSpec(
            title=f"🔴 {status.name}: check failed — {status.failure_reason}",
            callback_key=status.repo_path,
        )

    if kind == StatusKind.UP_TO_DATE:
        return MenuItemSpec(title=f"🟢 {status.name}", callback_key=status.repo_path)

    if kind == StatusKind.BEHIND:
        bs = status.branch_statuses[0]
        return MenuItemSpec(
            title=f"🟡 {status.name}: {bs.behind} behind ({bs.base})",
            callback_key=status.repo_path,
        )

    # Reserved tiers (UNPUSHED, BEHIND_AND_UNPUSHED, DIVERGED, CONFLICT_RISK) —
    # not produced in the skeleton, but rendered defensively so a future
    # core.git_ops enrichment doesn't crash the menu before menu_builder catches up.
    glyph = _SEVERITY_GLYPH[status.severity]
    return MenuItemSpec(title=f"{glyph} {status.name}", callback_key=status.repo_path)


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
